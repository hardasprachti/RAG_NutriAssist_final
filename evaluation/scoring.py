"""Scoring rules for the evaluation: pure functions, no network, no database.

These checks are written independently of ``backend/core/response_validator.py`` on purpose. The validator is the
system under test; if the evaluation reused its code, a blind spot in the validator would be a blind spot in the
audit too. The only things shared with the backend are the *names* of failure categories, so evaluation findings
land in the same ``failure_logs`` vocabulary.

Ground truth comes from two places the model cannot influence: the chunk files ingestion wrote
(``ingestion/data/chunks/*.jsonl``) and facts the question author read in the source documents and wrote into the
question file (``expected_numbers_*``, ``expected_terms_*``).
"""

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

# ── text normalisation ───────────────────────────────────────────────────────
_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-", " ": " ", "’": "'", "‘": "'"})
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_WORD = re.compile(r"[a-z]{4,}")
_NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                 "eight": "8", "nine": "9", "ten": "10", "twelve": "12", "fifteen": "15", "twenty": "20",
                 "thirty": "30"}
_STOP = frozenset(
    "that with from this have which their there about should would could other these those than then also such "
    "more most each into over only when where what while according recommend recommends recommended states state "
    "says said guidance guideline guidelines document people general generally every".split()
)


def norm(text: str) -> str:
    """Lower-case, dashes and spaces unified: how expected strings are matched against chunk text."""
    return re.sub(r"\s+", " ", text.translate(_DASHES).lower()).strip()


def numbers(text: str) -> set[str]:
    """Numbers in ``text`` as canonical strings ("2,300" -> "2300", "3.0" -> "3"), ranges as two numbers."""
    found: set[str] = set()
    for token in _NUMBER.findall(text.translate(_DASHES)):
        try:
            found.add(format(Decimal(token.replace(",", "")).normalize(), "f"))
        except InvalidOperation:
            continue
    return found


def chunk_numbers(text: str) -> set[str]:
    """What a chunk can support: its digits plus spelled-out numbers ("five portions" supports 5)."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    return numbers(text) | {digit for word, digit in _NUMBER_WORDS.items() if word in words}


def stems(text: str) -> set[str]:
    return {w[:5] for w in _WORD.findall(text.lower()) if w not in _STOP}


def support_ratio(claim: str, chunk_text: str, section: str = "") -> float:
    """Share of the claim's content-word stems that occur in the chunk (and its heading)."""
    claim_stems = stems(claim)
    if not claim_stems:
        return 1.0
    return len(claim_stems & stems(f"{chunk_text} {section}")) / len(claim_stems)


# ── ground truth ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class GroundChunk:
    chunk_id: str
    document_name: str
    publisher: str
    year: int
    source_url: str
    section: str
    text: str


class Corpus:
    """The chunks and registry as ingestion wrote them to disk."""

    def __init__(self, chunks: Mapping[str, GroundChunk], registry: Mapping[str, Mapping[str, Any]]) -> None:
        self.chunks = dict(chunks)
        self.registry = dict(registry)  # by document_name

    @classmethod
    def load(cls, chunks_dir: Path, registry_path: Path) -> "Corpus":
        chunks: dict[str, GroundChunk] = {}
        for path in sorted(chunks_dir.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                c = json.loads(line)
                chunks[c["chunk_id"]] = GroundChunk(
                    c["chunk_id"], c["document_name"], c["publisher"], int(c["year"]), c["source_url"],
                    c.get("section", ""), c["text"])
        registry = {d["document_name"]: d for d in json.loads(registry_path.read_text(encoding="utf-8"))["documents"]}
        return cls(chunks, registry)

    def chunk_for(self, chunk_id: str, api_sources: Sequence[Mapping[str, Any]] = ()) -> Optional[GroundChunk]:
        """The local chunk file wins; against a deployment whose corpus was ingested elsewhere, fall back to the
        excerpt the API itself returned for that chunk."""
        if chunk_id in self.chunks:
            return self.chunks[chunk_id]
        for s in api_sources:
            if s.get("chunk_id") == chunk_id:
                return GroundChunk(chunk_id, s["document_name"], s["publisher"], int(s["year"]), s["url"],
                                   s.get("section", ""), s.get("text", ""))
        return None


# ── findings ─────────────────────────────────────────────────────────────────
# Evaluation categories. Those that exist in failure_logs use the same string; the rest are evaluation-only
# judgements (they describe the *answer*, not a pipeline event) and are reported in the failure table only.
FAILURE_LOG_CATEGORIES = frozenset({
    "schema_validation_failure", "invalid_citation", "fabricated_source", "missing_refusal",
    "not_in_corpus_answered", "inconsistent_numerical_claim", "empty_claims", "vague_response", "unsupported_claim",
    "missing_citation", "incorrect_retrieval", "conflicting_guidance_error", "llm_error",
})


@dataclass(frozen=True)
class Finding:
    category: str
    detail: str
    fatal: bool = True  # False: a flag for the human reviewer, not counted as a failure

    def as_dict(self) -> dict[str, Any]:
        return {"category": self.category, "detail": self.detail, "fatal": self.fatal}


@dataclass
class Scored:
    findings: list[Finding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.fatal]

    @property
    def flags(self) -> list[Finding]:
        return [f for f in self.findings if not f.fatal]

    @property
    def passed(self) -> bool:
        return not self.failures


# ── retrieval ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Target:
    document: str
    section_match: tuple[str, ...]


def targets_of(question: Mapping[str, Any]) -> list[Target]:
    """The (document, section) pairs a retrieval question expects; cross-document questions expect several."""
    primary = Target(question["expected_document"],
                     tuple(question.get("section_match") or [question["expected_section"]]))
    extra = [Target(t["expected_document"], tuple(t["section_match"])) for t in question.get("also_expected", [])]
    return [primary, *extra]


def chunk_matches(target: Target, document_name: str, section: str) -> bool:
    if document_name != target.document:
        return False
    return any(norm(m) in norm(section) for m in target.section_match)


def first_rank(target: Target, hits: Sequence[Mapping[str, Any]]) -> Optional[int]:
    """1-based rank of the first retrieved chunk that is in the expected document *and* section, else None."""
    for rank, hit in enumerate(hits, start=1):
        if chunk_matches(target, hit["document_name"], hit["section"]):
            return rank
    return None


def evidence_found(question: Mapping[str, Any], hits: Sequence[Mapping[str, Any]]) -> Optional[bool]:
    """Stricter than the section check: do all ``evidence`` strings occur in retrieved text of the expected
    document? None when the question lists no evidence."""
    evidence = question.get("evidence")
    if not evidence:
        return None
    wanted = {t.document for t in targets_of(question)}
    text = norm(" ".join(h["text"] for h in hits if h["document_name"] in wanted))
    return all(norm(e) in text for e in evidence)


def classify_retrieval_miss(question: Mapping[str, Any], hits: Sequence[Mapping[str, Any]],
                            top_score: Optional[float], min_score: float) -> Optional[str]:
    """Why a question missed: ``gate`` (nothing cleared the similarity gate), ``wrong_document`` (the expected
    document is absent from the results), ``wrong_section`` (document present, section not). None on a hit."""
    ts = targets_of(question)
    if all(first_rank(t, hits) for t in ts):
        return None
    if not hits:
        return "gate" if (top_score is None or top_score < min_score) else "empty"
    missing = [t for t in ts if not first_rank(t, hits)]
    return "wrong_section" if all(any(h["document_name"] == t.document for h in hits) for t in missing) else "wrong_document"


# ── answers ──────────────────────────────────────────────────────────────────
_REFERRAL = re.compile(r"(?i)\b(dietitian|dietician|nutritionist|healthcare professional|health care professional|doctor|physician|"
                       r"health professional|healthcare provider|medical professional)\b")
_DOC_NAMES = {
    "Healthy Diet Fact Sheet": (r"\bWHO\b", r"(?i)world health organi[sz]ation"),
    "Dietary Guidelines for Americans, 2020-2025": (r"\bUSDA\b", r"(?i)dietary guidelines for americans", r"(?i)department of agriculture"),
    "Refrigerator & Freezer Storage Chart": (r"\bFDA\b", r"(?i)food and drug administration"),
    "The Eatwell Guide": (r"(?i)eatwell", r"(?i)food standards agency", r"\bFSA\b"),
    "Summary of Dietary Reference Values": (r"\bEFSA\b", r"(?i)european food safety"),
    "Dietary Guidelines for Indians": (r"\bICMR\b", r"\bNIN\b", r"(?i)national institute of nutrition", r"(?i)indian council"),
}


def documents_named(text: str) -> set[str]:
    return {name for name, patterns in _DOC_NAMES.items() if any(re.search(p, text) for p in patterns)}


def score_citations(response: Mapping[str, Any], corpus: Corpus) -> Scored:
    """Audit every claim of an ``answered`` response against ground truth (not against the backend's validator)."""
    scored = Scored()
    claims = response.get("claims") or []
    api_sources = response.get("retrieved_sources") or []
    shown_ids = {s["chunk_id"] for s in api_sources}
    checked = supported = 0
    for i, claim in enumerate(claims, start=1):
        src = claim.get("source") or {}
        cid = src.get("chunk_id", "")
        truth = corpus.chunk_for(cid, api_sources)
        tag = f"claim {i}"
        if truth is None:
            scored.findings.append(Finding("fabricated_source", f"{tag}: chunk_id {cid!r} does not exist in the corpus"))
            continue
        checked += 1
        if shown_ids and cid not in shown_ids:
            scored.findings.append(Finding("invalid_citation", f"{tag}: {cid} was not among the retrieved sources"))
        drift = [f for f, got, want in (("document", src.get("document_name"), truth.document_name),
                                         ("publisher", src.get("publisher"), truth.publisher),
                                         ("year", src.get("year"), truth.year),
                                         ("url", src.get("url"), truth.source_url)) if got != want]
        if drift:
            scored.findings.append(Finding("fabricated_source", f"{tag}: {', '.join(drift)} differ from the chunk {cid}"))
        reg = corpus.registry.get(truth.document_name)
        if reg and src.get("url") not in {reg["source_url"]}:
            scored.findings.append(Finding("fabricated_source", f"{tag}: url {src.get('url')!r} is not the registered source"))
        text = claim.get("claim_text", "")
        allowed = chunk_numbers(truth.text) | {str(truth.year)} | numbers(truth.document_name)
        missing = sorted(numbers(text) - allowed, key=float)
        if missing:
            scored.findings.append(Finding("unsupported_claim", f"{tag}: number(s) {', '.join(missing)} not in cited chunk {cid}"))
        else:
            supported += 1
        ratio = support_ratio(text, truth.text, truth.section)
        if ratio < 0.4:
            scored.findings.append(Finding("unsupported_claim", f"{tag}: weak wording support ({ratio:.0%}) from {cid}; review by hand",
                                           fatal=False))
        other = documents_named(text) - {truth.document_name}
        if other:
            scored.findings.append(Finding("conflicting_guidance_error",
                                           f"{tag}: names {', '.join(sorted(other))} but cites only {truth.document_name}"))
        if len(text.split()) < 3:
            scored.findings.append(Finding("vague_response", f"{tag}: too short to state anything", fatal=False))
    in_claims = set().union(*(numbers(c.get("claim_text", "")) for c in claims)) if claims else set()
    years = {str(c.get("source", {}).get("year")) for c in claims}
    stray = sorted(numbers(response.get("answer", "")) - in_claims - years, key=float)
    if stray:
        scored.findings.append(Finding("missing_citation", f"the answer states number(s) {', '.join(stray)} that no claim supports"))
    if len(response.get("answer", "").split()) < 5:
        scored.findings.append(Finding("vague_response", "the answer is too short to be useful", fatal=False))
    scored.metrics.update(claims=len(claims), claims_checked=checked, claims_number_supported=supported)
    return scored


def score_benchmark(case: Mapping[str, Any], response: Mapping[str, Any], corpus: Corpus) -> Scored:
    """Per-response scoring for the benchmark (Problem Statement §9): status, retrieval, citations, figures."""
    status = response.get("status")
    expected = case["expected_status"]
    scored = Scored()
    scored.metrics["status"] = status

    if status not in expected:
        scored.findings.append(_status_finding(status, expected))
    claims = response.get("claims") or []
    sources = response.get("retrieved_sources") or []

    if status == "answered":
        audit = score_citations(response, corpus)
        scored.findings.extend(audit.findings)
        scored.metrics.update(audit.metrics)
        cited_docs = {c["source"]["document_name"] for c in claims}
        retrieved_docs = {s["document_name"] for s in sources}
        scored.metrics["cited_documents"] = sorted(cited_docs)
        scored.metrics["retrieved_documents"] = sorted(retrieved_docs)

        want_all = case.get("expected_documents_all", [])
        want_any = case.get("expected_documents_any", [])
        if want_all and (gap := [d for d in want_all if d not in retrieved_docs]):
            scored.findings.append(Finding("incorrect_retrieval", f"expected evidence from {', '.join(gap)} was not retrieved"))
        if want_any and not (retrieved_docs & set(want_any)):
            scored.findings.append(Finding("incorrect_retrieval", f"none of {', '.join(want_any)} was retrieved"))
        # Retrieved but not used is a content gap, not a retrieval gap.
        if want_all and (gap := [d for d in want_all if d in retrieved_docs and d not in cited_docs]):
            scored.findings.append(Finding("missing_citation", f"retrieved but not cited: {', '.join(gap)}", fatal=False))
        if len(cited_docs) < case.get("min_documents", 0):
            scored.findings.append(Finding("incorrect_retrieval" if len(retrieved_docs) < case["min_documents"] else "missing_citation",
                                           f"{len(cited_docs)} document(s) cited, expected at least {case['min_documents']}"))

        claim_numbers = set().union(*(numbers(c["claim_text"]) for c in claims)) if claims else set()
        if need := [n for n in case.get("expected_numbers_all", []) if n not in claim_numbers]:
            scored.findings.append(Finding("unsupported_claim", f"expected figure(s) {', '.join(need)} absent from the claims "
                                                              f"(wrong or missing number); claims give {sorted(claim_numbers, key=float)}"))
        if (any_n := case.get("expected_numbers_any")) and not (set(any_n) & claim_numbers):
            scored.findings.append(Finding("unsupported_claim", f"none of the expected figures {any_n} appear in the claims"))
        body = norm(response.get("answer", "") + " " + " ".join(c["claim_text"] for c in claims))
        if need := [t for t in case.get("expected_terms_all", []) if norm(t) not in body]:
            scored.findings.append(Finding("vague_response", f"expected content missing: {', '.join(need)}"))
        if (any_t := case.get("expected_terms_any")) and not any(norm(t) in body for t in any_t):
            scored.findings.append(Finding("vague_response", f"none of the expected terms {any_t} appear"))
        # Attribution across documents: two documents' figures must not be merged into one claim.
        scored.metrics["separate_claims_per_document"] = all(len(documents_named(c["claim_text"])) <= 1 for c in claims)
    elif status == "out_of_scope":
        if not _REFERRAL.search(response.get("answer", "")):
            scored.findings.append(Finding("vague_response", "a decline that does not point to a professional"))
        if claims or sources:
            scored.findings.append(Finding("missing_refusal", "a refusal that still carries claims or sources"))
    elif status == "not_in_corpus":
        names = documents_named(response.get("answer", ""))
        scored.metrics["documents_named_in_refusal"] = len(names)
        if not names and "searched" not in response.get("answer", "").lower():
            scored.findings.append(Finding("vague_response", "not_in_corpus answer does not say which documents were searched",
                                           fatal=False))
    return scored


def _status_finding(status: Optional[str], expected: Sequence[str]) -> Finding:
    want = "/".join(expected)
    if "out_of_scope" in expected and status != "out_of_scope":
        return Finding("missing_refusal", f"a restricted question got status {status!r} (expected {want})")
    if "answered" not in expected and status == "answered":
        return Finding("not_in_corpus_answered", f"answered a question the corpus should not cover (expected {want})")
    if status == "out_of_scope":
        return Finding("incorrect_refusal", f"a legitimate question was declined as out of scope (expected {want})")
    if status == "not_in_corpus":
        return Finding("unhelpful_uncertainty", f"said the corpus does not cover a question it should (expected {want})")
    if status == "error":
        return Finding("error_status", f"the system returned an error instead of an answer (expected {want})")
    return Finding("unexpected_status", f"status {status!r}, expected {want}")


# ── consistency ──────────────────────────────────────────────────────────────
@dataclass
class RunDigest:
    status: str
    numbers: frozenset[str]
    documents: frozenset[str]
    sections: frozenset[str]
    chunk_ids: frozenset[str]
    stems: frozenset[str]
    answer: str
    evidence_ok: bool


def digest(response: Mapping[str, Any], corpus: Corpus) -> RunDigest:
    claims = response.get("claims") or []
    api_sources = response.get("retrieved_sources") or []
    nums: set[str] = set()
    st: set[str] = set()
    evidence_ok = True
    for c in claims:
        nums |= numbers(c["claim_text"])
        st |= stems(c["claim_text"])
        truth = corpus.chunk_for(c["source"]["chunk_id"], api_sources)
        if truth is None or numbers(c["claim_text"]) - (chunk_numbers(truth.text) | {str(truth.year)} | numbers(truth.document_name)):
            evidence_ok = False
    return RunDigest(
        status=str(response.get("status")),
        numbers=frozenset(nums),
        documents=frozenset(c["source"]["document_name"] for c in claims),
        sections=frozenset(f'{c["source"]["document_name"]} > {c["source"]["section"]}' for c in claims),
        chunk_ids=frozenset(c["source"]["chunk_id"] for c in claims),
        stems=frozenset(st),
        answer=str(response.get("answer", "")),
        evidence_ok=evidence_ok,
    )


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    a, b = set(a), set(b)
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def compare_runs(runs: Sequence[RunDigest], similarity_floor: float = 0.5) -> dict[str, Any]:
    """The five dimensions of the Problem Statement §10 matrix, computed mechanically. Recommendation stability
    is a lexical proxy (claim wording overlap between runs); anything under the floor is flagged for a human."""
    first = runs[0]
    all_numbers = set().union(*(r.numbers for r in runs))
    common_numbers = set.intersection(*(set(r.numbers) for r in runs))
    pairs = [(i, j) for i in range(len(runs)) for j in range(i + 1, len(runs))]
    similarity = min((jaccard(runs[i].stems, runs[j].stems) for i, j in pairs), default=1.0)
    return {
        "refusal_consistency": len({r.status for r in runs}) == 1,
        "numerical_consistency": len({r.numbers for r in runs}) == 1,
        "numbers_differing": sorted(all_numbers - common_numbers, key=float),
        "citation_consistency_documents": len({r.documents for r in runs}) == 1,
        "citation_consistency_sections": len({r.sections for r in runs}) == 1,
        "same_chunks": len({r.chunk_ids for r in runs}) == 1,
        "evidence_agreement": all(r.evidence_ok for r in runs),
        "min_pairwise_wording_similarity": round(similarity, 3),
        "recommendation_stability": "stable" if similarity >= similarity_floor and len({r.status for r in runs}) == 1 else "review",
        "status": first.status if len({r.status for r in runs}) == 1 else sorted({r.status for r in runs}),
    }

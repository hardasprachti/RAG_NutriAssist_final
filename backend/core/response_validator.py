"""Response validation (Architecture §7, §11, §13; Problem Statement §7-§8).

The model's output is untrusted. After Pydantic has parsed it, this module checks it against what was
actually retrieved:

* **Citations** - every ``chunk_id`` must be one the model was shown; document, publisher, year and URL must
  belong to the eight-document corpus (URL whitelist). The *chunk's* metadata is the truth: the model's copy is
  overwritten, never trusted.
* **Numbers** - every number in a claim must appear in the chunk it cites (no invented, converted or
  "rounded" figures); every number in the summary ``answer`` must appear in a claim.
* **Attribution** - a claim may not name another corpus document than the one it cites (sources merged into
  one statement).
* **Support** - a lexical check that the claim's content words occur in the cited chunk. Cheap and imperfect:
  near-zero overlap blocks the response, weak overlap is flagged for analysis.

Violations are *blocking* (the response is unusable) or *advisory* (recorded, response still returned).
Nothing here calls an LLM or the network.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Sequence

from core.corpus import BY_NAME, CORPUS, URL_WHITELIST, CorpusDocument
from core.failure_logger import FailureCategory, Violation
from core.safety_validator import DECLINE_MESSAGE
from models.schemas import Claim, NutritionResponse, ResponseStatus, SourceReference

_NUMBER = re.compile(r"(?<![\w.,])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![0-9])")
_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "fifteen": "15", "twenty": "20", "thirty": "30",
}
_WORD = re.compile(r"[a-z]{4,}")

# A number followed by a unit: "15 mcg", "1,000 IU", "2 days", "30%". Only units that change what a figure means
# are listed; spellings that mean the same thing share a canonical form ("µg" == "mcg", "grams" == "g").
_UNIT_ALIASES = {
    "mcg": "mcg", "ug": "mcg", "µg": "mcg", "μg": "mcg", "microgram": "mcg", "micrograms": "mcg",
    "mg": "mg", "milligram": "mg", "milligrams": "mg",
    "g": "g", "gram": "g", "grams": "g", "gm": "g",
    "kg": "kg", "kilogram": "kg", "kilograms": "kg", "kgs": "kg",
    "iu": "iu",
    "ml": "ml", "millilitre": "ml", "milliliter": "ml", "millilitres": "ml", "milliliters": "ml",
    "l": "l", "litre": "l", "liter": "l", "litres": "l", "liters": "l",
    "kcal": "kcal", "kcals": "kcal", "calorie": "kcal", "calories": "kcal",
    "kj": "kj",
    "%": "%", "percent": "%",
    "minute": "minute", "minutes": "minute", "min": "minute", "mins": "minute",
    "hour": "hour", "hours": "hour", "hr": "hour", "hrs": "hour",
    "day": "day", "days": "day",
    "week": "week", "weeks": "week",
    "month": "month", "months": "month",
    "year": "year", "years": "year",
    "°c": "c", "°f": "f",
}
_UNIT_AFTER_NUMBER = re.compile(
    r"^\s?(" + "|".join(sorted((re.escape(u) for u in _UNIT_ALIASES), key=len, reverse=True)) + r")(?![a-zµμ°])",
    re.IGNORECASE,
)
# gpt-oss tends to append its own citation markers ("【efsa_drv_summary_chunk_026】" or "[chunk_id: ...]") to the text.
# The citation lives in the structured ``source``; the marker is noise in what the user reads.
_MARKER = re.compile(r"\s*" + chr(0x3010) + "[^" + chr(0x3011) + "]*" + chr(0x3011)
                     + r"|\s*\[(?:chunk[_ ]?id:?\s*)?[a-z0-9_]+_chunk_\d+\]")
_STOPWORDS = frozenset(
    "that with from this have which their there about should would could other these those than then also such "
    "more most each into over only when where what while according recommend recommends recommended states "
    "state says said guidance guideline guidelines document people general generally per".split()
)

BLOCK_BELOW_SUPPORT = 0.15  # share of claim content words found in the cited chunk
FLAG_BELOW_SUPPORT = 0.40
MIN_ANSWER_WORDS = 5
REPOINT_MIN_SUPPORT = 0.30  # table chunks hold few words, so the bar is low; the tie margin does the discriminating
REPOINT_TIE_MARGIN = 0.05
MIN_CLAIM_WORDS = 3


def strip_markers(text: str) -> str:
    return re.sub(r"\s{2,}", " ", _MARKER.sub("", text)).strip()


def numbers_in(text: str) -> set[str]:
    """Normalised numbers in ``text`` ("1,100" == "1100", "3.0" == "3"; "B12" is not a number)."""
    found = set()
    for token in _NUMBER.findall(text):
        try:
            found.add(format(Decimal(token.replace(",", "")).normalize(), "f"))
        except InvalidOperation:  # pragma: no cover - the regex only yields valid numbers
            continue
    return found


def number_units(text: str) -> dict[str, set[str]]:
    """Each number in ``text`` with the units written right after it ("" when it has none), e.g.
    ``"15 mcg or 600 IU, 2 days"`` -> ``{"15": {"mcg"}, "600": {"iu"}, "2": {"day"}}``."""
    found: dict[str, set[str]] = {}
    for match in _NUMBER.finditer(text):
        try:
            number = format(Decimal(match.group().replace(",", "")).normalize(), "f")
        except InvalidOperation:  # pragma: no cover - the regex only yields valid numbers
            continue
        unit = _UNIT_AFTER_NUMBER.match(text[match.end():])
        found.setdefault(number, set()).add(_UNIT_ALIASES[unit.group(1).lower()] if unit else "")
    return found


def unit_mismatches(claim_text: str, chunk_text: str) -> list[str]:
    """Figures a claim gives in one unit that the chunk only gives in another ("15 mg" vs the chunk's "15 mcg").

    Judged conservatively: a number the chunk also writes without a unit (a table whose unit sits in the column
    header) is never flagged, and neither is a number the chunk does not contain (the digit check reports that).
    """
    in_chunk = number_units(chunk_text)
    problems = []
    for number, units in number_units(claim_text).items():
        for unit in units - {""}:
            available = in_chunk.get(number)
            if available and "" not in available and unit not in available:
                problems.append(f"{number} {unit} (the chunk gives {number} only as {', '.join(sorted(available))})")
    return sorted(problems)


def _chunk_numbers(text: str) -> set[str]:
    """Numbers a chunk supports: its digits, plus number words ("five portions" supports a claim's 5)."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    return numbers_in(text) | {digit for word, digit in _NUMBER_WORDS.items() if word in words}


def _stems(text: str, exclude: frozenset[str] = frozenset()) -> set[str]:
    return {w[:5] for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and w[:5] not in exclude}


def _org_stems() -> frozenset[str]:
    """Stems of document and publisher names: a claim may say "According to the Eatwell Guide" without the
    chunk repeating it."""
    return frozenset(s for d in CORPUS for s in _stems(f"{d.document_name} {d.publisher}"))


_ORG_STEMS = _org_stems()


def support_ratio(claim_text: str, chunk_text: str, section: str = "") -> float:
    claim = _stems(claim_text, _ORG_STEMS)
    if not claim:
        return 1.0
    return len(claim & _stems(f"{chunk_text} {section}")) / len(claim)


@dataclass
class ValidationResult:
    response: NutritionResponse  # normalised: sources overwritten from the chunks, searched documents named
    blocking: list[Violation] = field(default_factory=list)
    advisory: list[Violation] = field(default_factory=list)
    corrected_sources: int = 0

    @property
    def ok(self) -> bool:
        return not self.blocking

    @property
    def violations(self) -> list[Violation]:
        return [*self.blocking, *self.advisory]


class ResponseValidator:
    def __init__(
        self,
        url_whitelist: frozenset[str] = URL_WHITELIST,
        block_below_support: float = BLOCK_BELOW_SUPPORT,
        flag_below_support: float = FLAG_BELOW_SUPPORT,
    ) -> None:
        self.url_whitelist = url_whitelist
        self.block_below_support = block_below_support
        self.flag_below_support = flag_below_support

    def validate(
        self,
        response: NutritionResponse,
        retrieved: Sequence[Any],
        searched: Sequence[CorpusDocument] = CORPUS,
    ) -> ValidationResult:
        """Check ``response`` against the ``SearchHit``s the model was shown."""
        if response.status is ResponseStatus.error:
            return ValidationResult(
                response,
                blocking=[Violation(FailureCategory.schema_validation_failure,
                                    "the model returned status 'error', which is reserved for the system")],
            )
        if response.status is not ResponseStatus.answered:
            response = self._without_markers(response)
            return ValidationResult(self._with_referral(self._name_searched(response, searched)))

        response = self._without_markers(response)
        by_id = {h.chunk.chunk_id: h.chunk for h in retrieved}
        result = ValidationResult(response)
        claims: list[Claim] = []
        for i, claim in enumerate(response.claims):
            chunk = by_id.get(claim.source.chunk_id)
            self._check_source(result, i, claim, chunk)
            if chunk is None:
                claims.append(claim)
                continue
            problems = self._claim_problems(i, claim, chunk)
            if any(p.blocking and p.category is FailureCategory.unsupported_claim for p in problems):
                better = self._supporting_chunk(i, claim, chunk, retrieved)
                if better is not None:
                    result.advisory.append(Violation(
                        FailureCategory.invalid_citation,
                        f"claim cited {chunk.chunk_id} but its figures and wording are supported by "
                        f"{better.chunk_id}; citation re-pointed", i, blocking=False))
                    chunk, problems = better, self._claim_problems(i, claim, better)
            for problem in problems:
                (result.blocking if problem.blocking else result.advisory).append(problem)
            claims.append(claim.model_copy(update={"source": self._source_from(chunk, result, claim.source)}))
        self._check_answer(result, response, claims)
        result.response = response.model_copy(update={"claims": claims})
        return result

    @staticmethod
    def _without_markers(response: NutritionResponse) -> NutritionResponse:
        claims = [c.model_copy(update={"claim_text": strip_markers(c.claim_text) or c.claim_text}) for c in response.claims]
        return response.model_copy(update={"answer": strip_markers(response.answer) or response.answer, "claims": claims})

    # ── per-claim checks ─────────────────────────────────────────────────────
    def _check_source(self, result: ValidationResult, i: int, claim: Claim, chunk: Optional[Any]) -> None:
        src = claim.source
        if chunk is None:
            result.blocking.append(Violation(
                FailureCategory.invalid_citation,
                f"chunk_id {src.chunk_id!r} was not in the retrieved set", i))
        problems = []
        if src.url not in self.url_whitelist:
            problems.append(f"url {src.url!r} is not one of the corpus documents")
        if src.document_name not in BY_NAME:
            problems.append(f"document {src.document_name!r} is not in the corpus")
        elif BY_NAME[src.document_name].publisher != src.publisher:
            problems.append(f"publisher {src.publisher!r} does not match document {src.document_name!r}")
        if problems:
            result.blocking.append(Violation(FailureCategory.fabricated_source, "; ".join(problems), i))

    def _source_from(self, chunk: Any, result: ValidationResult, given: SourceReference) -> SourceReference:
        truth = SourceReference(
            document_name=chunk.document_name, publisher=chunk.publisher, year=chunk.year,
            section=chunk.section, url=chunk.source_url, chunk_id=chunk.chunk_id,
        )
        if truth != given:
            result.corrected_sources += 1
            drift = [f for f in ("document_name", "publisher", "year", "url")
                     if getattr(truth, f) != getattr(given, f)]
            if drift:  # a reworded section is routine; a different document or year is a mis-citation
                result.advisory.append(Violation(
                    FailureCategory.invalid_citation,
                    f"source {', '.join(drift)} differed from chunk {chunk.chunk_id}; replaced with the chunk's",
                    blocking=False))
        return truth

    def _claim_problems(self, i: int, claim: Claim, chunk: Any) -> list[Violation]:
        """Everything wrong with ``claim`` as a statement backed by ``chunk`` (blocking and advisory)."""
        found: list[Violation] = []
        claim_numbers = numbers_in(claim.claim_text) - {str(chunk.year)}
        unsupported = sorted(claim_numbers - _chunk_numbers(chunk.text), key=float)
        if unsupported:
            found.append(Violation(
                FailureCategory.unsupported_claim,
                f"number(s) {', '.join(unsupported)} do not appear in the cited chunk {chunk.chunk_id}", i))

        wrong_units = unit_mismatches(claim.claim_text, chunk.text)
        if wrong_units:
            found.append(Violation(
                FailureCategory.unsupported_claim,
                f"unit mismatch against the cited chunk {chunk.chunk_id}: {'; '.join(wrong_units)}", i))

        cited = BY_NAME.get(chunk.document_name)
        others = [d.document_name for d in CORPUS if d is not cited and d.named_in(claim.claim_text)]
        if others:
            found.append(Violation(
                FailureCategory.conflicting_guidance_error,
                f"claim names {', '.join(others)} but cites only {chunk.document_name}; "
                "each document needs its own claim", i))

        ratio = support_ratio(claim.claim_text, chunk.text, chunk.section)
        if ratio < self.block_below_support:
            found.append(Violation(
                FailureCategory.unsupported_claim,
                f"only {ratio:.0%} of the claim's content words occur in the cited chunk {chunk.chunk_id}", i))
        elif ratio < self.flag_below_support:
            found.append(Violation(
                FailureCategory.unsupported_claim,
                f"weak lexical support ({ratio:.0%}) from the cited chunk {chunk.chunk_id}", i, blocking=False))

        if len(claim.claim_text.split()) < MIN_CLAIM_WORDS:
            found.append(Violation(
                FailureCategory.vague_response, "claim is too short to state anything", i, blocking=False))
        return found

    def _supporting_chunk(self, i: int, claim: Claim, cited: Any, retrieved: Sequence[Any]) -> Optional[Any]:
        """Another retrieved chunk that fully supports a claim its cited chunk does not.

        Tables are split into row-group chunks that repeat the header, so a model easily cites the wrong
        sibling for a correct figure. The claim is re-pointed only when the other chunk passes every blocking
        check on its own (all numbers present, no other document named) AND its wording matches well. Anything
        ambiguous stays rejected: candidates from different sections that match about equally (say the male
        and the female table) are never guessed between. The re-pointing is recorded as an advisory finding.
        """
        scored = []
        for h in retrieved:  # best retrieval score first
            other = h.chunk
            if other.chunk_id == cited.chunk_id:
                continue
            if any(p.blocking for p in self._claim_problems(i, claim, other)):
                continue
            ratio = support_ratio(claim.claim_text, other.text, other.section)
            if ratio >= REPOINT_MIN_SUPPORT:
                scored.append((ratio, other))
        if not scored:
            return None
        best = max(r for r, _ in scored)
        close = [c for r, c in scored if r >= best - REPOINT_TIE_MARGIN]
        if len({c.section for c in close}) > 1:
            return None
        return close[0]

    # ── whole-response checks ────────────────────────────────────────────────
    def _check_answer(self, result: ValidationResult, response: NutritionResponse, claims: list[Claim]) -> None:
        in_claims = set().union(*(numbers_in(c.claim_text) for c in claims)) if claims else set()
        years = {str(c.source.year) for c in claims}
        stray = sorted(numbers_in(response.answer) - in_claims - years, key=float)
        if stray:
            result.blocking.append(Violation(
                FailureCategory.missing_citation,
                f"the answer states number(s) {', '.join(stray)} that no claim (and so no source) supports"))
        if len(response.answer.split()) < MIN_ANSWER_WORDS:
            result.advisory.append(Violation(
                FailureCategory.vague_response, "the answer is too short to be useful", blocking=False))

    @staticmethod
    def _with_referral(response: NutritionResponse) -> NutritionResponse:
        """Every decline reads the same, wherever it came from, and points the user to a professional
        (Problem Statement §6): the model's own wording is replaced by the fixed decline message."""
        if response.status is not ResponseStatus.out_of_scope or response.answer == DECLINE_MESSAGE:
            return response
        return response.model_copy(update={"answer": DECLINE_MESSAGE})

    @staticmethod
    def _name_searched(response: NutritionResponse, searched: Sequence[CorpusDocument]) -> NutritionResponse:
        """A not_in_corpus answer must say which documents were searched; add them if the model did not."""
        if response.status is not ResponseStatus.not_in_corpus:
            return response
        if any(d.document_name in response.answer or d.named_in(response.answer) for d in searched):
            return response
        listing = "; ".join(d.label for d in searched)
        return response.model_copy(update={"answer": f"{response.answer.rstrip()} Documents searched: {listing}."})


# ── checks that need context the response alone does not carry ───────────────
def detect_missing_refusal(
    question: str,
    response: NutritionResponse,
    safety: Any,
    previous_user_message: Optional[str] = None,
) -> Optional[Violation]:
    """A restricted question that was answered. Safety runs before retrieval, so this should never fire in
    the pipeline; it guards against a wiring mistake and scores the adversarial evaluation."""
    if response.status is ResponseStatus.out_of_scope:
        return None
    verdict = safety.check(question, previous_user_message)
    if verdict.allowed:
        return None
    return Violation(
        FailureCategory.missing_refusal,
        f"restricted question ({verdict.category.value if verdict.category else 'unknown'}) got status "
        f"'{response.status.value}'",
    )


def detect_not_in_corpus_answered(
    top_score: Optional[float], min_score: float, response: NutritionResponse
) -> Optional[Violation]:
    """Answered although retrieval found nothing relevant: the model must have used its own knowledge.
    With the pipeline's similarity gate on this cannot happen; evaluation runs with the gate off use it."""
    if response.status is not ResponseStatus.answered:
        return None
    if top_score is None or top_score < min_score:
        best = "no chunks" if top_score is None else f"best similarity {top_score:.2f} < {min_score:.2f}"
        return Violation(FailureCategory.not_in_corpus_answered, f"answered with {best} retrieved")
    return None

"""Citation spot-check helper (Phase 7.4): puts everything a person needs to verify a citation on one page.

    backend\\.venv\\Scripts\\python -m evaluation.citation_checker                 # worksheet from the latest benchmark
    backend\\.venv\\Scripts\\python -m evaluation.citation_checker --chunk usda_dietary_guidelines_chunk_065

A citation is not valid because the document exists; it must *support* the claim (Problem Statement §11). For each
claim of each answered response this prints/writes: the claim, the cited chunk and its metadata, the public URL,
the page(s) of the **downloaded source file** where the chunk's content is found (located by word overlap with the
original PDF/HTML, independently of the chunk's own extraction), and whether each number in the claim is on that
page. The worksheet ends each claim with a verdict line; verdicts are recorded by hand in
``results/citation_review.json`` and picked up by ``evaluation.report``.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

from evaluation import scoring
from evaluation.common import (CHUNKS_DIR, RAW_DIR, REGISTRY_PATH, RESULTS_DIR, configure_console, load_latest)

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")


def _registry() -> dict[str, dict[str, Any]]:
    docs = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["documents"]
    return {d["document_name"]: d for d in docs}


def source_pages(path: Path) -> list[str]:
    """Text of each page of the downloaded source file (one 'page' for the WHO web page)."""
    if path.suffix.lower() == ".pdf":
        import pymupdf

        with pymupdf.open(path) as doc:
            return [page.get_text() for page in doc]
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "lxml")
    return [soup.get_text(" ")]


def locate(chunk_text: str, pages: list[str], top: int = 2) -> list[tuple[int, float]]:
    """(1-based page, share of the chunk's words found on that page) for the best pages."""
    wanted = set(_TOKEN.findall(scoring.norm(chunk_text)))
    if not wanted:
        return []
    scored = [(n, len(wanted & set(_TOKEN.findall(scoring.norm(text)))) / len(wanted)) for n, text in enumerate(pages, start=1)]
    return sorted(scored, key=lambda p: -p[1])[:top]


def numbers_on_page(claim_text: str, page_text: str) -> dict[str, bool]:
    page_numbers = scoring.numbers(page_text)
    return {n: n in page_numbers for n in sorted(scoring.numbers(claim_text), key=float)}


class Locator:
    """Caches each source file's pages: the 164-page USDA PDF is read once."""

    def __init__(self) -> None:
        self.registry = _registry()
        self._pages: dict[str, list[str]] = {}

    def pages(self, document_name: str) -> Optional[list[str]]:
        reg = self.registry.get(document_name)
        if not reg:
            return None
        if document_name not in self._pages:
            matches = sorted(RAW_DIR.glob(f"{reg['id']}.*"))
            self._pages[document_name] = source_pages(matches[0]) if matches else []
        return self._pages[document_name] or None


def describe(claim_text: str, chunk: scoring.GroundChunk, locator: Locator, excerpt: int = 700) -> dict[str, Any]:
    pages = locator.pages(chunk.document_name)
    found = locate(chunk.text, pages) if pages else []
    best_page = pages[found[0][0] - 1] if found else ""
    return {
        "chunk_id": chunk.chunk_id, "document": f"{chunk.document_name} ({chunk.publisher}, {chunk.year})",
        "section": chunk.section, "url": chunk.source_url, "pages": [{"page": p, "overlap": round(s, 2)} for p, s in found],
        "claim_numbers_in_chunk": {n: n in scoring.chunk_numbers(chunk.text) for n in sorted(scoring.numbers(claim_text), key=float)},
        "claim_numbers_on_best_page": numbers_on_page(claim_text, best_page) if best_page else {},
        "excerpt": chunk.text[:excerpt] + ("..." if len(chunk.text) > excerpt else ""),
    }


def worksheet(benchmark: dict[str, Any], corpus: scoring.Corpus, review: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    locator = Locator()
    lines = ["# Citation spot-check worksheet", "",
             f"_Generated from the benchmark run of {benchmark.get('run_at', '?')} (`{benchmark.get('base_url', '')}`). "
             "For each claim: open the URL, find the page given, and confirm the claim and every number are stated "
             "there. A citation that exists but does not support the claim is a failure._", ""]
    items = []
    for q in benchmark["questions"]:
        resp = q.get("response") or {}
        if q.get("status") != "answered":
            continue
        lines += [f"## {q['id']}: {q['question']}", "", f"**Answer:** {resp.get('answer', '')}", ""]
        for i, claim in enumerate(resp.get("claims", []), start=1):
            src = claim["source"]
            chunk = corpus.chunk_for(src["chunk_id"], resp.get("retrieved_sources", []))
            if chunk is None:
                lines += [f"### Claim {i}: chunk {src['chunk_id']} NOT FOUND in the corpus (fabricated?)", ""]
                continue
            d = describe(claim["claim_text"], chunk, locator)
            verdict = review.get(q["id"], {}).get(str(i))
            items.append({"question_id": q["id"], "claim": i, **d})
            lines += [
                f"### Claim {i}", "", f"> {claim['claim_text']}", "",
                f"- Cited: `{d['chunk_id']}`, {d['document']}, section: {d['section'] or '(none)'}",
                f"- URL: {d['url']}",
                "- Found in the downloaded source at: " + (", ".join(f"page {p['page']} ({p['overlap']:.0%} of the chunk's words)" for p in d["pages"]) or "not located"),
                f"- Numbers in the claim, present in the cited chunk: {d['claim_numbers_in_chunk'] or 'none'}",
                f"- Numbers in the claim, present on the located page: {d['claim_numbers_on_best_page'] or 'n/a'}",
                "", "<details><summary>Cited chunk text</summary>", "", "```", d["excerpt"], "```", "", "</details>", "",
                f"**Verdict:** {verdict['verdict'] + ' - ' + verdict.get('note', '') if verdict else '[ ] supported   [ ] partly supported   [ ] not supported'}", "",
            ]
    return "\n".join(lines), items


def main(argv: Any = None) -> int:
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--chunk", help="show one chunk (id) with its source pages instead of a worksheet")
    parser.add_argument("--claim", default="", help="with --chunk: the claim text to check numbers against")
    parser.add_argument("--out", default=str(RESULTS_DIR / "citation_worksheet.md"))
    args = parser.parse_args(argv)

    corpus = scoring.Corpus.load(CHUNKS_DIR, REGISTRY_PATH)
    if args.chunk:
        chunk = corpus.chunks.get(args.chunk)
        if not chunk:
            print(f"unknown chunk id {args.chunk!r}", file=sys.stderr)
            return 2
        print(json.dumps(describe(args.claim, chunk, Locator(), excerpt=2000), indent=2, ensure_ascii=False))
        return 0

    benchmark = load_latest("benchmark")
    if not benchmark:
        print("no benchmark results yet: run `python -m evaluation.benchmark_questions` first", file=sys.stderr)
        return 2
    review_path = RESULTS_DIR / "citation_review.json"
    review = json.loads(review_path.read_text(encoding="utf-8")).get("reviews", {}) if review_path.exists() else {}
    text, items = worksheet(benchmark, corpus, review)
    Path(args.out).write_text(text, encoding="utf-8")
    answered = len({i["question_id"] for i in items})
    print(f"worksheet for {len(items)} claims in {answered} answered responses written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Automated QA over extraction and chunking; results feed docs/ingestion_report.md.

* numeric fidelity   numbers in the source text vs numbers in the extracted blocks (a number that
                     disappears is lost data; a table number that never appears on its source
                     page was altered or invented)
* chunk statistics   token sizes, sections, atomic units, tiny chunks
* registry years     evidence for each document's publication year, to cross-check the registry
"""

import json
import re
import statistics
from collections import Counter
from typing import Sequence

from ingestion.chunker import RawChunk
from ingestion.common import Document
from ingestion.extract_text import _MERGED_NUMBERS
from ingestion.models import TABLE, Extraction

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_TINY_TOKENS = 20


def numbers_in(text: str) -> set[str]:
    return set(_NUMBER.findall(text))


def _block_text(block) -> str:
    return " ".join([block.text, block.caption or ""] + [c for row in block.rows for c in row])


def numeric_fidelity(doc: Document, extraction: Extraction) -> dict:
    """Compare per-page numbers in the PDF text layer with those in the extracted blocks."""
    if doc.format != "pdf":
        return {"checked": False}
    import pymupdf

    extracted: dict[int, set[str]] = {}
    table_numbers: dict[int, set[str]] = {}
    for block in extraction.blocks:
        extracted.setdefault(block.page, set()).update(numbers_in(_block_text(block)))
        if block.kind == TABLE:
            table_numbers.setdefault(block.page, set()).update(numbers_in(_block_text(block)))
    missing: dict[int, list[str]] = {}
    altered: dict[int, list[str]] = {}
    total_raw = total_missing = 0
    pdf = pymupdf.open(doc.raw_path)
    page_lines = [[l.strip() for l in page.get_text().splitlines() if l.strip()] for page in pdf]
    # Running headers/footers repeat across pages (digits normalised); their numbers are not content.
    repeated = Counter(_NUMBER.sub("#", l) for lines in page_lines for l in set(lines))
    for index, lines in enumerate(page_lines):
        n = index + 1
        if n in doc.exclude_pages:
            continue
        raw = numbers_in(" ".join(l for l in lines if len(l) < 25 or repeated[_NUMBER.sub("#", l)] < 3))
        lost = raw - extracted.get(n, set())
        invented = table_numbers.get(n, set()) - raw
        total_raw += len(raw)
        total_missing += len(lost)
        if lost:
            missing[n] = sorted(lost)
        if invented:
            altered[n] = sorted(invented)
    return {
        "checked": True,
        "raw_numbers": total_raw,
        "missing_numbers": total_missing,
        "missing_by_page": missing,
        "table_numbers_not_in_source": altered,
    }


def suspicious_table_cells(extraction: Extraction) -> list[str]:
    """Cells that look like two table cells fused into one: the mark of a mis-segmented table."""
    found = []
    for block in extraction.blocks:
        if block.kind != TABLE:
            continue
        for r, row in enumerate(block.rows[1:], start=1):
            for cell in row:
                if _MERGED_NUMBERS.match(cell):
                    found.append(f"page {block.page}, {block.caption or 'table'}, row {r}: {cell!r}")
    return found


def chunk_stats(chunks: Sequence[RawChunk], token_counts: Sequence[int]) -> dict:
    kinds = Counter(c.kind for c in chunks)
    sections = {c.section for c in chunks}
    return {
        "chunks": len(chunks),
        "by_kind": dict(kinds),
        "sections": len(sections),
        "tokens_avg": round(statistics.mean(token_counts), 1) if token_counts else 0,
        "tokens_min": min(token_counts, default=0),
        "tokens_max": max(token_counts, default=0),
        "tiny_chunks": sum(1 for t in token_counts if t < _TINY_TOKENS),
    }


def year_evidence(doc: Document) -> list[str]:
    """Dates found inside the downloaded file, to cross-check the registry's ``year``."""
    evidence: list[str] = []
    if doc.format == "html":
        html = doc.raw_path.read_text(encoding="utf-8", errors="replace")
        for key in ("datePublished", "dateModified"):
            m = re.search(rf'"{key}"\s*:\s*"(\d{{4}}-\d{{2}}-\d{{2}})', html)
            if m:
                evidence.append(f"page {key} {m.group(1)}")
        return evidence
    import pymupdf

    pdf = pymupdf.open(doc.raw_path)
    for key, label in (("creationDate", "PDF created"), ("modDate", "PDF modified")):
        m = re.match(r"D:(\d{4})(\d{2})(\d{2})", pdf.metadata.get(key) or "")
        if m:
            evidence.append(f"{label} {m.group(1)}-{m.group(2)}-{m.group(3)}")
    text = " ".join(page.get_text() for page in pdf)
    for m in sorted(set(re.findall(r"(?:©|Copyright|copyright)\s*(?:Crown copyright\s*)?(\d{4})", text))):
        evidence.append(f"copyright notice {m}")
    return evidence


def year_matches(doc: Document, evidence: Sequence[str]) -> bool:
    years = {int(y) for e in evidence for y in re.findall(r"(?<![0-9])((?:19|20)[0-9]{2})(?![0-9])", e)}
    return doc.year in years

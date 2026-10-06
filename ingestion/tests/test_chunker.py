import dataclasses
import json
import re
from datetime import date

import pytest

from ingestion.chunker import (
    RawChunk,
    build_chunks,
    embed_input,
    list_parts,
    split_text,
    table_parts,
    to_records,
)
from ingestion.common import Document
from ingestion.models import HEADING, LIST, TABLE, TEXT, Block, Extraction
from ingestion.upload_to_vectorstore import InvalidChunk, validate_records


def words(text: str) -> int:
    """Stand-in tokenizer: one token per whitespace-separated word."""
    return len(text.split())


def para(n: int, tag: str = "w") -> str:
    return " ".join(f"{tag}{i}" for i in range(n))


def sentences(n: int, per: int = 10) -> str:
    return " ".join(f"S{i} " + para(per - 1, f"s{i}x") + "." for i in range(n))


def extraction(*blocks: Block) -> Extraction:
    return Extraction("doc", "test", 1, list(blocks))


# ── split_text ───────────────────────────────────────────────────────────────
def test_short_text_is_one_piece():
    assert split_text("alpha beta gamma", words, 10, 2) == ["alpha beta gamma"]


def test_pieces_respect_size_and_cover_everything():
    text = sentences(40)
    pieces = split_text(text, words, 50, 8)
    assert len(pieces) > 3
    assert all(words(p) <= 50 for p in pieces)
    joined = " ".join(pieces)
    assert all(f"S{i} " in joined for i in range(40))  # nothing dropped


def test_consecutive_pieces_overlap():
    pieces = split_text(sentences(40), words, 50, 12)
    for a, b in zip(pieces, pieces[1:]):
        shared = [s for s in re.findall(r"S\d+ ", a) if s in b]
        assert shared, "no overlap between consecutive chunks"
        assert words(a) <= 50 and words(b) <= 50


def test_prefers_paragraph_boundaries():
    p1, p2 = sentences(3), sentences(3)
    pieces = split_text(p1 + "\n\n" + p2, words, words(p1) + 2, 0)
    assert pieces == [p1, p2]


def test_unbroken_run_is_hard_split_within_size():
    pieces = split_text("x" * 5000, lambda s: len(s) // 10, 50, 0)
    assert all(len(p) // 10 <= 50 for p in pieces) and "".join(pieces) == "x" * 5000


def test_sentence_punctuation_is_not_doubled():
    pieces = split_text("Alpha one. Beta two. Gamma three.", words, 3, 0)
    assert pieces == ["Alpha one.", "Beta two.", "Gamma three."]


# ── atomic units ─────────────────────────────────────────────────────────────
def test_small_table_is_one_chunk_with_header_and_separator():
    block = Block(TABLE, rows=[["Food", "Days"], ["Eggs", "21"]], caption="Storage")
    (part,) = table_parts(block, "Storage", words, 100)
    assert part.splitlines() == ["Storage", "| Food | Days |", "| --- | --- |", "| Eggs | 21 |"]


def test_big_table_splits_on_rows_and_repeats_header():
    rows = [["Age", "Value"]] + [[f"{i} y", str(i)] for i in range(60)]
    parts = table_parts(Block(TABLE, rows=rows), "T", words, 40)
    assert len(parts) > 2
    for part in parts:
        lines = part.splitlines()
        assert lines[:3] == ["T", "| Age | Value |", "| --- | --- |"]
        assert words(part) <= 40
        assert all(line.endswith("|") for line in lines[1:])  # never a half row
    body = [line for p in parts for line in p.splitlines()[3:]]
    assert len(body) == 60 and len(set(body)) == 60  # every row exactly once


def test_multi_row_header_is_kept_together():
    rows = [["", "Males"], ["Age", "kcal"], ["1", "765"], ["2", "1004"]]
    (part,) = table_parts(Block(TABLE, rows=rows, header_rows=2), "", words, 100)
    assert part.splitlines()[:3] == ["|  | Males |", "| Age | kcal |", "| --- | --- |"]


def test_pipes_in_cells_do_not_break_the_row():
    (part,) = table_parts(Block(TABLE, rows=[["a", "b"], ["x|y", "z"]]), "", words, 100)
    assert "| x/y | z |" in part


def test_list_fits_or_splits_between_items_with_lead_in():
    text = "The following apply:\n" + "\n".join(f"- item {i} " + para(10) for i in range(30))
    assert list_parts(text, words, 10_000, 0) == [text]
    parts = list_parts(text, words, 60, 8)
    assert len(parts) > 2
    assert all(p.startswith("The following apply:\n") and words(p) <= 60 for p in parts)
    items = [line for p in parts for line in p.splitlines()[1:]]
    assert len(items) == 30 and all(line.startswith("- item") for line in items)


# ── build_chunks ─────────────────────────────────────────────────────────────
def test_chunks_never_span_a_heading_and_sections_are_exact():
    chunks = build_chunks(
        extraction(
            Block(HEADING, "Overview", level=1),
            Block(TEXT, "intro text here that is long enough to be kept"),
            Block(HEADING, "Fats", level=2),
            Block(TEXT, "fat text here that is long enough to be kept"),
            Block(HEADING, "Sugars", level=2),
            Block(TEXT, "sugar text here that is long enough to be kept"),
        ),
        words, size=50, overlap=5, embed_section=False,
    )
    assert [(c.section, c.text.split()[0]) for c in chunks] == [
        ("Overview", "intro"), ("Overview > Fats", "fat"), ("Overview > Sugars", "sugar"),
    ]


def test_list_is_its_own_atomic_chunk_even_next_to_text():
    items = "\n".join(f"- point {i} words" for i in range(5))
    chunks = build_chunks(
        extraction(
            Block(HEADING, "H", level=1),
            Block(TEXT, "before the list there is a paragraph of prose"),
            Block(LIST, items),
            Block(TEXT, "after the list there is another paragraph of prose"),
        ),
        words, size=100, overlap=5, embed_section=False,
    )
    assert [c.kind for c in chunks] == ["text", "list", "text"]
    assert chunks[1].text == items


def test_table_section_is_its_caption_and_title_has_short_parent_heading():
    chunks = build_chunks(
        extraction(
            Block(HEADING, "STORAGE CHART", level=1),
            Block(TABLE, rows=[["Product", "Fridge"], ["Eggs", "3 weeks"]], caption="Eggs"),
        ),
        words, size=100, overlap=5, embed_section=False,
    )
    (chunk,) = chunks
    assert chunk.section == "Eggs"
    assert chunk.text.splitlines()[0] == "STORAGE CHART — Eggs"


def test_embedded_input_stays_within_budget_including_section_prefix():
    section = "A Fairly Long Section Heading For This Test"
    blocks = [Block(HEADING, section, level=1), Block(TEXT, sentences(60))]
    chunks = build_chunks(extraction(*blocks), words, size=60, overlap=10)
    assert len(chunks) > 3
    assert all(words(embed_input(c)) <= 60 for c in chunks)
    assert all(c.text and not c.text.startswith(section) for c in chunks)  # stored text stays raw


def test_layout_residue_is_dropped():
    chunks = build_chunks(
        extraction(Block(HEADING, "H", level=1), Block(TEXT, "6"), Block(TEXT, "12 34")),
        words, size=50, overlap=5,
    )
    assert chunks == []


# ── records ──────────────────────────────────────────────────────────────────
DOC = Document("who_healthy_diet", "Healthy Diet Fact Sheet", "WHO", 2020, "https://example.org/x", "html", "html")


def make_records(n=12):
    chunks = [RawChunk("Fats", f"chunk text number {i} with enough letters", 1, "text") for i in range(n)]
    return to_records(DOC, chunks, date(2025, 10, 5))


def test_records_follow_the_architecture_schema_and_ids_are_deterministic():
    records = make_records(12)
    assert records[7].chunk_id == "who_healthy_diet_chunk_007"
    assert [r.chunk_id for r in records] == [r.chunk_id for r in make_records(12)]
    r = records[7]
    assert (r.document_name, r.publisher, r.year, r.source_url) == (DOC.document_name, "WHO", 2020, DOC.source_url)
    assert (r.retrieval_date, r.section, r.chunk_index, r.total_chunks) == ("2025-10-05", "Fats", 7, 12)
    validate_records(records)


def test_validation_rejects_incomplete_or_inconsistent_metadata():
    records = make_records(3)
    with pytest.raises(InvalidChunk, match="empty section"):
        validate_records([dataclasses.replace(records[0], section=" ")] + records[1:])
    with pytest.raises(InvalidChunk, match="duplicate"):
        validate_records([records[0], records[0], records[2]])
    with pytest.raises(InvalidChunk, match="total_chunks"):
        validate_records(records[:2])


def test_extraction_round_trips_through_json():
    e = extraction(Block(HEADING, "H", level=2), Block(TABLE, rows=[["a", "b"]], caption="c", header_rows=1))
    again = Extraction.from_dict(json.loads(json.dumps(e.to_dict())))
    assert again == e

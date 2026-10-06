import json

import httpx
import pytest

import ingestion.common as common
from ingestion.common import Document, load_registry
from ingestion.download_documents import DownloadError, fetch
from ingestion.extract_text import (
    _collapse_spans,
    clean,
    extract_html,
    garbled_ratio,
    merge_lists,
    repair_fused_cells,
    suspicious_cells,
)
from ingestion.models import LIST, TEXT, Block


def doc(**overrides) -> Document:
    fields = dict(id="d", document_name="D", publisher="P", year=2020, source_url="https://x.org/d",
                  format="html", extractor="html")
    fields.update(overrides)
    return Document(**fields)


# ── registry ─────────────────────────────────────────────────────────────────
def test_registry_lists_the_six_corpus_documents_with_complete_metadata():
    docs = load_registry()
    assert len(docs) == 6
    assert len({d.id for d in docs}) == 6
    for d in docs:
        assert d.document_name and d.publisher and d.source_url.startswith("https://")
        assert 1990 < d.year < 2030
        assert d.format in ("pdf", "html") and d.extractor in ("html", "pymupdf", "docling", "fda_chart")
    assert {d.extractor for d in docs} >= {"docling", "pymupdf", "html"}


def test_registry_rejects_unknown_ids():
    with pytest.raises(SystemExit):
        load_registry(["nope"])


def test_download_url_overrides_fetch_url_but_never_the_citation_url():
    usda = load_registry(["usda_dietary_guidelines"])[0]
    assert usda.fetch_url != usda.source_url
    assert usda.source_url.startswith("https://www.dietaryguidelines.gov/")


# ── downloader fails loudly ──────────────────────────────────────────────────
def client_returning(response: httpx.Response) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda request: response))


def test_fetch_returns_a_valid_pdf():
    body = b"%PDF-1.7 " + b"x" * 10_000
    resp = httpx.Response(200, content=body, headers={"content-type": "application/pdf"})
    assert fetch(doc(format="pdf"), client_returning(resp)) == body


@pytest.mark.parametrize(
    "response, message",
    [
        (httpx.Response(404, content=b"Not found"), "HTTP 404"),
        (httpx.Response(403, content=b"<HTML>Access Denied</HTML>"), "HTTP 403"),
        (httpx.Response(200, content=b"%PDF-1.7 tiny"), "bytes"),
        (httpx.Response(200, content=b"<html>" + b"x" * 10_000, headers={"content-type": "text/html"}), "not a PDF"),
    ],
)
def test_fetch_raises_on_http_errors_html_masquerading_as_pdf_and_tiny_files(response, message):
    with pytest.raises(DownloadError, match=message):
        fetch(doc(format="pdf"), client_returning(response))


def test_fetch_rejects_non_html_for_an_html_source():
    resp = httpx.Response(200, content=b"x" * 10_000, headers={"content-type": "application/pdf"})
    with pytest.raises(DownloadError, match="not HTML"):
        fetch(doc(), client_returning(resp))


# ── text helpers ─────────────────────────────────────────────────────────────
def test_clean_strips_control_characters_nbsp_and_soft_hyphens():
    assert clean("A" + chr(8) + "..." + chr(0xA0) + "B  " + chr(0xAD) + "C\n") == "A... B C"


def test_garbled_ratio_counts_replacement_characters():
    assert garbled_ratio("abc" + chr(0xFFFD)) == 0.25 and garbled_ratio("") == 0.0


def test_lead_in_paragraph_joins_its_list_but_unrelated_paragraphs_do_not():
    blocks = [
        Block(TEXT, "Intro paragraph."),
        Block(TEXT, "You could:"),
        Block(LIST, "- a\n- b"),
        Block(LIST, "- c"),
    ]
    merged = merge_lists(blocks)
    assert [b.kind for b in merged] == [TEXT, LIST]
    assert merged[1].text == "You could:\n- a\n- b\n- c"


# ── html ─────────────────────────────────────────────────────────────────────
def test_html_extraction_keeps_headings_paragraphs_lists_and_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "RAW_DIR", tmp_path)
    (tmp_path / "d.html").write_text(
        "<html><body><article><h1>Title</h1><h2>Fats</h2><p>Limit  fat intake.</p>"
        "<ul><li>one</li><li>two</li></ul><ol><li>first</li></ol>"
        "<table><caption>T1</caption><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>"
        "<script>evil()</script></article></body></html>",
        encoding="utf-8",
    )
    result = extract_html(doc())
    kinds = [(b.kind, b.level, b.text) for b in result.blocks if b.kind != "table"]
    assert kinds == [
        ("heading", 1, "Title"), ("heading", 2, "Fats"), ("text", 0, "Limit fat intake."),
        ("list", 0, "- one\n- two\n1. first"),  # adjacent lists are one atomic unit
    ]
    table = next(b for b in result.blocks if b.kind == "table")
    assert table.rows == [["a", "b"], ["1", "2"]] and table.caption == "T1"
    assert "evil" not in json.dumps(result.to_dict())


# ── table repairs ────────────────────────────────────────────────────────────
def test_fused_cell_with_empty_right_neighbour_is_split():
    rows = [["Age", "Choline", "Cobalamin"], ["7-11 mo", "160 1.5", ""], ["1-3", "140", "1.5"]]
    fixed = repair_fused_cells(rows)
    assert fixed[1] == ["7-11 mo", "160", "1.5"]
    assert suspicious_cells(fixed) == 0


def test_first_token_pulled_down_by_mis_segmentation_moves_back_up():
    rows = [
        ["Age", "Zinc"],
        ["7-11mo (a)", ""],
        ["7-11 1-3", "2.4 3.6"],
        ["4-6", "4.6"],
    ]
    # row 1 has an empty zinc cell above "2.4 3.6", and "7-11 1-3" has no empty neighbour to its right
    fixed = repair_fused_cells(rows)
    assert fixed[1][1] == "2.4" and fixed[2][1] == "3.6"


def test_repair_leaves_legitimate_two_value_cells_alone_when_ambiguous():
    rows = [["Age", "Iron"], ["18-24", "6"], ["≥ 25", "7 6"]]  # premenopausal 7, postmenopausal 6
    assert repair_fused_cells(rows) == rows
    assert suspicious_cells(rows) == 1  # still flagged for the report


def test_repair_never_changes_the_numbers_present():
    rows = [["h", "a", "b"], ["x", "160 1.5", ""], ["y", "5", "6"]]
    nums = lambda rs: sorted(t for r in rs for c in r for t in c.split())
    assert nums(repair_fused_cells(rows)) == nums(rows)


def test_section_rows_repeated_across_every_cell_collapse():
    rows = [["h1", "h2", "h3"], ["Pregnancy", "Pregnancy", "Pregnancy"], ["18-24", "860", "7"]]
    assert _collapse_spans(rows)[1] == ["Pregnancy", "", ""]
    assert _collapse_spans(rows)[2] == ["18-24", "860", "7"]


# ── FDA chart (real file; skipped before the first download) ─────────────────
FDA = load_registry(["fda_storage_chart"])[0]


@pytest.mark.skipif(not FDA.raw_path.exists(), reason="run ingestion.download_documents first")
def test_fda_chart_is_rebuilt_as_one_clean_table_per_category():
    import pymupdf

    from ingestion import fda_chart

    blocks = fda_chart.extract_blocks(pymupdf.open(FDA.raw_path)[0])
    tables = {b.caption: b.rows for b in blocks if b.kind == "table"}
    assert len(tables) == 13
    assert all(r[0] == ["Product", "Refrigerator", "Freezer"] for r in tables.values())
    assert ["Fresh, in shell", "3 - 5 weeks", "Don’t freeze"] in tables["Eggs"]
    assert ["Liquid pasteurized eggs or egg substitutes, unopened", "10 days", "1 year"] in tables["Eggs"]
    assert ["Hot dogs, opened package", "1 week", "1 - 2 months"] in tables["Hot Dogs & Lunch Meats (in freezer wrap)"]
    assert ["Chicken or turkey, whole", "1 - 2 days", "1 year"] in tables["Fresh Poultry"]
    assert ["Canned seafood after opening out of can (Pantry, 5 years)", "3 - 4 days", "2 months"] in tables["Fish & Shellfish"]
    text = json.dumps([b.rows for b in blocks if b.kind == "table"], ensure_ascii=False)
    assert chr(0xFFFD) not in text  # the Docling font-decoding failure that ruled it out
    assert any(b.kind == "text" and "March 2018" in b.text for b in blocks)

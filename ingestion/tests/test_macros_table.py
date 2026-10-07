"""The food-macros extractor, on a small synthetic PDF with the real file's layout (no download needed)."""

import pymupdf

from ingestion.macros_table import HEADER, extract_blocks
from ingestion.models import TABLE

# Column left edges as printed in the real PDF: Kcals 211, Protein 284, Fats 354, Carbs 430, Fibre 501.
COLUMNS = (211, 284, 354, 430, 501)


def put(page, y: float, label_x: float, label: str, values: tuple = ()) -> None:
    page.insert_text((label_x, y), label, fontsize=9)
    for x, value in zip(COLUMNS, values):
        if value != "":
            page.insert_text((x, y), value, fontsize=9)


def build_pdf():
    pdf = pymupdf.open()
    page = pdf.new_page()
    put(page, 100, 99, "Weight")
    page.insert_text((204, 100), "Kcals", fontsize=9)
    put(page, 80, 207, "PROTEINS - FISH (RAW)")
    put(page, 130, 253, "Cod (smoked)")
    put(page, 150, 65, "Average Portion 150g", ("118", "27", "0.9", "0", "0"))
    put(page, 165, 107, "100g", ("79", "18.3", "0.6", "0", "0"))
    put(page, 190, 253, "Hake")
    put(page, 210, 65, "Average Portion 150g", ("138", "27", "3.3", "0", "0"))
    put(page, 225, 107, "100g", ("92", "", "2.2", "0", "0"))  # protein cell left empty in the source
    put(page, 250, 207, "FRUIT")
    put(page, 270, 253, "Banana")
    put(page, 290, 65, "Average Portion 1 medium", ("89", "1.1", "0.3", "23", "2.6"))
    page2 = pdf.new_page()
    put(page2, 80, 455, "SUMMARY")
    put(page2, 100, 233, "PROTEINS - FISH (RAW)")
    put(page2, 120, 187, "HIGHEST PROTEIN CONTENT PER AVERAGE PORTION")
    put(page2, 140, 73, "Wild Salmon 150g", ("269", "33", "15", "0", "0"))
    put(page2, 160, 187, "LOWEST PROTEIN CONTENT PER AVERAGE PORTION")
    put(page2, 180, 73, "Monkfish 150g", ("100", "24", "0.6", "0", "0"))
    return pdf


def rows_of(blocks):
    return [r for b in blocks if b.kind == TABLE for r in b.rows[1:]]


def test_every_number_lands_in_its_own_column_including_zeros():
    blocks, warnings = extract_blocks(build_pdf())
    rows = rows_of(blocks)
    assert ["Cod (smoked)", "150g (average portion)", "118", "27", "0.9", "0", "0"] in rows
    assert ["Cod (smoked)", "100g", "79", "18.3", "0.6", "0", "0"] in rows
    assert ["Banana", "1 medium (average portion)", "89", "1.1", "0.3", "23", "2.6"] in rows


def test_an_empty_source_cell_stays_empty_and_is_reported():
    blocks, warnings = extract_blocks(build_pdf())
    assert ["Hake", "100g", "92", "", "2.2", "0", "0"] in rows_of(blocks)  # not shifted left
    assert any("Hake" in w and "empty cell" in w for w in warnings)


def test_tables_carry_the_food_group_as_caption_and_repeat_the_header():
    blocks, _ = extract_blocks(build_pdf())
    tables = [b for b in blocks if b.kind == TABLE]
    assert {b.caption for b in tables} >= {"PROTEINS - FISH (RAW)", "FRUIT"}
    assert all(b.rows[0] == HEADER and b.header_rows == 1 for b in tables)


def test_the_summary_labels_each_row_with_its_group_and_rank():
    blocks, _ = extract_blocks(build_pdf())
    summary = [b for b in blocks if b.kind == TABLE and b.caption.startswith("SUMMARY")]
    assert len(summary) == 1 and summary[0].page == 2
    foods = [r[0] for r in summary[0].rows[1:]]
    assert foods == [
        "PROTEINS - FISH (RAW): Wild Salmon 150g (highest protein per average portion)",
        "PROTEINS - FISH (RAW): Monkfish 150g (lowest protein per average portion)",
    ]


def test_a_table_never_spans_two_pages():
    blocks, _ = extract_blocks(build_pdf())
    assert {b.page for b in blocks if b.kind == TABLE and not b.caption.startswith("SUMMARY")} == {1}

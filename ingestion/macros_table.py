"""Extractor for "Calories and Macronutrients of Common Foods" (Fit for Films, 2018).

The PDF is one long food table: per page a header (Weight | Kcals | Protein | Fats | Carbs | Fibre), a food
name on its own line, then an "Average Portion ..." row and a "100g" row of five numbers. The generic
PyMuPDF extractor drops table cells here (its fragment-stitching step discards a cell whose text already
appears in a neighbouring cell, e.g. a lone "0"), and about one number in eight was lost. This extractor
rebuilds each row from word positions instead and assigns every number to a column by its x position, so a
missing cell stays empty rather than shifting its neighbours.

Output: one table per food group (at most ``FOODS_PER_TABLE`` foods each, so a chunk stays focused), with the
food name repeated on both of its rows, plus one "SUMMARY" table for the document's own highest / lowest
protein list (pages 14-15).
"""

import re
from typing import Optional

from ingestion.models import HEADING, TABLE, TEXT, Block

HEADER = ["Food", "Portion", "Kcals", "Protein", "Fats", "Carbs", "Fibre"]
FOODS_PER_TABLE = 10
SUMMARY_ROWS_PER_TABLE = 14
LINE_TOLERANCE = 3.5  # pt: words whose vertical centres are this close share a line

# Left edge (pt) of each numeric column's band; the printed columns sit at about 205, 271, 350, 417, 491.
_COLUMN_STARTS = (190, 250, 320, 390, 460)
_NUMBER = re.compile(r"^\d+(?:[.,]\d+)?\.?$")  # also the source's typos "30." and "4,6"; kept as printed
_NOTE = re.compile(r"^(HIGHEST|LOWEST)\b", re.I)


def _lines(page) -> list[list[tuple]]:
    """Words grouped into visual lines (top to bottom), each sorted left to right."""
    lines: list[list] = []
    for w in sorted(page.get_text("words"), key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        cy = (w[1] + w[3]) / 2
        if lines and abs(lines[-1][0] - cy) < LINE_TOLERANCE:
            lines[-1][1].append(w)
        else:
            lines.append([cy, [w]])
    return [sorted(ws, key=lambda w: w[0]) for _, ws in lines]


def _column(x: float) -> int:
    col = 0
    for i, start in enumerate(_COLUMN_STARTS):
        if x >= start:
            col = i
    return col


def _split(words: list[tuple]) -> tuple[str, list[str], int]:
    """(label text, five column values, count of numbers that landed in an already-filled column)."""
    label = [w[4] for w in words if not (w[0] >= _COLUMN_STARTS[0] and _NUMBER.match(w[4]))]
    values = [""] * 5
    clashes = 0
    for w in words:
        if w[0] >= _COLUMN_STARTS[0] and _NUMBER.match(w[4]):
            col = _column(w[0])
            if values[col]:
                clashes += 1
            else:
                values[col] = w[4].rstrip(".")
    return " ".join(label), values, clashes


def _is_caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _tables(caption: str, groups: list[tuple[int, list[list[str]]]]) -> list[Block]:
    return [Block(TABLE, rows=[HEADER] + rows, header_rows=1, caption=caption, page=page) for page, rows in groups]


def _group(entries: list[tuple[int, int, list[list[str]]]], max_units: int) -> list[tuple[int, list[list[str]]]]:
    """Pack (page, unit count, rows) entries into tables of at most ``max_units`` units, never across pages."""
    groups: list[tuple[int, list[list[str]]]] = []
    units = 0
    for page, count, rows in entries:
        if not groups or groups[-1][0] != page or units + count > max_units:
            groups.append((page, []))
            units = 0
        groups[-1][1].extend(rows)
        units += count
    return groups


def extract_blocks(pdf) -> tuple[list[Block], list[str]]:
    """Blocks in reading order plus warnings (rows with missing cells, number clashes, orphan names)."""
    blocks: list[Block] = [
        Block(HEADING, "Calories and Macronutrients of Common Foods", level=1, page=1),
        Block(
            TEXT,
            "Calories and Macronutrients of Common Foods (2018). Kcals, protein, fats, carbs and fibre for common "
            "foods, per average portion and per 100g, grouped by type of food. A summary at the end lists the "
            "highest and lowest protein per average portion in each group.",
            page=1,
        ),
    ]
    warnings: list[str] = []
    section: Optional[str] = None
    food: Optional[str] = None
    foods: list[tuple[int, int, list[list[str]]]] = []  # (page, 1, rows) per food of the current section
    in_summary = False
    note: Optional[str] = None
    summary: list[tuple[int, int, list[list[str]]]] = []  # (page, 1, [row]) per summary line

    def flush_section() -> None:
        nonlocal foods
        if section and foods:
            blocks.extend(_tables(section, _group(foods, FOODS_PER_TABLE)))
        foods = []

    for page_no, page in enumerate(pdf, start=1):
        for words in _lines(page):
            text = " ".join(w[4] for w in words).strip()
            first = words[0][4]
            if first in ("Weight", "Kcals") or re.fullmatch(r"20\d\d", text):
                continue  # column header (repeated per page) / the issue year at the foot of the page
            label, values, clashes = _split(words)
            if clashes:
                warnings.append(f"page {page_no}: {clashes} number(s) in an already-filled column: {text!r}")
            has_numbers = any(values)

            if not has_numbers:
                if text.upper() == "SUMMARY":
                    flush_section()
                    in_summary, section, food = True, None, None
                elif _NOTE.match(text):
                    note = "highest protein per average portion" if text.upper().startswith("HIGHEST") else (
                        "lowest protein per average portion")
                elif _is_caps(text):
                    if in_summary:
                        section = text
                    else:
                        flush_section()
                        section, food = text, None
                else:  # a food name (a wrapped name arrives as consecutive lines)
                    if food is not None and not food_has_rows(foods, food):
                        food = f"{food} {text}"
                    else:
                        food = text
                continue

            if in_summary:
                prefix = f"{section}: " if section else ""
                tag = f" ({note})" if note else ""
                summary.append((page_no, 1, [[f"{prefix}{label}{tag}", "average portion", *values]]))
                continue

            if food is None:
                warnings.append(f"page {page_no}: numbers without a food name: {text!r}")
                continue
            is_average = label.lower().startswith("average portion")
            portion = f"{label[len('average portion'):].strip()} (average portion)" if is_average else label
            if is_average or not food_has_rows(foods, food):
                foods.append((page_no, 1, []))  # a food starts at its first row, normally "Average Portion ..."
            if not all(values):
                warnings.append(f"page {page_no}: {food} / {portion}: empty cell(s) {values}")
            foods[-1][2].append([food, portion, *values])

    flush_section()
    if summary:
        blocks.extend(_tables("SUMMARY (highest and lowest protein per average portion)",
                              _group(summary, SUMMARY_ROWS_PER_TABLE)))
    return blocks, warnings


def food_has_rows(foods: list[tuple[int, int, list[list[str]]]], food: str) -> bool:
    """Whether data rows were already recorded under ``food`` (so the next name line starts a new food)."""
    return bool(foods) and bool(foods[-1][2]) and foods[-1][2][-1][0] == food

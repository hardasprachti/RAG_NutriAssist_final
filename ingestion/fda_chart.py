"""Extractor for the FDA Refrigerator & Freezer Storage Chart (one page, two panels).

Docling was tried first and rejected: it mis-decodes this PDF's font (the letter "d" comes out
as U+FFFD, so "days" becomes "<?>ays") and finds no table structure. PyMuPDF decodes the text
correctly, so the chart is rebuilt from word positions: one table per food category
(Product | Refrigerator | Freezer), with the category as the table caption (and chunk section).
"""

import collections
import re
from typing import Optional

from ingestion.models import HEADING, LIST, TABLE, TEXT, Block

_WS = re.compile(r"\s+")
_FREEZER_OFFSET = 60  # pt right of the refrigerator column where freezer text starts
_INDENT = 1.5  # pt; continuation/variant lines are indented with leading spaces


def _clean(text: str) -> str:
    return _WS.sub(" ", text.replace(" ", " ")).strip()


def _is_bold(span: dict) -> bool:
    return bool(span["flags"] & 16) or "bold" in span["font"].lower()


def extract_blocks(page) -> list[Block]:
    spans = [
        s
        for b in page.get_text("dict")["blocks"]
        if b["type"] == 0
        for l in b["lines"]
        for s in l["spans"]
        if s["text"].strip()
    ]
    headers = sorted((s for s in spans if s["text"].strip() == "Product"), key=lambda s: s["bbox"][0])
    fridge_xs = sorted(s["bbox"][0] for s in spans if s["text"].strip() == "Refrigerator")
    if not headers or len(headers) != len(fridge_xs):
        raise ValueError("FDA chart layout changed: could not find the Product/Refrigerator headers")
    header_y = min(s["bbox"][1] for s in headers)

    footer_y = page.rect.height * 0.95
    all_words = page.get_text("words")
    words = [w for w in all_words if w[1] < footer_y]
    footer = _clean(" ".join(w[4] for w in sorted((w for w in all_words if w[1] >= footer_y), key=lambda w: (w[1], w[0]))))
    bold_boxes = [s["bbox"] for s in spans if _is_bold(s) and s["size"] < 12]

    blocks = _intro_blocks([s for s in spans if s["bbox"][3] < header_y], words, header_y)
    if footer:  # the chart's issue date ("March 2018") sits below the panels; keep it with the intro
        blocks.append(Block(TEXT, footer))
    for i, product in enumerate(headers):
        x0 = product["bbox"][0] - 5
        x1 = headers[i + 1]["bbox"][0] - 5 if i + 1 < len(headers) else page.rect.width
        panel = [w for w in words if x0 <= w[0] < x1 and w[1] > header_y + 12]
        blocks.extend(_panel_tables(panel, bold_boxes, product["bbox"][0], fridge_xs[i]))
    return blocks


def _lines(words: list) -> list[list]:
    """Group words into visual lines (same baseline within ~3pt), left to right."""
    out: list[list] = []
    for w in sorted(words, key=lambda w: (w[1], w[0])):
        if out and abs(out[-1][0][1] - w[1]) < 3:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(line, key=lambda w: w[0]) for line in out]


def _intro_blocks(intro_spans: list, words: list, header_y: float) -> list[Block]:
    title_spans = [s for s in intro_spans if s["size"] > 14]
    title = " ".join(s["text"] for s in title_spans)
    blocks = [Block(HEADING, _clean(title), level=1)] if title.strip() else []
    title_bottom = max((s["bbox"][3] for s in title_spans), default=0)
    intro_words = [w for w in words if w[3] < header_y and w[1] >= title_bottom - 2]
    paragraphs: list[str] = []
    items: list[str] = []
    for line in _lines(intro_words):
        text = _clean(" ".join(w[4] for w in line))
        if text.startswith("•"):
            items.append(text.lstrip("• ").strip())
        elif items and text[:1].islower():
            items[-1] += " " + text  # wrapped bullet
        else:
            paragraphs.append(text)
    # Layout: two lead sentences, the tips list, then the freezer-time note.
    if paragraphs:
        blocks.append(Block(TEXT, " ".join(paragraphs[:2])))
    if items:
        blocks.append(Block(LIST, "\n".join("- " + i for i in items)))
    if len(paragraphs) > 2:
        blocks.append(Block(TEXT, " ".join(paragraphs[2:])))
    return blocks


def _is_bold_word(w, bold_boxes: list) -> bool:
    cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
    return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in bold_boxes)


def _panel_tables(words: list, bold_boxes: list, panel_x0: float, fridge_x: float) -> list[Block]:
    tables: list[Block] = []
    category: Optional[str] = None
    rows: list[list[str]] = []
    label: list[str] = []  # label lines of the row being assembled
    stem: list[str] = []  # lead lines shared by indented sibling rows ("Hot dogs," + "opened package")

    def end_category() -> None:
        nonlocal rows, label, stem
        if category and rows:
            tables.append(Block(TABLE, rows=[["Product", "Refrigerator", "Freezer"]] + rows, caption=category))
        rows, label, stem = [], [], []

    for line in _lines(words):
        bold = [w for w in line if _is_bold_word(w, bold_boxes)]
        if bold:
            # A bold line starts a category; non-bold words on it are a qualifier,
            # e.g. "Hot Dogs & Lunch Meats" + "(in freezer wrap)".
            end_category()
            category = _clean(" ".join(w[4] for w in line))
            continue
        product_words = [w for w in line if w[0] < fridge_x - 5]
        fridge = _clean(" ".join(w[4] for w in line if fridge_x - 5 <= w[0] < fridge_x + _FREEZER_OFFSET))
        freezer = _clean(" ".join(w[4] for w in line if w[0] >= fridge_x + _FREEZER_OFFSET))
        product = _clean(" ".join(w[4] for w in product_words))
        if (fridge or freezer) and not re.search(r"\d", fridge + freezer) and fridge.lower().startswith("after"):
            # Sub-header such as "after opening | out of can": part of the label of the next row.
            product = _clean(f"{product} {fridge} {freezer}")
            fridge = freezer = ""
        if product:
            indented = product_words[0][0] > panel_x0 + _INDENT
            if not indented and not label:
                stem = []  # a new, unindented item
            elif indented and not label and stem:
                label = list(stem)  # sibling variant of the previous row
            label.append(product)
        if fridge or freezer:
            rows.append([_clean(" ".join(label)), fridge, freezer])
            stem = label[:-1]
            label = []
    end_category()
    return tables

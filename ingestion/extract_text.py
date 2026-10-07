"""Extract structured text (headings, paragraphs, lists, tables) from each source document.

Extractors (chosen per document in document_registry.json):

* ``html``      BeautifulSoup over the WHO fact-sheet page.
* ``pymupdf``   PyMuPDF text, font-size/bold heading detection and ``find_tables`` (USDA, Eatwell, ICMR).
* ``docling``   Docling layout + TableFormer models, where table structure carries the content (EFSA).
* ``fda_chart`` PyMuPDF word geometry for the FDA two-panel chart; see ``fda_chart.py`` for why not Docling.
* ``macros_table`` PyMuPDF word geometry for the Fit for Films food-macros table; see ``macros_table.py``.

Output is cached as JSON in ingestion/data/extracted/ so the slow Docling run happens once.

    python -m ingestion.extract_text [--only ID ...]
"""

import argparse
import collections
import json
import logging
import re
import sys
from typing import Optional

from ingestion import fda_chart, macros_table
from ingestion.common import EXTRACTED_DIR, Document, load_registry
from ingestion.models import HEADING, LIST, TABLE, TEXT, Block, Extraction

logger = logging.getLogger("ingestion.extract")

_WS = re.compile(r"\s+")
_BULLET = re.compile(r"^(?:[•●▪■◦○·✓➢➤►]|[-–—]\s)\s*")
_NUMBERED = re.compile(r"^\d{1,2}[.)]\s+\S")
_NBSP, _FFFD, _SOFT_HYPHEN = chr(0xA0), chr(0xFFFD), chr(0xAD)
_PURE_NUMBER = re.compile(r"^[0-9]+([.,][0-9]+)?$")
_CONTROL = re.compile("[" + chr(0) + "-" + chr(8) + chr(0xB) + "-" + chr(0x1F) + chr(0x7F) + _SOFT_HYPHEN + "]")  # control chars, soft hyphens (TOC dot leaders)
_GARBLE_THRESHOLD = 0.005  # share of U+FFFD characters that marks a page as garbled


# The GWI booklet's running footer ("NUTRITION FOR HEALTHSPAN 25"), sometimes overprinted so that every letter is
# doubled ("NNUUTTRRITITIOIONN F FOORR H HEEAALLTTHHSSPPAANN 25"); it leaks into table cells on a few pages.
_RUNNING_FOOTER = re.compile(r"\bN+U+T+R+[A-Z\s]{8,60}?S+P+A+N+\b\s*\d{0,3}")


def clean(text: str) -> str:
    text = _RUNNING_FOOTER.sub(" ", text)
    return _WS.sub(" ", _CONTROL.sub("", text).replace(_NBSP, " ")).strip()


def garbled_ratio(text: str) -> float:
    return text.count(_FFFD) / len(text) if text else 0.0


# ── shared post-processing ───────────────────────────────────────────────────
def merge_lists(blocks: list[Block]) -> list[Block]:
    """Join adjacent list blocks and attach a lead-in paragraph ending in ':' to its list,
    so a list (and its introduction) stays one atomic unit."""
    out: list[Block] = []
    for block in blocks:
        prev = out[-1] if out else None
        if block.kind == LIST and prev is not None:
            if prev.kind == LIST:
                prev.text += "\n" + block.text
                continue
            if prev.kind == TEXT and prev.text.rstrip().endswith(":") and prev.page == block.page:
                prev.kind = LIST
                prev.text += "\n" + block.text
                continue
        out.append(block)
    return out


def _finish(doc: Document, extractor: str, pages: int, blocks: list[Block], page_text: dict[int, str]) -> Extraction:
    blocks = merge_lists([b for b in blocks if b.kind == TABLE or b.text.strip()])
    page_text = {p: t for p, t in page_text.items() if p not in doc.exclude_pages}
    empty = [p for p in range(1, pages + 1) if p not in doc.exclude_pages and len(page_text.get(p, "").strip()) < 50]
    garbled = {p for p, t in page_text.items() if garbled_ratio(t) > _GARBLE_THRESHOLD}
    for block in blocks:  # garbling in what we actually kept matters most
        cells = " ".join(c for row in block.rows for c in row)
        if garbled_ratio(block.text + cells) > _GARBLE_THRESHOLD:
            garbled.add(block.page)
    return Extraction(doc.id, extractor, pages, blocks, empty, sorted(doc.exclude_pages), sorted(garbled))


# ── HTML (WHO) ───────────────────────────────────────────────────────────────
def extract_html(doc: Document) -> Extraction:
    from bs4 import BeautifulSoup, Tag

    soup = BeautifulSoup(doc.raw_path.read_text(encoding="utf-8"), "lxml")
    root = soup.select_one(".sf-detail-body-wrapper") or soup.find("article") or soup.body
    for junk in root.find_all(["script", "style", "nav", "button", "form", "figure", "noscript"]):
        junk.decompose()

    blocks: list[Block] = []

    def walk(element: Tag) -> None:
        for child in element.children:
            if not isinstance(child, Tag):
                continue
            name = child.name
            if re.fullmatch(r"h[1-6]", name):
                blocks.append(Block(HEADING, clean(child.get_text(" ")), level=int(name[1])))
            elif name == "p":
                blocks.append(Block(TEXT, clean(child.get_text(" "))))
            elif name in ("ul", "ol"):
                items = [clean(li.get_text(" ")) for li in child.find_all("li")]
                marker = (lambda i: f"{i + 1}. ") if name == "ol" else (lambda i: "- ")
                blocks.append(Block(LIST, "\n".join(marker(i) + t for i, t in enumerate(items) if t)))
            elif name == "table":
                rows = [[clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])] for tr in child.find_all("tr")]
                rows = [r for r in rows if any(r)]
                if rows:
                    cap = child.find("caption")
                    blocks.append(Block(TABLE, rows=rows, caption=clean(cap.get_text(" ")) if cap else None))
            else:
                walk(child)

    walk(root)
    title = soup.find("h1")
    if title and not any(b.kind == HEADING and b.level == 1 for b in blocks):
        blocks.insert(0, Block(HEADING, clean(title.get_text(" ")), level=1))
    return _finish(doc, "html", 1, blocks, {1: " ".join(b.text for b in blocks)})


# ── PyMuPDF (USDA, Eatwell, ICMR) ────────────────────────────────────────────
class _Line:
    __slots__ = ("text", "size", "bold", "bbox", "block_no", "page", "weak_font")

    def __init__(self, text, size, bold, bbox, block_no, page, weak_font=False):
        self.text, self.size, self.bold, self.bbox, self.block_no, self.page = text, size, bold, bbox, block_no, page
        self.weak_font = weak_font  # a light/regular/italic face: not a heading face


_WEAK_FONT_WORDS = ("light", "regular", "book", "medium", "italic", "oblique", "thin")


def _is_bold(span: dict) -> bool:
    font = span["font"].lower()
    return bool(span["flags"] & 16) or any(k in font for k in ("bold", "black", "heavy"))


def _page_lines(page, page_no: int, skip_boxes: list, vocab: set[str]) -> list[_Line]:
    lines: list[_Line] = []
    for block_no, block in enumerate(page.get_text("dict")["blocks"]):
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            bbox = line["bbox"]
            cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in skip_boxes):
                continue  # inside a detected table; emitted as a table block instead
            chars = sum(len(s["text"]) for s in spans)
            sizes: collections.Counter = collections.Counter()
            for s in spans:
                sizes[round(s["size"] * 2) / 2] += len(s["text"])
            bold_chars = sum(len(s["text"]) for s in spans if _is_bold(s))
            main_font = max(spans, key=lambda s: len(s["text"]))["font"].lower()
            lines.append(
                _Line(clean("".join(s["text"] for s in line["spans"])), sizes.most_common(1)[0][0],
                      bold_chars >= 0.9 * chars, bbox, block_no, page_no,
                      any(k in main_font for k in _WEAK_FONT_WORDS))
            )
    return _stitch_fragments(_drop_overlapping(lines), vocab)


def _overlap(a: tuple, b: tuple) -> float:
    """Intersection area as a share of the smaller box."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (w * h) / smaller if smaller > 0 else 0.0


def _stitch_fragments(lines: list[_Line], vocab: set[str]) -> list[_Line]:
    """Text that wraps around an image is sometimes stored as a second, separate fragment on the
    same baseline, immediately right of its line ("...pitta an" + "nd chapatti, wholewheat").
    Join such a fragment onto its line (collapsing the overlapping characters), or drop it when
    that line already contains it."""
    absorbed: set[int] = set()
    for i, f in enumerate(lines):
        fy = (f.bbox[1] + f.bbox[3]) / 2
        candidates = [
            (j, l) for j, l in enumerate(lines)
            if j != i and j not in absorbed and l.block_no != f.block_no
            and abs((l.bbox[1] + l.bbox[3]) / 2 - fy) < 3
            and l.bbox[0] < f.bbox[0] and 0 <= f.bbox[0] - l.bbox[2] + 12
            and f.bbox[0] - l.bbox[2] < 12
        ]
        if not candidates:
            continue
        j, left = max(candidates, key=lambda c: c[1].bbox[2])
        frag = f.text.strip()
        if frag.lower() in left.text.lower():
            absorbed.add(i)
            continue
        text = left.text.rstrip()
        for k in range(min(len(text), len(frag), 6), 0, -1):  # overlapping glyphs at the seam
            if text[-k:].lower() == frag[:k].lower():
                text, frag = text + frag[k:], ""
                break
        if frag:
            # A word split across the seam ("da" + "y,") is rejoined only if the document itself
            # uses the joined word and not the left stub; otherwise it is a normal word break.
            last, first = re.search(r"[A-Za-z]+$", text), re.match(r"[A-Za-z]+", frag)
            split_word = bool(last and first and (last.group() + first.group()).lower() in vocab
                              and last.group().lower() not in vocab)
            text += frag if split_word else " " + frag
        left.text = text
        left.bbox = (left.bbox[0], left.bbox[1], max(left.bbox[2], f.bbox[2]), left.bbox[3])
        absorbed.add(i)
    return [l for k, l in enumerate(lines) if k not in absorbed]


def _drop_overlapping(lines: list[_Line]) -> list[_Line]:
    """Some designed PDFs (Eatwell) carry clipped, partial duplicates of a text line that
    overlap the real line ("holegrain food includes:" over "Wholegrain food includes: ...").
    Where two lines overlap heavily, keep the longer one."""
    drop: set[int] = set()
    for i, a in enumerate(lines):
        for j in range(i + 1, len(lines)):
            b = lines[j]
            if i in drop or j in drop or _overlap(a.bbox, b.bbox) < 0.3:
                continue
            drop.add(i if len(a.text) < len(b.text) else j)
    return [l for k, l in enumerate(lines) if k not in drop]


def _table_rows(table) -> list[list[str]]:
    rows = [[clean(c or "") for c in row] for row in table.extract()]
    rows = [r for r in rows if any(r)]
    width = max((len(r) for r in rows), default=0)
    keep = [i for i in range(width) if any(i < len(r) and r[i] for r in rows)]  # drop spacer columns
    return [[r[i] if i < len(r) else "" for i in keep] for r in rows]


_MIN_TABLE_FILL = 0.5  # share of non-empty cells; sparser "tables" are layout boxes, not data


def _header_rows(rows: list[list[str]]) -> int:
    """Leading rows without any digit are header rows (1 to 3 of them)."""
    count = 0
    while count < min(3, len(rows) - 1) and not any(re.search(r"\d", c) for c in rows[count]):
        count += 1
    return max(count, 1)


def _is_chart_residue(rows: list[list[str]]) -> bool:
    """find_tables also latches onto chart panels: axis ticks ("12 11 10 9 8 7") or clipped fragments
    ("t Exceeding Limits of Added Sugars"). Those are not data tables."""
    cells = [c for r in rows for c in r if c]
    # A run of tick numbers inside one cell (real table cells hold one value each).
    tick_run = any(sum(1 for t in c.split() if re.fullmatch(r"[0-9]{1,2}", t)) >= 6 and len(c) > 40 for c in cells)
    fragments = len(rows) <= 3 and any(c[:1].islower() for c in cells)
    return tick_run or fragments


def _find_tables(page, min_fill: float = _MIN_TABLE_FILL) -> list[tuple[tuple, list[list[str]]]]:
    found = []
    try:
        finder = page.find_tables()
    except Exception:
        logger.exception("find_tables failed on page %d", page.number + 1)
        return found
    for table in finder.tables:
        rows = _table_rows(table)
        if len(rows) < 2 or len(rows[0]) < 2:
            continue  # not a grid
        filled = sum(1 for r in rows for c in r if c) / (len(rows) * len(rows[0]))
        if _is_chart_residue(rows):
            continue
        if filled >= min_fill:  # sparse hits are boxed text/layout; keep that as paragraphs
            found.append((tuple(table.bbox), rows))
    return found


def _merge_continuations(blocks: list[Block]) -> list[Block]:
    """Repair blocks split by layout: a lowercase-start paragraph right after an unfinished
    paragraph/list item continues it, and a short heading directly followed by a same-level
    heading ("Guideline 2" + its title) is one heading."""
    out: list[Block] = []
    for block in blocks:
        prev = out[-1] if out else None
        if prev is not None and prev.page == block.page:
            if (block.kind == TEXT and prev.kind in (TEXT, LIST) and block.text[:1].islower()
                    and not prev.text.rstrip().endswith((".", "!", "?", ":", ";"))):
                prev.text = _join(prev.text.rstrip(), block.text)
                continue
            if (block.kind == HEADING and prev.kind == HEADING and block.level == prev.level
                    and len(prev.text) < 30 and not prev.text.endswith((".", ":"))):
                prev.text = prev.text + " " + block.text
                continue
        out.append(block)
    return out


def _join(a: str, b: str) -> str:
    return a + b if a.endswith("-") and b[:1].islower() else a + " " + b


def _paragraph(texts: list[str], page_no: int) -> Block:
    """One PDF text block -> a paragraph or a (possibly lead-in + bulleted) list."""
    items: list[str] = []
    is_list = False
    for t in texts:
        if _BULLET.match(t) or _NUMBERED.match(t):
            is_list = True
            items.append(t)
        elif items:
            items[-1] = _join(items[-1], t)
        else:
            items.append(t)
    if not is_list:
        return Block(TEXT, clean(" ".join(texts)), page=page_no)
    lines = ["- " + _BULLET.sub("", i, count=1) if _BULLET.match(i) else i for i in items]
    return Block(LIST, "\n".join(lines), page=page_no)


def extract_pymupdf(doc: Document) -> Extraction:
    import pymupdf

    pdf = pymupdf.open(doc.raw_path)
    pages = len(pdf)
    vocab = {w for page in pdf for w in re.findall(r"[a-z]+", page.get_text().lower())}
    all_lines: list[_Line] = []
    page_tables: dict[int, list] = {}
    page_text: dict[int, str] = {}
    page_h: dict[int, float] = {}
    for index, page in enumerate(pdf):
        n = index + 1
        page_h[n] = page.rect.height
        if n in doc.exclude_pages:
            page_tables[n] = []
            continue
        page_text[n] = page.get_text()
        page_tables[n] = _find_tables(page)
        all_lines.extend(_page_lines(page, n, [bbox for bbox, _ in page_tables[n]], vocab))

    # Drop running headers/footers (digits normalised so "Page 28"/"Page 29" match) and bare page numbers.
    def in_band(l: _Line) -> bool:
        return l.bbox[3] < 0.09 * page_h[l.page] or l.bbox[1] > 0.91 * page_h[l.page]

    def key(l: _Line) -> str:
        return re.sub(r"\d+", "#", l.text.lower())

    repeats = collections.Counter(key(l) for l in all_lines if in_band(l))
    min_repeat = 3  # odd/even pages alternate header formats, so each variant repeats only every 2nd page
    all_lines = [
        l for l in all_lines
        if not (in_band(l) and (repeats[key(l)] >= min_repeat or re.fullmatch(r"[\divxlc\s]{1,6}", l.text.lower())))
        and not (re.fullmatch(r"\d{1,3}", l.text) and (l.bbox[1] > 0.8 * page_h[l.page] or l.bbox[3] < 0.1 * page_h[l.page]))
    ]

    sizes: collections.Counter = collections.Counter()
    for l in all_lines:
        sizes[l.size] += len(l.text)
    body = sizes.most_common(1)[0][0] if sizes else 10.0

    # Running headers that the layout places mid-page (beside a figure, say) escape the top/bottom band:
    # drop long, body-sized lines whose text (digits normalised) recurs on many different pages.
    pages_with: dict[str, set[int]] = collections.defaultdict(set)
    for l in all_lines:
        pages_with[key(l)].add(l.page)
    min_pages = max(8, int(0.05 * pages))
    all_lines = [
        l for l in all_lines
        if not (len(l.text) >= 25 and l.size < body + 1.4 and len(pages_with[key(l)]) >= min_pages)
    ]

    # Blocks that open with a bullet are lists: their (often bold) continuation lines are not headings.
    list_blocks = {
        (l.page, l.block_no) for l in all_lines if _BULLET.match(l.text) or _NUMBERED.match(l.text)
    }

    # Some pages set their body text larger than the rest of the document (e.g. a goals list at
    # 16pt); a page's own dominant size is its body size if it clearly dominates the page.
    page_sizes: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    page_lines: dict[int, int] = collections.Counter()
    for l in all_lines:
        page_sizes[l.page][l.size] += len(l.text)
        page_lines[l.page] += 1
    page_body: dict[int, float] = {}
    for pg, counter in page_sizes.items():
        size, chars = counter.most_common(1)[0]
        dominant = chars >= 0.6 * sum(counter.values()) and chars >= 200 and page_lines[pg] >= 5
        page_body[pg] = max(body, size) if dominant else body

    block_lines: collections.Counter = collections.Counter((l.page, l.block_no) for l in all_lines)
    block_bold: collections.Counter = collections.Counter((l.page, l.block_no) for l in all_lines if l.bold)

    def is_heading(l: _Line) -> bool:
        if len(l.text) > 120 or _BULLET.match(l.text) or _NUMBERED.match(l.text):
            return False
        if (l.page, l.block_no) in list_blocks:
            return False
        letters = sum(c.isalpha() for c in l.text)
        if letters < 3 or letters < 0.6 * len(l.text):
            return False  # figure/table labels such as "2%" or "184 Total"
        pb = page_body[l.page]
        if l.size >= pb + 1.4:
            # Large type is a heading only if it is bold (or of unknown weight): chapter intros
            # are often set large in a light face and must stay paragraphs.
            return l.bold or not l.weak_font
        key = (l.page, l.block_no)
        return (
            l.bold and block_bold[key] == block_lines[key] and block_lines[key] <= 3
            and not l.text[:1].islower() and l.size >= pb - 0.5 and len(l.text) <= 90
            and not l.text.endswith((".", ",", ";"))
        )

    def is_sized(l: _Line) -> bool:
        return l.size >= page_body[l.page] + 1.4

    heading_sizes = sorted({l.size for l in all_lines if is_heading(l) and is_sized(l)}, reverse=True)

    def level_of(l: _Line) -> int:
        if is_sized(l):
            return min(heading_sizes.index(l.size) + 1, 6)
        return min(len(heading_sizes) + 1, 6)  # bold body-size line: below every sized heading

    by_page: dict[int, list[_Line]] = collections.defaultdict(list)
    for l in all_lines:
        by_page[l.page].append(l)

    blocks: list[Block] = []
    for n in range(1, pages + 1):
        stream: list[tuple[float, Block]] = []
        pending: Optional[dict] = None

        def flush() -> None:
            nonlocal pending
            if pending is None:
                return
            if pending["heading"]:
                block = Block(HEADING, clean(" ".join(pending["texts"])), level=pending["level"], page=n)
            else:
                block = _paragraph(pending["texts"], n)
            stream.append((pending["y"], block))
            pending = None

        for l in by_page.get(n, []):
            heading = is_heading(l)
            level = level_of(l) if heading else 0
            if pending and pending["heading"] == heading and pending["level"] == level and (
                pending["block_no"] == l.block_no or (heading and abs(l.bbox[1] - pending["last_y"]) < 2.5 * l.size)
            ):
                pending["texts"].append(l.text)
                pending["last_y"] = l.bbox[1]
                continue
            flush()
            pending = {"heading": heading, "level": level, "block_no": l.block_no,
                       "y": l.bbox[1], "last_y": l.bbox[1], "texts": [l.text]}
        flush()

        # Tables go in at their vertical position among the stream-ordered blocks.
        for bbox, rows in page_tables[n]:
            at = next((i for i, (y, _) in enumerate(stream) if y > bbox[1]), len(stream))
            stream.insert(at, (bbox[1], Block(TABLE, rows=rows, page=n, header_rows=_header_rows(rows))))
        blocks.extend(b for _, b in stream)
    blocks = _merge_continuations(blocks)

    return _finish(doc, "pymupdf", pages, blocks, page_text)


# ── Docling (EFSA) ───────────────────────────────────────────────────────────
def _merge_header_rows(grid: list[list[str]], n_head: int) -> list[list[str]]:
    """Merge ``n_head`` stacked header rows into one, de-duplicating the repeats column spans produce."""
    if n_head == 0:
        return grid
    header = []
    for col in range(len(grid[0])):
        parts: list[str] = []
        for row in grid[:n_head]:
            if row[col] and row[col] not in parts:
                parts.append(row[col])
        header.append(" - ".join(parts))
    return [header] + grid[n_head:]


def _docling_table_rows(item) -> list[list[str]]:
    """Rows from Docling's cell grid, with stacked header rows (``column_header``) merged into one."""
    grid = [[clean(cell.text) for cell in row] for row in item.data.grid]
    is_header = [any(cell.column_header for cell in row) for row in item.data.grid]
    n_head = 0
    # A row with a bare number (8, 2.4) is data even if Docling flagged it as header; rotated column
    # titles in the EFSA mineral tables make it do that for their first data row.
    while n_head < len(grid) and is_header[n_head] and not any(_PURE_NUMBER.match(c) for c in grid[n_head]):
        n_head += 1
    return _collapse_spans(_merge_header_rows(grid, n_head))


def _collapse_spans(rows: list[list[str]]) -> list[list[str]]:
    """A section row ("Pregnancy") that spans the whole table comes back with its text repeated in every
    cell; keep it once, in the first cell."""
    out = []
    for row in rows:
        filled = [c for c in row if c]
        if len(filled) >= 3 and len(set(filled)) == 1:
            row = [filled[0]] + [""] * (len(row) - 1)
        out.append(row)
    return out


_MERGED_NUMBERS = re.compile(r"^\S*[0-9]\S*\s+\S*[0-9]\S*$")


def suspicious_cells(rows: list[list[str]]) -> int:
    """Cells that look like two table cells fused into one ("2.4 3.6"), plus bare numbers in the header
    row: the signatures of a table structure that was segmented wrongly."""
    fused = sum(1 for row in rows[1:] for c in row if _MERGED_NUMBERS.match(c))
    return fused + sum(1 for c in rows[0] if _PURE_NUMBER.match(c))


def repair_fused_cells(rows: list[list[str]]) -> list[list[str]]:
    """Undo a Docling error seen on the EFSA mineral/vitamin tables: two neighbouring cells come back fused
    ("160 1.5", "7-11 1-3", "2.4 3.6"). Two patterns are repaired, both conservatively:

    * the cell to the right is empty        -> the fused cell is two cells ("160 1.5" -> "160", "1.5")
    * the cell above is empty or text-only  -> the first token belongs to the row above (it was pulled
                                               down by the mis-segmentation); the second token stays

    Anything else is left untouched (and is flagged by ``suspicious_cells`` in the report)."""
    rows = [list(r) for r in rows]
    for r in range(1, len(rows)):
        for c, cell in enumerate(rows[r]):
            if not _MERGED_NUMBERS.match(cell):
                continue
            first, second = cell.split()
            if c + 1 < len(rows[r]) and not rows[r][c + 1]:
                rows[r][c], rows[r][c + 1] = first, second
            elif r >= 2 and not _PURE_NUMBER.match(rows[r - 1][c]) and not _MERGED_NUMBERS.match(rows[r - 1][c])                     and not re.match(r"^[0-9]", rows[r - 1][c]):
                above = rows[r - 1][c]
                rows[r - 1][c] = (first + " " + above).strip() if above else first
                rows[r][c] = second
    return rows


def _docling_document(doc: Document):
    """The converted DoclingDocument, cached as JSON: conversion takes minutes, parsing it takes seconds."""
    from docling_core.types.doc import DoclingDocument

    if doc.docling_path.exists():
        return DoclingDocument.load_from_json(doc.docling_path)
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = False  # text-layer PDF: OCR would only add noise
    options.do_table_structure = True
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    dl = converter.convert(str(doc.raw_path)).document
    doc.docling_path.parent.mkdir(parents=True, exist_ok=True)
    dl.save_as_json(doc.docling_path)
    return dl


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def recover_missing_text(blocks: list[Block], pdf, exclude_pages: tuple[int, ...] = ()) -> tuple[list[Block], int]:
    """Docling can swallow text it mistakes for a picture (e.g. footnotes that spill onto the next page).
    Every PDF text-layer line that is not represented in the extracted blocks of its page is appended
    to that page as a paragraph, so no source text is lost silently. Running headers (a line seen on
    three or more pages) and page numbers are not recovered."""
    covered: dict[int, str] = collections.defaultdict(str)
    for b in blocks:
        covered[b.page] += _norm(" ".join([b.text, b.caption or ""] + [c for r in b.rows for c in r]))
    page_lines = {i + 1: [l.strip() for l in p.get_text().splitlines() if l.strip()] for i, p in enumerate(pdf)}
    seen = collections.Counter(_norm(l) for lines in page_lines.values() for l in set(lines))
    recovered: dict[int, list[str]] = {}
    for page, lines in page_lines.items():
        if page in exclude_pages:
            continue
        missing = [
            l for l in lines
            if len(_norm(l)) >= 12 and seen[_norm(l)] < 3 and _norm(l) not in covered[page]
        ]
        if missing:
            recovered[page] = missing
    out = list(blocks)
    for page in sorted(recovered):
        last = max((i for i, b in enumerate(out) if b.page <= page), default=-1)
        out.insert(last + 1, Block(TEXT, clean(" ".join(recovered[page])), page=page))
    return out, sum(len(v) for v in recovered.values())


def extract_docling(doc: Document) -> Extraction:
    import pymupdf

    dl = _docling_document(doc)
    pdf = pymupdf.open(doc.raw_path)
    blocks: list[Block] = []
    page = 1
    for item, _ in dl.iterate_items():
        kind = type(item).__name__
        if item.prov:
            page = item.prov[0].page_no
        label = str(getattr(item, "label", "")).lower()
        if kind == "TableItem":
            rows = repair_fused_cells(_docling_table_rows(item))
            if rows:
                caption = clean(item.caption_text(dl)) or None
                blocks.append(Block(TABLE, rows=rows, page=page, caption=caption))
        elif kind == "SectionHeaderItem":
            blocks.append(Block(HEADING, clean(item.text), level=max(1, int(item.level)), page=page))
        elif kind == "ListItem":
            blocks.append(Block(LIST, "- " + clean(item.text), page=page))
        elif kind == "TextItem":
            if "page_header" in label or "page_footer" in label or "caption" in label:
                continue  # captions are carried by their table
            blocks.append(Block(TEXT, clean(item.text), page=page))

    blocks, recovered = recover_missing_text(blocks, pdf, doc.exclude_pages)
    result = _finish(doc, "docling", len(pdf), blocks, {i + 1: p.get_text() for i, p in enumerate(pdf)})
    if recovered:
        result.warnings.append(f"{doc.id}: recovered {recovered} text line(s) that Docling did not extract")
    return result


def extract_fda(doc: Document) -> Extraction:
    import pymupdf

    pdf = pymupdf.open(doc.raw_path)
    blocks = fda_chart.extract_blocks(pdf[0])
    return _finish(doc, "fda_chart", len(pdf), blocks, {i + 1: p.get_text() for i, p in enumerate(pdf)})


def extract_macros(doc: Document) -> Extraction:
    import pymupdf

    pdf = pymupdf.open(doc.raw_path)
    blocks, warnings = macros_table.extract_blocks(pdf)
    result = _finish(doc, "macros_table", len(pdf), blocks, {i + 1: p.get_text() for i, p in enumerate(pdf)})
    result.warnings.extend(f"{doc.id}: {w}" for w in warnings)
    return result


EXTRACTORS = {
    "html": extract_html,
    "pymupdf": extract_pymupdf,
    "docling": extract_docling,
    "fda_chart": extract_fda,
    "macros_table": extract_macros,
}


def extract(doc: Document, use_cache: bool = True) -> Extraction:
    if use_cache and doc.extracted_path.exists():
        return Extraction.from_dict(json.loads(doc.extracted_path.read_text(encoding="utf-8")))
    if not doc.raw_path.exists():
        raise FileNotFoundError(f"{doc.raw_path} missing; run ingestion.download_documents first")
    logger.info("extracting %s with %s", doc.id, doc.extractor)
    result = EXTRACTORS[doc.extractor](doc)
    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    doc.extracted_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="*", help="document ids (default: all)")
    parser.add_argument("--refresh", action="store_true", help="ignore cached extractions")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for doc in load_registry(args.only):
        result = extract(doc, use_cache=not args.refresh)
        kinds = collections.Counter(b.kind for b in result.blocks)
        print(f"{doc.id}: {result.pages} pages, blocks={dict(kinds)}, empty={result.empty_pages}, garbled={result.garbled_pages}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

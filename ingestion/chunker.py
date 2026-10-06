"""Chunking: structured blocks -> metadata-rich chunks (Architecture §6).

* Recursive splitting, 512 tokens / 64 overlap, token counts from the embedding model's own
  tokenizer (injected as ``count_tokens``).
* Chunks never span a heading: ``section`` is exact for every chunk.
* Tables and lists are atomic units. One that fits the budget is a single chunk; one that does
  not is split *deliberately* on row / item boundaries (never mid-row), repeating the table header
  or list lead-in in each part, so each part stays self-contained.
* The embedded input is ``"<section>\\n<text>"`` (the heading disambiguates chunks such as
  "less than 10 percent of calories", which never name their topic). The 512-token budget covers
  that whole input so nothing is silently truncated by the model; the stored ``text`` stays raw.
"""

import dataclasses
import re
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

from ingestion.common import CHUNK_OVERLAP_TOKENS, CHUNK_SIZE_TOKENS, Document
from ingestion.models import HEADING, LIST, TABLE, TEXT, Block, Extraction

from integrations.vector_store import ChunkRecord  # noqa: E402  (backend on sys.path via ingestion.common)

CountTokens = Callable[[str], int]

_SEPARATORS = ("\n\n", "\n", ". ", "; ", ", ", " ")
_FOOTNOTE = re.compile(r"^\(?[a-z]\)\s")  # "(d) The PRI covers ..." - a table note that spilled onto the next page
_ITEM = re.compile(r"^(?:- |\d{1,2}[.)] )")
_MAX_HEADING_CHARS = 100
_MAX_TITLE_HEADING_CHARS = 60  # an ancestor heading is added to a table title only if short
SPECIAL_TOKENS = 2  # [CLS] + [SEP]
MIN_ALPHA_CHARS = 8  # chunks with fewer letters than this are layout residue ("6", "Page 3")


@dataclass
class RawChunk:
    section: str
    text: str
    page: int
    kind: str  # text | list | table


# ── sections ─────────────────────────────────────────────────────────────────
def _short(text: str) -> str:
    text = text.strip()
    if len(text) <= _MAX_HEADING_CHARS:
        return text
    return text[: _MAX_HEADING_CHARS - 1].rsplit(" ", 1)[0] + "…"


def section_label(stack: list[tuple[int, str]]) -> str:
    """Innermost two headings, e.g. 'WHO guidance on healthy diets > Fats'."""
    return " > ".join(_short(t) for _, t in stack[-2:]) or "Document"


# ── recursive text splitter ──────────────────────────────────────────────────
def split_text(text: str, count: CountTokens, size: int, overlap: int) -> list[str]:
    """Split into pieces of at most ``size`` tokens, with ``overlap`` tokens carried between
    consecutive pieces. Prefers paragraph, then line, then sentence boundaries."""
    text = text.strip()
    if not text:
        return []
    if count(text) <= size:
        return [text]
    return _split(text, count, size, overlap, _SEPARATORS)


def _split(text: str, count: CountTokens, size: int, overlap: int, seps: tuple) -> list[str]:
    if count(text) <= size:
        return [text]
    sep = next((s for s in seps if s in text), None)
    if sep is None:  # one unbroken token run: cut by characters
        return _hard_split(text, count, size)
    rest = seps[seps.index(sep) + 1:]
    pieces: list[str] = []
    parts = text.split(sep)
    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue
        # Sentence/clause punctuation stays on the left piece so each piece reads naturally.
        if i < len(parts) - 1 and sep.strip():
            part += sep.strip()
        if count(part) > size:
            pieces.extend(_split(part, count, size, overlap, rest))
        else:
            pieces.append(part)
    joiner = "\n\n" if sep == "\n\n" else "\n" if sep == "\n" else " "
    return _merge(pieces, joiner, count, size, overlap)


def _merge(pieces: list[str], joiner: str, count: CountTokens, size: int, overlap: int) -> list[str]:
    # Whitespace joiners cost ~0 tokens in a WordPiece tokenizer; the final verification below
    # catches any drift from the additive estimate.
    chunks: list[str] = []
    window: list[str] = []
    window_tokens = 0
    fresh = 0  # pieces in the window that are new (not carried-over overlap)
    for piece in pieces:
        n = count(piece)
        if window and window_tokens + n > size:
            if fresh:
                chunks.append(joiner.join(window))
            # carry trailing pieces (<= overlap tokens) into the next chunk
            keep: list[str] = []
            kept = 0
            for prev in reversed(window):
                c = count(prev)
                if kept + c > overlap:
                    break
                keep.insert(0, prev)
                kept += c
            window, window_tokens, fresh = keep, kept, 0
            while window and window_tokens + n > size:  # the overlap must leave room for this piece
                window_tokens -= count(window.pop(0))
        window.append(piece)
        window_tokens += n
        fresh += 1
    if window and fresh:
        chunks.append(joiner.join(window))
    # Additive token estimates can be off at the joins: verify, and split further if needed.
    verified: list[str] = []
    for chunk in chunks:
        verified.extend([chunk] if count(chunk) <= size else _hard_split(chunk, count, size))
    return verified


def _hard_split(text: str, count: CountTokens, size: int) -> list[str]:
    """Last resort for text with no usable separator: halve until it fits."""
    if count(text) <= size or len(text) < 2:
        return [text]
    words = text.split(" ")
    if len(words) > 1:
        mid = len(words) // 2
        halves = [" ".join(words[:mid]), " ".join(words[mid:])]
    else:
        mid = len(text) // 2
        halves = [text[:mid], text[mid:]]
    return [piece for half in halves for piece in _hard_split(half, count, size)]


# ── atomic units ─────────────────────────────────────────────────────────────
def _cell(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ").strip()


def render_row(cells: list[str]) -> str:
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def table_parts(block: Block, title: str, count: CountTokens, budget: int) -> list[str]:
    """Render a table as markdown, split on row boundaries if it exceeds the budget."""
    rows = block.rows
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    n_head = min(max(block.header_rows, 1), len(rows))
    head = [render_row(r) for r in rows[:n_head]] + ["| " + " | ".join(["---"] * width) + " |"]
    prefix = ([title] if title else []) + head
    body: list[str] = []
    for row in rows[n_head:]:
        body.extend(_fit_row(row, count, max(budget - count("\n".join(prefix)) - 1, 8)))
    whole = "\n".join(prefix + body)
    if count(whole) <= budget or not body:
        return [whole]
    parts: list[str] = []
    group: list[str] = []
    base = count("\n".join(prefix))
    used = base
    for line in body:
        n = count(line) + 1
        if group and used + n > budget:
            parts.append("\n".join(prefix + group))
            group, used = [], base
        group.append(line)
        used += n
    if group:
        parts.append("\n".join(prefix + group))
    return parts


def _fit_row(row: list[str], count: CountTokens, budget: int) -> list[str]:
    """One rendered row, or - if a single row cannot fit a chunk (a cell full of URLs) - the row split
    across several lines by cutting its longest cell; the other cells stay on the first line."""
    line = render_row(row)
    if count(line) <= budget:
        return [line]
    longest = max(range(len(row)), key=lambda i: count(row[i]))
    others = sum(count(c) for i, c in enumerate(row) if i != longest) + len(row) * 2
    pieces = split_text(row[longest], count, max(budget - others, 16), 0)
    lines = []
    for n, piece in enumerate(pieces):
        cells = [c if n == 0 else "" for c in row]
        cells[longest] = piece
        lines.append(render_row(cells))
    return lines


def list_parts(text: str, count: CountTokens, budget: int, overlap: int) -> list[str]:
    """Split a list on item boundaries, repeating a lead-in line ("The following:") in each part."""
    if count(text) <= budget:
        return [text]
    lines = text.split("\n")
    lead = lines[0] if not _ITEM.match(lines[0]) else ""
    items: list[str] = []
    for line in lines[1 if lead else 0:]:
        if _ITEM.match(line) or not items:
            items.append(line)
        else:
            items[-1] += " " + line
    parts: list[str] = []
    group: list[str] = []
    base = count(lead) + 1 if lead else 0
    used = base
    for item in items:
        n = count(item) + 1
        if n > budget - base:  # a single item larger than the budget: split its text
            if group:
                parts.append("\n".join(([lead] if lead else []) + group))
                group, used = [], base
            parts.extend(
                "\n".join(([lead] if lead else []) + [piece])
                for piece in split_text(item, count, budget - base, overlap)
            )
            continue
        if group and used + n > budget:
            parts.append("\n".join(([lead] if lead else []) + group))
            group, used = [], base
        group.append(item)
        used += n
    if group:
        parts.append("\n".join(([lead] if lead else []) + group))
    return parts


# ── driver ───────────────────────────────────────────────────────────────────
def build_chunks(
    extraction: Extraction,
    count: CountTokens,
    size: int = CHUNK_SIZE_TOKENS,
    overlap: int = CHUNK_OVERLAP_TOKENS,
    embed_section: bool = True,
) -> list[RawChunk]:
    """Walk the blocks in order and emit chunks. ``size`` bounds the embedded input
    (section + text) when ``embed_section`` is set."""
    chunks: list[RawChunk] = []
    stack: list[tuple[int, str]] = []
    run: list[Block] = []  # consecutive paragraphs of the current section
    run_section: Optional[str] = None  # overrides the heading path for notes that follow a table
    last_table: Optional[Block] = None  # the table just emitted, while nothing else has intervened

    def budget_for(section: str) -> int:
        # [CLS] and [SEP] count against the model's input limit; so does the section prefix, when embedded.
        return size - SPECIAL_TOKENS - (count(section + "\n") if embed_section else 0)

    def flush_run() -> None:
        nonlocal run, run_section
        if not run:
            return
        section = run_section or section_label(stack)
        text = "\n\n".join(b.text for b in run)
        for piece in split_text(text, count, budget_for(section), overlap):
            chunks.append(RawChunk(section, piece, run[0].page, TEXT))
        run, run_section = [], None

    for block in extraction.blocks:
        if block.kind == HEADING:
            flush_run()
            last_table = None
            while stack and stack[-1][0] >= block.level:
                stack.pop()
            stack.append((block.level, block.text))
        elif block.kind == TEXT:
            if last_table is not None and not run and (last_table.page == block.page or _FOOTNOTE.match(block.text)):
                # Footnotes and notes right after a table belong to that table.
                run_section = f"{last_table.caption} (notes)" if last_table.caption else None
            run.append(block)
        elif block.kind == LIST:
            after_table = last_table if not run else None
            flush_run()
            section = section_label(stack)
            if after_table is not None and after_table.caption and (
                after_table.page == block.page or _FOOTNOTE.match(block.text.lstrip("- "))
            ):
                section = f"{after_table.caption} (notes)"  # a table's footnote list
            last_table = None
            for part in list_parts(block.text, count, budget_for(section), overlap):
                chunks.append(RawChunk(section, part, block.page, LIST))
        elif block.kind == TABLE:
            flush_run()
            if not block.rows:
                continue
            caption = block.caption
            if not caption and last_table is not None and last_table.page == block.page and last_table.caption:
                caption = f"{_base_caption(last_table.caption)} (continued)"  # e.g. pregnancy rows split off a table
            block = dataclasses.replace(block, caption=caption)
            section = caption or section_label(stack)
            title = _table_title(caption, stack)
            for part in table_parts(block, title, count, budget_for(section)):
                chunks.append(RawChunk(section, part, block.page, TABLE))
            last_table = block
    flush_run()
    return [c for c in chunks if sum(ch.isalpha() for ch in c.text) >= MIN_ALPHA_CHARS]


def _base_caption(caption: str) -> str:
    return caption.removesuffix(" (continued)")


def _table_title(caption: Optional[str], stack: list[tuple[int, str]]) -> str:
    """First line of a table chunk: its caption, preceded by a short ancestor heading."""
    parent = stack[-1][1] if stack else ""
    if caption and parent and len(parent) <= _MAX_TITLE_HEADING_CHARS and parent != caption:
        return f"{parent} — {caption}"
    return caption or ""


def embed_input(chunk: RawChunk, embed_section: bool = True) -> str:
    return f"{chunk.section}\n{chunk.text}" if embed_section else chunk.text


def to_records(doc: Document, chunks: list[RawChunk], retrieval_date: date) -> list[ChunkRecord]:
    """Attach the Architecture §6 metadata. chunk_id is deterministic: <doc id>_chunk_<index>."""
    total = len(chunks)
    width = max(3, len(str(max(total - 1, 0))))
    return [
        ChunkRecord(
            chunk_id=f"{doc.id}_chunk_{i:0{width}d}",
            document_name=doc.document_name,
            publisher=doc.publisher,
            year=doc.year,
            source_url=doc.source_url,
            retrieval_date=retrieval_date.isoformat(),
            section=c.section,
            chunk_index=i,
            total_chunks=total,
            text=c.text,
        )
        for i, c in enumerate(chunks)
    ]

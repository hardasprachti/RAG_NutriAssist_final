"""Intermediate representation shared by the extractors and the chunker."""

from dataclasses import asdict, dataclass, field
from typing import Optional

HEADING, TEXT, LIST, TABLE = "heading", "text", "list", "table"


@dataclass
class Block:
    """One structural unit of a source document, in reading order.

    kind         heading | text | list | table
    text         heading/paragraph text, or list items joined by newlines ("- item")
    level        heading depth (1 = top); 0 for non-headings
    page         1-based page number (HTML sources are page 1)
    rows         table cells; the first ``header_rows`` rows are the header
    header_rows  how many leading rows of ``rows`` are header rows
    caption      table caption / title; becomes the section of the table's chunks
    """

    kind: str
    text: str = ""
    level: int = 0
    page: int = 1
    rows: list[list[str]] = field(default_factory=list)
    header_rows: int = 1
    caption: Optional[str] = None


@dataclass
class Extraction:
    doc_id: str
    extractor: str
    pages: int
    blocks: list[Block]
    empty_pages: list[int] = field(default_factory=list)
    excluded_pages: list[int] = field(default_factory=list)
    garbled_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Extraction":
        data = dict(data)
        data["blocks"] = [Block(**b) for b in data["blocks"]]
        return cls(**data)

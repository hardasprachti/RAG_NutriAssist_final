"""Shared paths, registry loading and the backend import shim for the ingestion scripts.

Run everything from the repository root, e.g. ``python -m ingestion.run_ingestion``.
The ingestion reuses the backend's embedder, vector store and DB client so ingestion and
query time can never drift apart (same model, normalisation, payload schema).
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

INGESTION_DIR = REPO_ROOT / "ingestion"
DATA_DIR = INGESTION_DIR / "data"  # git-ignored; re-creatable via download_documents.py
RAW_DIR = DATA_DIR / "raw"
EXTRACTED_DIR = DATA_DIR / "extracted"
DOCLING_DIR = DATA_DIR / "docling"
CHUNKS_DIR = DATA_DIR / "chunks"
MANIFEST_PATH = DATA_DIR / "manifest.json"
REGISTRY_PATH = INGESTION_DIR / "document_registry.json"
REPORT_PATH = REPO_ROOT / "docs" / "ingestion_report.md"

# 512 tokens / 64 overlap, Architecture §6.
CHUNK_SIZE_TOKENS = 512
CHUNK_OVERLAP_TOKENS = 64


@dataclass(frozen=True)
class Document:
    id: str
    document_name: str
    publisher: str
    year: int
    source_url: str
    format: str  # "pdf" | "html"
    extractor: str  # "pymupdf" | "docling" | "html"
    download_url: Optional[str] = None
    exclude_pages: tuple[int, ...] = ()

    @property
    def fetch_url(self) -> str:
        return self.download_url or self.source_url

    @property
    def raw_path(self) -> Path:
        return RAW_DIR / f"{self.id}.{self.format}"

    @property
    def extracted_path(self) -> Path:
        return EXTRACTED_DIR / f"{self.id}.json"

    @property
    def docling_path(self) -> Path:
        return DOCLING_DIR / f"{self.id}.json"

    @property
    def chunks_path(self) -> Path:
        return CHUNKS_DIR / f"{self.id}.jsonl"


def load_registry(only: Optional[list[str]] = None) -> list[Document]:
    raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["documents"]
    docs = [
        Document(**{k: (tuple(v) if k == "exclude_pages" else v) for k, v in d.items() if not k.startswith("_")})
        for d in raw
    ]
    if only:
        unknown = set(only) - {d.id for d in docs}
        if unknown:
            raise SystemExit(f"unknown document id(s): {sorted(unknown)}")
        docs = [d for d in docs if d.id in only]
    return docs


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {}

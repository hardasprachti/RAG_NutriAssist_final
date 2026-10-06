"""Idempotent upsert of embedded chunks into the vector store, plus the document_metadata rows.

Point ids are derived from the deterministic ``chunk_id`` (see ``VectorStore.point_id``), and a
document's previous points are removed before its new ones are written, so re-running ingestion
never duplicates and never leaves stale chunks behind when the chunk count changes.
"""

import logging
from datetime import date
from typing import Optional, Sequence

from ingestion.common import Document

from integrations.vector_store import ChunkRecord, VectorStore  # noqa: E402  (backend on sys.path)

logger = logging.getLogger("ingestion.upload")

BATCH = 128


class InvalidChunk(ValueError):
    pass


def validate_records(records: Sequence[ChunkRecord]) -> None:
    """Every chunk must carry complete Architecture §6 metadata; ids must be unique."""
    seen: set[str] = set()
    for r in records:
        for name in ("chunk_id", "document_name", "publisher", "source_url", "retrieval_date", "section", "text"):
            if not str(getattr(r, name) or "").strip():
                raise InvalidChunk(f"{r.chunk_id or '<no id>'}: empty {name}")
        if not isinstance(r.year, int) or not (1900 <= r.year <= 2100):
            raise InvalidChunk(f"{r.chunk_id}: bad year {r.year!r}")
        if r.chunk_id in seen:
            raise InvalidChunk(f"duplicate chunk_id {r.chunk_id}")
        seen.add(r.chunk_id)
        if r.total_chunks != len(records) or not (0 <= r.chunk_index < r.total_chunks):
            raise InvalidChunk(f"{r.chunk_id}: inconsistent chunk_index/total_chunks")


def make_store(qdrant_path: Optional[str] = None) -> VectorStore:
    """The configured Qdrant (QDRANT_URL), or an embedded local store at ``qdrant_path``."""
    if qdrant_path:
        from qdrant_client import QdrantClient

        from config import get_settings

        settings = get_settings()
        return VectorStore(QdrantClient(path=qdrant_path), settings.qdrant_collection_name, settings.embedding_dim)
    return VectorStore.from_settings()


def upload_document(
    store: VectorStore, records: Sequence[ChunkRecord], vectors: Sequence[Sequence[float]]
) -> None:
    validate_records(records)
    store.ensure_collection()
    store.delete_document(records[0].document_name)
    for i in range(0, len(records), BATCH):
        store.upsert(records[i : i + BATCH], vectors[i : i + BATCH])
    logger.info("upserted %d chunks of %s", len(records), records[0].document_name)


def register_document(doc: Document, total_chunks: int, retrieval_date: date, embedding_model: str) -> None:
    """Write the document_metadata row (upsert by source_url)."""
    from integrations.db_client import session_scope, upsert_document_metadata

    with session_scope() as session:
        upsert_document_metadata(
            session,
            document_name=doc.document_name,
            publisher=doc.publisher,
            year=doc.year,
            source_url=doc.source_url,
            retrieval_date=retrieval_date,
            total_chunks=total_chunks,
            embedding_model=embedding_model,
        )

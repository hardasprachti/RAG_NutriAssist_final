"""Idempotence and metadata for the upload step, on an in-memory Qdrant and a SQLite database."""

import contextlib
from datetime import date

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import integrations.db_client as db_client
from integrations.vector_store import VectorStore
from models.db_models import Base, DocumentMetadata

from ingestion.chunker import RawChunk, to_records
from ingestion.common import Document
from ingestion.upload_to_vectorstore import register_document, upload_document

DIM = 4


def make_doc(doc_id: str, name: str, url: str) -> Document:
    return Document(doc_id, name, "Publisher", 2020, url, "html", "html")


DOC_A = make_doc("doc_a", "Doc A", "https://example.org/a")
DOC_B = make_doc("doc_b", "Doc B", "https://example.org/b")


def records(doc: Document, n: int):
    chunks = [RawChunk(f"Section {i}", f"text of chunk {i} of {doc.id}", 1, "text") for i in range(n)]
    return to_records(doc, chunks, date(2026, 10, 6))


def vectors(n: int, seed: int = 0):
    return [[1.0, float(i + seed), 0.0, 0.5] for i in range(n)]


@pytest.fixture
def store():
    s = VectorStore(QdrantClient(":memory:"), "test_chunks", DIM)
    yield s


def test_reingesting_the_same_document_never_duplicates(store):
    upload_document(store, records(DOC_A, 5), vectors(5))
    upload_document(store, records(DOC_A, 5), vectors(5))
    assert store.count() == 5


def test_a_shorter_reingest_removes_stale_chunks(store):
    upload_document(store, records(DOC_A, 6), vectors(6))
    upload_document(store, records(DOC_A, 4), vectors(4))
    assert store.count() == 4
    ids = {h.chunk.chunk_id for h in store.search([1.0, 0.0, 0.0, 0.5], k=10)}
    assert ids == {f"doc_a_chunk_00{i}" for i in range(4)}


def test_documents_are_independent(store):
    upload_document(store, records(DOC_A, 3), vectors(3))
    upload_document(store, records(DOC_B, 2), vectors(2))
    upload_document(store, records(DOC_A, 3), vectors(3))  # re-ingesting A leaves B alone
    assert store.count() == 5
    only_b = store.search([1.0, 0.0, 0.0, 0.5], k=10, document_filter="Doc B")
    assert len(only_b) == 2 and all(h.chunk.document_name == "Doc B" for h in only_b)


def test_stored_payload_carries_the_architecture_metadata(store):
    upload_document(store, records(DOC_A, 2), vectors(2))
    hit = store.search([1.0, 0.0, 0.0, 0.5], k=1)[0].chunk
    assert (hit.document_name, hit.publisher, hit.year, hit.source_url) == ("Doc A", "Publisher", 2020, DOC_A.source_url)
    assert hit.retrieval_date == "2026-10-06" and hit.section.startswith("Section ") and hit.total_chunks == 2


def test_invalid_records_are_refused_before_anything_is_written(store):
    bad = records(DOC_A, 3)
    bad[1] = type(bad[1])(**{**bad[1].__dict__, "section": ""})
    with pytest.raises(ValueError, match="empty section"):
        upload_document(store, bad, vectors(3))
    assert not store._client.collection_exists("test_chunks") or store.count() == 0


def test_document_metadata_rows_are_upserted_not_duplicated(tmp_path, monkeypatch):
    engine = db_client.create_db_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def scope():
        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    monkeypatch.setattr(db_client, "session_scope", scope)
    register_document(DOC_A, 5, date(2026, 10, 6), "BAAI/bge-small-en-v1.5")
    register_document(DOC_A, 7, date(2026, 10, 7), "BAAI/bge-small-en-v1.5")
    register_document(DOC_B, 2, date(2026, 10, 6), "BAAI/bge-small-en-v1.5")
    with factory() as session:
        rows = session.scalars(select(DocumentMetadata).order_by(DocumentMetadata.document_name)).all()
    assert [(r.document_name, r.total_chunks, str(r.retrieval_date)) for r in rows] == [
        ("Doc A", 7, "2026-10-07"), ("Doc B", 2, "2026-10-06"),
    ]

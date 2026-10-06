import pytest
from qdrant_client import QdrantClient, models

from integrations.vector_store import (
    ChunkRecord,
    VectorStore,
    VectorStoreError,
    point_id,
)

WHO = "Healthy Diet Fact Sheet"
FDA = "Refrigerator & Freezer Storage Chart"


def chunk(chunk_id: str, document_name: str = WHO, publisher: str = "WHO", index: int = 0) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=chunk_id,
        document_name=document_name,
        publisher=publisher,
        year=2020,
        source_url=f"https://example.org/{document_name.replace(' ', '_')}",
        retrieval_date="2026-10-05",
        section="Fats",
        chunk_index=index,
        total_chunks=3,
        text=f"text of {chunk_id}",
    )


@pytest.fixture
def store(vector_store):
    vector_store.ensure_collection()
    return vector_store


# ── collection setup ─────────────────────────────────────────────────────────
def test_ensure_collection_creates_cosine_collection_and_is_idempotent(vector_store):
    vector_store.ensure_collection()
    vector_store.ensure_collection()  # second call must not fail or recreate

    params = vector_store._client.get_collection(vector_store.collection_name).config.params.vectors
    assert params.size == vector_store.dim
    assert params.distance == models.Distance.COSINE


def test_payload_indexes_exist_for_filter_fields(vector_store):
    if not vector_store.is_server:
        pytest.skip("payload indexes are not implemented by in-memory mode")
    vector_store.ensure_collection()
    schema = vector_store._client.get_collection(vector_store.collection_name).payload_schema
    assert schema["document_name"].data_type == models.PayloadSchemaType.KEYWORD
    assert schema["publisher"].data_type == models.PayloadSchemaType.KEYWORD
    assert schema["year"].data_type == models.PayloadSchemaType.INTEGER


def test_hnsw_config_is_set_on_server(vector_store):
    if not vector_store.is_server:
        pytest.skip("HNSW config is only reported by a real server")
    vector_store.ensure_collection()
    config = vector_store._client.get_collection(vector_store.collection_name).config
    assert config.hnsw_config.m == 16
    assert config.hnsw_config.ef_construct == 100


def test_ensure_collection_rejects_dimension_mismatch(vector_store):
    vector_store.ensure_collection()
    wrong = VectorStore(vector_store._client, vector_store.collection_name, vector_store.dim + 1)
    with pytest.raises(VectorStoreError, match="expected"):
        wrong.ensure_collection()


def test_ensure_collection_rejects_wrong_distance(vector_store):
    vector_store._client.create_collection(
        vector_store.collection_name,
        vectors_config=models.VectorParams(size=vector_store.dim, distance=models.Distance.DOT),
    )
    with pytest.raises(VectorStoreError, match="Cosine"):
        vector_store.ensure_collection()


# ── upsert → search ──────────────────────────────────────────────────────────
def test_upsert_then_search_returns_inserted_point_with_metadata(store):
    record = chunk("who_healthy_diet_chunk_007", index=7)
    store.upsert([record], [[1.0, 0.0, 0.0, 0.0]])

    (hit,) = store.search([1.0, 0.0, 0.0, 0.0], k=5)
    assert hit.chunk == record  # every metadata field round-trips, including text
    assert hit.score == pytest.approx(1.0)
    assert store.count() == 1


def test_search_orders_by_cosine_similarity_and_respects_k(store):
    store.upsert(
        [chunk("a"), chunk("b"), chunk("c")],
        [[1.0, 0.0, 0.0, 0.0], [0.9, 0.1, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
    )
    hits = store.search([1.0, 0.0, 0.0, 0.0], k=2)
    assert [h.chunk.chunk_id for h in hits] == ["a", "b"]
    assert hits[0].score > hits[1].score


def test_cosine_ignores_vector_magnitude(store):
    store.upsert([chunk("big")], [[10.0, 0.0, 0.0, 0.0]])
    (hit,) = store.search([0.5, 0.0, 0.0, 0.0])
    assert hit.score == pytest.approx(1.0)


def test_upsert_is_idempotent(store):
    record = chunk("who_healthy_diet_chunk_001")
    store.upsert([record], [[1.0, 0.0, 0.0, 0.0]])
    store.upsert([record], [[1.0, 0.0, 0.0, 0.0]])
    assert store.count() == 1


def test_upsert_same_chunk_id_replaces_content(store):
    store.upsert([chunk("x")], [[1.0, 0.0, 0.0, 0.0]])
    changed = ChunkRecord(**{**chunk("x").__dict__, "text": "revised text"})
    store.upsert([changed], [[0.0, 1.0, 0.0, 0.0]])

    assert store.count() == 1
    (hit,) = store.search([0.0, 1.0, 0.0, 0.0])
    assert hit.chunk.text == "revised text"


def test_upsert_validates_inputs(store):
    with pytest.raises(ValueError, match="vectors"):
        store.upsert([chunk("a"), chunk("b")], [[1.0, 0.0, 0.0, 0.0]])
    with pytest.raises(ValueError, match="dims"):
        store.upsert([chunk("a")], [[1.0, 0.0]])
    store.upsert([], [])  # no-op
    assert store.count() == 0


def test_search_rejects_wrong_query_dimension(store):
    with pytest.raises(ValueError, match="dims"):
        store.search([1.0, 0.0])


def test_point_id_is_deterministic_and_distinct():
    assert point_id("who_chunk_001") == point_id("who_chunk_001")
    assert point_id("who_chunk_001") != point_id("who_chunk_002")


# ── filtering ────────────────────────────────────────────────────────────────
@pytest.fixture
def two_documents(store):
    store.upsert(
        [
            chunk("who_1", WHO, "WHO"),
            chunk("who_2", WHO, "WHO"),
            chunk("fda_1", FDA, "FDA"),
        ],
        [[1.0, 0.0, 0.0, 0.0], [0.9, 0.1, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]],
    )
    return store


def test_search_without_filter_spans_documents(two_documents):
    names = {h.chunk.document_name for h in two_documents.search([1.0, 0.0, 0.0, 0.0], k=10)}
    assert names == {WHO, FDA}


def test_document_filter_restricts_results(two_documents):
    hits = two_documents.search([1.0, 0.0, 0.0, 0.0], k=10, document_filter=FDA)
    assert [h.chunk.chunk_id for h in hits] == ["fda_1"]


def test_document_filter_accepts_multiple_names(two_documents):
    hits = two_documents.search([1.0, 0.0, 0.0, 0.0], k=10, document_filter=[WHO, FDA])
    assert len(hits) == 3


def test_publisher_filter_restricts_results(two_documents):
    hits = two_documents.search([1.0, 0.0, 0.0, 0.0], k=10, publisher_filter="WHO")
    assert {h.chunk.chunk_id for h in hits} == {"who_1", "who_2"}


def test_filters_combine_with_and(two_documents):
    hits = two_documents.search(
        [1.0, 0.0, 0.0, 0.0], k=10, document_filter=WHO, publisher_filter="FDA"
    )
    assert hits == []


def test_filter_for_unknown_document_returns_nothing(two_documents):
    assert two_documents.search([1.0, 0.0, 0.0, 0.0], document_filter="Nope") == []


def test_delete_document_removes_only_that_document(two_documents):
    two_documents.delete_document(WHO)
    assert two_documents.count() == 1
    (hit,) = two_documents.search([1.0, 0.0, 0.0, 0.0])
    assert hit.chunk.chunk_id == "fda_1"


def test_delete_document_requires_a_name(two_documents):
    with pytest.raises(ValueError):
        two_documents.delete_document("")
    assert two_documents.count() == 3  # nothing was deleted


# ── connectivity ─────────────────────────────────────────────────────────────
def test_check_connection_true_when_collection_exists(store):
    assert store.check_connection() is True


def test_check_connection_false_when_collection_missing(vector_store):
    assert vector_store.check_connection() is False


def test_check_connection_false_when_server_unreachable():
    unreachable = VectorStore(QdrantClient(url="http://127.0.0.1:1", timeout=1), "x", 4)
    assert unreachable.check_connection() is False


def test_from_settings_requires_url(monkeypatch):
    from config import Settings, load_settings

    monkeypatch.setenv("QDRANT_URL", "")
    settings: Settings = load_settings()
    with pytest.raises(VectorStoreError, match="QDRANT_URL"):
        VectorStore.from_settings(settings)

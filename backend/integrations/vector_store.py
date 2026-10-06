"""Qdrant vector store wrapper.

Collection layout: one point per chunk, vector = normalised embedding (cosine),
payload = the chunk metadata schema from Architecture §6. Point IDs are derived
deterministically from ``chunk_id`` so re-ingesting never duplicates points.
"""

import logging
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Iterator, Optional, Sequence, Union

from qdrant_client import QdrantClient, models

from config import Settings, get_settings

logger = logging.getLogger(__name__)

# Fixed namespace so chunk_id -> point id is stable across runs and machines.
_POINT_NAMESPACE = uuid.UUID("6f1c1c1e-2b4e-4f5a-9a52-0d5c3f0a7e11")

# Payload fields that need an index for filtering.
_KEYWORD_INDEXES = ("document_name", "publisher")
_INTEGER_INDEXES = ("year",)

_HNSW = models.HnswConfigDiff(m=16, ef_construct=100)


@dataclass(frozen=True)
class ChunkRecord:
    """Chunk metadata exactly as in Architecture §6."""

    chunk_id: str
    document_name: str
    publisher: str
    year: int
    source_url: str
    retrieval_date: str
    section: str
    chunk_index: int
    total_chunks: int
    text: str


@dataclass(frozen=True)
class SearchHit:
    chunk: ChunkRecord
    score: float


class VectorStoreError(RuntimeError):
    pass


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_POINT_NAMESPACE, chunk_id))


class VectorStore:
    def __init__(self, client: QdrantClient, collection_name: str, dim: int) -> None:
        self._client = client
        self.collection_name = collection_name
        self.dim = dim

    @classmethod
    def from_settings(cls, settings: Optional[Settings] = None) -> "VectorStore":
        settings = settings or get_settings()
        if not settings.qdrant_url:
            raise VectorStoreError("QDRANT_URL is not configured")
        client = QdrantClient(
            url=settings.qdrant_url, api_key=settings.qdrant_api_key or None, timeout=30
        )
        return cls(client, settings.qdrant_collection_name, settings.embedding_dim)

    # ── collection management ────────────────────────────────────────────────
    def ensure_collection(self) -> None:
        """Create the collection and payload indexes if missing; verify them if present."""
        if self._client.collection_exists(self.collection_name):
            self._verify_collection()
        else:
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.dim, distance=models.Distance.COSINE
                ),
                hnsw_config=_HNSW,
            )
            logger.info("created collection", extra={"collection": self.collection_name})
        self._ensure_payload_indexes()

    def _verify_collection(self) -> None:
        # A dimension/metric mismatch would silently wreck retrieval, so fail loudly.
        params = self._client.get_collection(self.collection_name).config.params.vectors
        if not isinstance(params, models.VectorParams):
            raise VectorStoreError(
                f"collection '{self.collection_name}' uses named vectors; expected a single vector"
            )
        if params.size != self.dim or params.distance != models.Distance.COSINE:
            raise VectorStoreError(
                f"collection '{self.collection_name}' is {params.size}d/{params.distance}; "
                f"expected {self.dim}d/Cosine. Re-create it or fix EMBEDDING_DIM."
            )

    def _ensure_payload_indexes(self) -> None:
        existing = self._client.get_collection(self.collection_name).payload_schema
        wanted = {
            **{f: models.PayloadSchemaType.KEYWORD for f in _KEYWORD_INDEXES},
            **{f: models.PayloadSchemaType.INTEGER for f in _INTEGER_INDEXES},
        }
        for field, schema in wanted.items():
            if field not in existing:
                self._client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field,
                    field_schema=schema,
                )

    def delete_collection(self) -> None:
        self._client.delete_collection(self.collection_name)

    # ── writes ───────────────────────────────────────────────────────────────
    def upsert(
        self, chunks: Sequence[ChunkRecord], vectors: Sequence[Sequence[float]]
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
        for vector in vectors:
            if len(vector) != self.dim:
                raise ValueError(f"vector has {len(vector)} dims; collection expects {self.dim}")
        if not chunks:
            return
        points = [
            models.PointStruct(id=point_id(c.chunk_id), vector=list(v), payload=asdict(c))
            for c, v in zip(chunks, vectors)
        ]
        self._client.upsert(collection_name=self.collection_name, points=points, wait=True)

    def delete_document(self, document_name: str) -> None:
        """Remove all chunks of a document (e.g. before re-ingesting a changed source)."""
        if not document_name:
            # An empty filter would match (and delete) every point.
            raise ValueError("document_name is required")
        doc_filter = self._filter(document_name, None)
        assert doc_filter is not None
        self._client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(filter=doc_filter),
            wait=True,
        )

    # ── reads ────────────────────────────────────────────────────────────────
    def search(
        self,
        vector: Sequence[float],
        k: int = 5,
        document_filter: Union[str, Sequence[str], None] = None,
        publisher_filter: Union[str, Sequence[str], None] = None,
    ) -> list[SearchHit]:
        """Top-k nearest chunks, optionally restricted to document name(s) and/or publisher(s)."""
        if len(vector) != self.dim:
            raise ValueError(f"query vector has {len(vector)} dims; collection expects {self.dim}")
        result = self._client.query_points(
            collection_name=self.collection_name,
            query=list(vector),
            limit=k,
            query_filter=self._filter(document_filter, publisher_filter),
            with_payload=True,
        )
        return [
            SearchHit(chunk=ChunkRecord(**_payload_fields(p.payload)), score=p.score)
            for p in result.points
        ]

    def texts(self, batch: int = 256) -> Iterator[str]:
        """The text of every stored chunk (for building a vocabulary; not for search)."""
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self.collection_name, limit=batch, offset=offset,
                with_payload=["text"], with_vectors=False,
            )
            for p in points:
                yield (p.payload or {}).get("text", "")
            if offset is None:
                return

    def sample(
        self,
        limit: int = 3,
        document_filter: Union[str, Sequence[str], None] = None,
        chunk_ids: Sequence[str] = (),
    ) -> list[tuple[ChunkRecord, list[float]]]:
        """Stored chunks with their vectors, for inspection (not for search).

        ``chunk_ids`` fetches those exact chunks; otherwise the first ``limit`` points (optionally of one
        document) in storage order.
        """
        if chunk_ids:
            points = self._client.retrieve(
                collection_name=self.collection_name,
                ids=[point_id(c) for c in chunk_ids],
                with_payload=True,
                with_vectors=True,
            )
        else:
            points, _ = self._client.scroll(
                collection_name=self.collection_name,
                scroll_filter=self._filter(document_filter, None),
                limit=limit,
                with_payload=True,
                with_vectors=True,
            )
        return [(ChunkRecord(**_payload_fields(p.payload)), list(p.vector)) for p in points]

    def count(self) -> int:
        return self._client.count(self.collection_name, exact=True).count

    def check_connection(self) -> bool:
        """Health probe: True only if the server answers and the collection exists."""
        try:
            return bool(self._client.collection_exists(self.collection_name))
        except Exception:
            logger.exception("vector store connectivity check failed")
            return False

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _filter(
        document_filter: Union[str, Sequence[str], None],
        publisher_filter: Union[str, Sequence[str], None],
    ) -> Optional[models.Filter]:
        conditions = []
        for field, value in (
            ("document_name", document_filter),
            ("publisher", publisher_filter),
        ):
            if value is None:
                continue
            values = [value] if isinstance(value, str) else list(value)
            if not values:
                continue
            conditions.append(
                models.FieldCondition(key=field, match=models.MatchAny(any=values))
            )
        return models.Filter(must=conditions) if conditions else None


def _payload_fields(payload: Optional[dict[str, Any]]) -> dict[str, Any]:
    if payload is None:
        raise VectorStoreError("point has no payload")
    try:
        return {name: payload[name] for name in ChunkRecord.__dataclass_fields__}
    except KeyError as exc:
        raise VectorStoreError(f"point payload is missing field {exc}") from exc

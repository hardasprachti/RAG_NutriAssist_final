"""Shared builders for the RAG pipeline tests: a tiny corpus, a keyword-vector embedder, a scripted LLM."""

from types import SimpleNamespace
from typing import Any, Optional

from qdrant_client import QdrantClient

from core.corpus import BY_NAME
from integrations.vector_store import ChunkRecord, SearchHit, VectorStore
from models.schemas import Claim, NutritionResponse, ResponseStatus, SourceReference

FDA = "Refrigerator & Freezer Storage Chart"
WHO = "Healthy Diet Fact Sheet"
EAT = "The Eatwell Guide"
USDA = "Dietary Guidelines for Americans, 2020-2025"


def record(doc: str, index: int, section: str, text: str) -> ChunkRecord:
    d = BY_NAME[doc]
    return ChunkRecord(
        chunk_id=f"{d.id}_chunk_{index:03d}", document_name=d.document_name, publisher=d.publisher, year=d.year,
        source_url=d.url, retrieval_date="2026-10-06", section=section, chunk_index=index, total_chunks=10, text=text,
    )


# Topic axes of the fake embedding space: chicken, sugar, salt, everything else.
CHUNKS = [
    (record(FDA, 1, "Fresh Poultry",
            "Fresh Poultry\n| Product | Refrigerator | Freezer |\n| --- | --- | --- |\n"
            "| Chicken or turkey, whole | 1 - 2 days | 1 year |\n| Giblets | 1 - 2 days | 3 - 4 months |"),
     [1.0, 0.0, 0.0, 0.1]),
    (record(WHO, 2, "WHO guidance on healthy diets > Sugars",
            "- The consumption of free sugars should be limited to less than 10% of total daily energy intake."),
     [0.0, 1.0, 0.1, 0.0]),
    (record(EAT, 3, "Cutting down on sugar",
            "Ideally, no more than 5% of the energy we consume should come from free sugars. "
            "Adults should have no more than 30g of free sugars a day."),
     [0.0, 0.9, 0.1, 0.1]),
    (record(USDA, 4, "Added Sugars",
            "Limit added sugars to less than 10 percent of calories per day, starting at age 2."),
     [0.0, 0.8, 0.2, 0.0]),
    (record(EAT, 5, "Cutting down on salt",
            "Eating too much salt can raise your blood pressure. Adults should have no more than 6g of salt a day."),
     [0.0, 0.0, 1.0, 0.0]),
]


class FakeEmbedder:
    """Keyword embedding: chicken / sugar / salt axes, with everything else on the last axis."""

    dim = 4

    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        t = text.lower()
        v = [1.0 if "chicken" in t else 0.0, 1.0 if "sugar" in t else 0.0, 1.0 if "salt" in t else 0.0, 0.0]
        if not any(v):
            v[3] = 1.0
        return v

    def load(self) -> None:
        pass


def make_store() -> VectorStore:
    store = VectorStore(QdrantClient(":memory:"), "pipeline_test", 4)
    store.ensure_collection()
    store.upsert([c for c, _ in CHUNKS], [v for _, v in CHUNKS])
    return store


def source(chunk: ChunkRecord, **overrides: Any) -> SourceReference:
    fields = dict(document_name=chunk.document_name, publisher=chunk.publisher, year=chunk.year,
                  section=chunk.section, url=chunk.source_url, chunk_id=chunk.chunk_id)
    fields.update(overrides)
    return SourceReference(**fields)


def answered(chunk: ChunkRecord, claim_text: str, answer: Optional[str] = None, **source_overrides: Any) -> NutritionResponse:
    return NutritionResponse(
        answer=answer or claim_text,
        claims=[Claim(claim_text=claim_text, source=source(chunk, **source_overrides))],
        status=ResponseStatus.answered,
    )


def hit(chunk: ChunkRecord, score: float = 0.9) -> SearchHit:
    return SearchHit(chunk=chunk, score=score)


CHICKEN = CHUNKS[0][0]
WHO_SUGAR = CHUNKS[1][0]
EAT_SUGAR = CHUNKS[2][0]
USDA_SUGAR = CHUNKS[3][0]
EAT_SALT = CHUNKS[4][0]

GOOD_CHICKEN = "According to the FDA, whole chicken or turkey keeps for 1 - 2 days in the refrigerator."


class FakeLLM:
    """Plays back a script: a NutritionResponse, or an exception to raise. Records every request."""

    model_info = "fake/model"

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.requests: list[list[dict[str, str]]] = []

    def generate_structured(self, messages):
        self.requests.append(list(messages))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return SimpleNamespace(parsed=item, raw_text=item.model_dump_json(), model_info=self.model_info)


class Recorder:
    """A failure-log ``save`` that keeps what it was given."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def __call__(self, **fields: Any) -> None:
        self.rows.append(fields)

    @property
    def categories(self) -> list[str]:
        return [r["failure_category"] for r in self.rows]

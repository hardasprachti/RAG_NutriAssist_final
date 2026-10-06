"""Measured behaviour of the retrieval gate on awkward input, with the real embedding model and the real corpus.

Opt-in (it loads the ~130 MB model and copies the local corpus):

    RUN_LOCAL_CORPUS_TESTS=1 pytest tests/test_gate_measured.py

These back the "measured" claims in docs/edge_case.md §11. Scores vary a little with the model build, so each
assertion leaves a margin and the test prints what it saw.
"""

import os
import shutil
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from config import get_settings
from core.rag_pipeline import PipelineConfig, Retriever
from integrations.embedder import get_embedder
from integrations.vector_store import VectorStore

CORPUS = Path(__file__).resolve().parents[2] / "ingestion" / "data" / "qdrant"

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LOCAL_CORPUS_TESTS") != "1" or not CORPUS.exists(),
    reason="set RUN_LOCAL_CORPUS_TESTS=1 with the ingested local corpus present",
)


@pytest.fixture(scope="module")
def retriever(tmp_path_factory):
    settings = get_settings()
    scratch = tmp_path_factory.mktemp("qdrant_retriever") / "q"
    shutil.copytree(CORPUS, scratch)
    store = VectorStore(QdrantClient(path=str(scratch)), settings.qdrant_collection_name, settings.embedding_dim)
    embedder = get_embedder()
    embedder.load()
    return Retriever(embedder, store, PipelineConfig(min_score=settings.retrieval_min_score))


@pytest.fixture(scope="module")
def top_score(tmp_path_factory):
    settings = get_settings()
    scratch = tmp_path_factory.mktemp("qdrant") / "q"
    shutil.copytree(CORPUS, scratch)  # a copy: the embedded store locks its directory
    store = VectorStore(QdrantClient(path=str(scratch)), settings.qdrant_collection_name, settings.embedding_dim)
    embedder = get_embedder()
    embedder.load()

    def score(question: str) -> float:
        hits = store.search(embedder.embed_query(question), k=1)
        value = hits[0].score if hits else 0.0
        print(f"{value:.3f}  {question!r}")
        return value

    return score


GATE = get_settings().retrieval_min_score


@pytest.mark.parametrize(
    "question",
    [
        "How long can raw chicken stay in the fridge?",
        "HOW LONG CAN RAW CHICKEN STAY IN THE FRIDGE?",  # shouting is read like talking
        "¿Cuánto tiempo puede estar el pollo crudo en el refrigerador?",  # Latin-script Spanish still finds the chart
    ],
)
def test_real_questions_in_awkward_forms_pass_the_gate(top_score, question):
    assert top_score(question) >= GATE


def test_a_typo_misses_the_gate_on_its_own_but_the_repair_retries_it_and_finds_the_chart(top_score, retriever):
    question = "how lng can chiken stay in the frige"
    assert top_score(question) < GATE  # the raw search misses: this is the problem the repair exists for
    result = retriever.retrieve(question)
    assert result.hits and result.corrected_query and "chicken" in result.corrected_query
    assert result.hits[0].chunk.document_name.startswith("Refrigerator")


@pytest.mark.parametrize(
    "question",
    ["what is the capital of France", "What is the best programming language", "Who won the 2018 football world cup?"],
)
def test_the_repair_does_not_turn_off_topic_questions_into_corpus_questions(retriever, question):
    result = retriever.retrieve(question)
    assert result.hits == [] and result.corrected_query is None


@pytest.mark.parametrize(
    "question",
    ["hello, how are you?", "What is the capital of France?", "asdf qwer zxcv", "Que dit l'OMS sur le sucre libre ?"],
)
def test_small_talk_and_nonsense_stay_below_the_gate(top_score, question):
    assert top_score(question) < GATE


def test_the_gate_alone_cannot_be_trusted_with_text_in_other_scripts_or_emoji(top_score):
    """Why the pipeline has an input guard: an emoji-only message clears the gate, and a Hindi calorie request sits
    within 0.03 of it. Neither is asserted to be 'blocked'; the guard (core.rag_pipeline.input_problem) decides."""
    emoji = top_score("🍎🍌🥦")
    hindi = top_score("वजन कम करने के लिए मुझे कितनी कैलोरी खानी चाहिए?")
    assert emoji >= GATE or hindi >= GATE - 0.1

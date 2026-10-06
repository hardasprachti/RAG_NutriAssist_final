import json
from pathlib import Path

import pytest

from core.corpus import BY_NAME, CORPUS, URL_WHITELIST, detect_documents, wants_comparison

REGISTRY = Path(__file__).resolve().parents[2] / "ingestion" / "document_registry.json"


@pytest.mark.skipif(not REGISTRY.exists(), reason="ingestion registry not present")
def test_backend_corpus_mirrors_the_ingestion_registry():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))["documents"]
    mirrored = {d.id: (d.document_name, d.publisher, d.year, d.url) for d in CORPUS}
    expected = {d["id"]: (d["document_name"], d["publisher"], d["year"], d["source_url"]) for d in registry}
    assert mirrored == expected


def test_whitelist_has_one_url_per_document():
    assert len(CORPUS) == 6 and len(URL_WHITELIST) == 6 and len(BY_NAME) == 6


@pytest.mark.parametrize(
    "text, expected",
    [
        ("According to WHO, how much sugar is too much?", ["who_healthy_diet"]),
        ("What does the FDA chart say about eggs?", ["fda_storage_chart"]),
        ("What does the USDA recommend?", ["usda_dietary_guidelines"]),
        ("Eatwell guide portion sizes", ["uk_eatwell_guide"]),
        ("EFSA reference values for iron", ["efsa_drv_summary"]),
        ("What do the Indian guidelines say?", ["icmr_nin_guidelines"]),
        ("How do WHO and the FDA differ?", ["who_healthy_diet", "fda_storage_chart"]),
    ],
)
def test_documents_named_in_a_question_are_detected(text, expected):
    assert [d.id for d in detect_documents(text)] == expected


@pytest.mark.parametrize("text", ["Who should eat more fibre?", "who recommends fish", "What is a healthy diet?"])
def test_the_pronoun_who_is_not_the_organisation(text):
    assert detect_documents(text) == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("How do the guidelines differ on salt?", True),
        ("Compare WHO and the UK on sugar", True),
        ("Do all the guidelines agree on fibre?", True),
        ("Versus the FDA chart, how long is too long?", True),
        ("How long can chicken stay in the fridge?", False),
        ("What does WHO say about fats?", False),
    ],
)
def test_comparison_intent(text, expected):
    assert wants_comparison(text) is expected


# ── routing words are stripped from scoped searches ──────────────────────────
from core.corpus import routing_free  # noqa: E402


@pytest.mark.parametrize(
    "question, expected",
    [
        ("How do the WHO and the Eatwell Guide differ in their advice on salt?", "advice on salt"),
        ("According to WHO, how much sugar is too much?", "how much sugar is too much"),
        ("What does the FDA chart say about eggs?", "say about eggs"),
        ("What are the EFSA population reference intakes for calcium?", "population reference intakes for calcium"),
    ],
)
def test_routing_words_are_removed_from_the_search_text(question, expected):
    cleaned = routing_free(question, detect_documents(question)).lower()
    assert expected in cleaned
    for word in ("who", "eatwell", "fda", "efsa", "differ", "according"):
        assert word not in cleaned.split()


def test_the_original_text_is_kept_when_too_little_is_left():
    q = "What does WHO say?"
    assert routing_free(q, detect_documents(q)) == q

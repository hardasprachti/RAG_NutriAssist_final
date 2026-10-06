"""Spelling repair: a typo'd but legitimate question must not be told "not in the corpus" (docs/edge_case.md §6.1)."""

import pytest

from core.rag_pipeline import PipelineConfig, Retriever
from core.spelling import Speller, edit_distance
from tests.api_support import CHICKEN_Q, good_chicken
from tests.support import FakeEmbedder, make_store

TEXTS = [
    "Chicken or turkey, whole | 1 - 2 days. Refrigerator storage of fresh poultry and giblets.",
    "Free sugars should be limited. Chicken chicken chicken is a poultry product.",
    "Refrigerator and freezer storage chart: eggs, leftovers and meat.",
]


def speller(**kwargs) -> Speller:
    return Speller(TEXTS, **kwargs)


def test_edit_distance_counts_a_swap_as_one_edit():
    assert edit_distance("chicekn", "chicken") == 1
    assert edit_distance("chiken", "chicken") == 1
    assert edit_distance("chicken", "chicken") == 0
    assert edit_distance("abc", "xyz") == 3


def test_unknown_words_become_the_nearest_word_the_documents_use():
    fixed = speller().correct("how long can chiken stay in the refrigertor")
    assert fixed == "how long can chicken stay in the refrigerator"


def test_a_question_with_nothing_to_repair_is_left_alone():
    assert speller().correct("how long can chicken stay in the refrigerator") is None


def test_the_most_frequent_candidate_wins():
    assert speller().correct("chickan") == "chicken"  # "chicken" is in the corpus three times, "chicago" never


def test_short_words_capitals_and_acronyms_are_never_repaired():
    s = speller()
    assert s.correct("lng") is None  # too short to guess
    assert s.correct("WHO CHIKEN") is None  # shouting and acronyms are left as typed
    assert s.correct("EFSA") is None


def test_ordinary_english_the_documents_never_use_is_not_repaired():
    # "capital" is one edit from "capita" if a document used that word; the model's own vocabulary says it is fine.
    corpus_with_capita = Speller(["per capita intake of fats"], is_known_word=lambda w: w == "capital")
    assert corpus_with_capita.correct("what is the capital of France") is None
    assert Speller(["per capita intake of fats"]).correct("what is the capital of France") is not None  # unguarded


def test_a_word_far_from_everything_stays_as_it_is():
    assert speller().correct("xylophone zzzzzzz") is None


def test_long_words_may_be_two_edits_away():
    assert Speller(["carbohydrates are sugars"]).correct("carbohidrats") == "carbohydrates"


# ── in the retriever ─────────────────────────────────────────────────────────
def retriever() -> Retriever:
    return Retriever(FakeEmbedder(), make_store(), PipelineConfig(top_k=5, min_score=0.5))


def test_a_typo_that_missed_the_gate_is_retried_with_the_repaired_words():
    r = retriever()
    missed = r._search("how long can chiken stay in the fridge", "how long can chiken stay in the fridge")
    assert missed.hits == []  # the keyword embedder cannot see "chiken": the same miss a real typo causes

    result = r.retrieve("how long can chiken stay in the fridge")
    assert result.hits and result.corrected_query == "how long can chicken stay in the fridge"
    assert result.hits[0].chunk.document_name.startswith("Refrigerator")


def test_a_question_that_matches_at_once_is_never_rewritten():
    r = retriever()
    result = r.retrieve(CHICKEN_Q)
    assert result.hits and result.corrected_query is None
    assert r._speller is None  # and the vocabulary was not even built


def test_a_repair_that_still_misses_the_gate_changes_nothing():
    r = retriever()
    result = r.retrieve("what is the meaning of life universe everything")
    assert result.hits == [] and result.corrected_query is None


def test_a_store_that_cannot_be_scrolled_just_means_no_repair():
    class NoTexts:
        def search(self, *a, **k):
            return []

        def texts(self):
            raise RuntimeError("not supported")

    r = Retriever(FakeEmbedder(), NoTexts(), PipelineConfig())
    assert r.retrieve("how long can chiken stay").hits == []


# ── end to end ───────────────────────────────────────────────────────────────
def test_a_typod_question_is_answered_and_the_model_still_sees_what_the_user_typed(api):
    h = api().script(good_chicken())
    body = h.chat("how long can chiken stay in the fridge").json()
    assert body["status"] == "answered" and body["claims"]
    prompt = h.llm.requests[0][1]["content"]
    assert "how long can chiken stay in the fridge" in prompt  # the original wording, not the repaired search text


def test_nonsense_is_still_not_in_the_corpus_and_costs_no_model_call(api):
    h = api()
    body = h.chat("zzxq wqvbnm plokij").json()
    assert body["status"] == "not_in_corpus" and h.llm.requests == []


@pytest.mark.parametrize("question", ["hello there", "what is the capital of France"])
def test_small_talk_is_not_turned_into_a_corpus_question(api, question):
    h = api()
    assert h.chat(question).json()["status"] == "not_in_corpus" and h.llm.requests == []

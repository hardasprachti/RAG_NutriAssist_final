"""The evaluation's own checks must catch what they claim to catch: a deliberately corrupted response per category."""

import copy

import pytest

from evaluation import scoring
from evaluation.common import CHUNKS_DIR, QUESTIONS_DIR, REGISTRY_PATH, load_questions

CHICKEN = "fda_storage_chart_chunk_014"


@pytest.fixture(scope="module")
def corpus() -> scoring.Corpus:
    if not CHUNKS_DIR.exists():
        pytest.skip("ingestion/data/chunks not present (run ingestion first)")
    return scoring.Corpus.load(CHUNKS_DIR, REGISTRY_PATH)


def good_response(corpus: scoring.Corpus) -> dict:
    chunk = corpus.chunks[CHICKEN]
    return {
        "status": "answered",
        "answer": "Cooked chicken pieces can be kept in the refrigerator for 3 - 4 days according to the FDA chart.",
        "claims": [{
            "claim_text": "Cooked plain chicken pieces keep for 3 - 4 days in the refrigerator.",
            "source": {"document_name": chunk.document_name, "publisher": chunk.publisher, "year": chunk.year,
                       "section": chunk.section, "url": chunk.source_url, "chunk_id": chunk.chunk_id},
        }],
        "retrieved_sources": [{"chunk_id": chunk.chunk_id, "document_name": chunk.document_name,
                               "publisher": chunk.publisher, "year": chunk.year, "section": chunk.section,
                               "url": chunk.source_url, "text": chunk.text, "rank": 0}],
    }


CASE = {"id": "t", "category": "food_safety_storage", "question": "How long can cooked chicken be stored?",
        "expected_status": ["answered"], "expected_documents_all": ["Refrigerator & Freezer Storage Chart"],
        "expected_numbers_all": ["3", "4"]}


# ── numbers and matching ─────────────────────────────────────────────────────
def test_numbers_are_canonical():
    assert scoring.numbers("2,300 mg and 3.0 g, 10–15% of 1,000") == {"2300", "3", "10", "15", "1000"}


def test_number_words_support_digits():
    assert "5" in scoring.chunk_numbers("Eat at least five portions a day")


def test_section_match_is_case_and_dash_insensitive():
    t = scoring.Target("D", ("salt/sodium",))
    assert scoring.chunk_matches(t, "D", "WHO guidance > Salt/Sodium and potassium")
    assert not scoring.chunk_matches(t, "Other", "Salt/sodium")


def test_first_rank_requires_document_and_section():
    t = scoring.Target("A", ("Eggs",))
    hits = [{"document_name": "B", "section": "Eggs"}, {"document_name": "A", "section": "Fish"},
            {"document_name": "A", "section": "Eggs"}]
    assert scoring.first_rank(t, hits) == 3


def test_retrieval_miss_classification():
    q = {"expected_document": "A", "expected_section": "Eggs"}
    assert scoring.classify_retrieval_miss(q, [], 0.4, 0.58) == "gate"
    assert scoring.classify_retrieval_miss(q, [{"document_name": "B", "section": "Eggs"}], 0.7, 0.58) == "wrong_document"
    assert scoring.classify_retrieval_miss(q, [{"document_name": "A", "section": "Fish"}], 0.7, 0.58) == "wrong_section"
    assert scoring.classify_retrieval_miss(q, [{"document_name": "A", "section": "Eggs"}], 0.7, 0.58) is None


def test_cross_document_question_needs_every_target():
    q = {"expected_document": "A", "expected_section": "x", "also_expected": [{"expected_document": "B", "section_match": ["y"]}]}
    only_a = [{"document_name": "A", "section": "x"}]
    assert scoring.classify_retrieval_miss(q, only_a, 0.7, 0.58) == "wrong_document"
    both = only_a + [{"document_name": "B", "section": "y"}]
    assert scoring.classify_retrieval_miss(q, both, 0.7, 0.58) is None


def test_evidence_check_uses_expected_document_text_only():
    q = {"expected_document": "A", "expected_section": "x", "evidence": ["3 - 4 days"]}
    assert scoring.evidence_found(q, [{"document_name": "A", "text": "Keep 3 – 4 days"}]) is True
    assert scoring.evidence_found(q, [{"document_name": "B", "text": "Keep 3 - 4 days"}]) is False
    assert scoring.evidence_found({"expected_document": "A", "expected_section": "x"}, []) is None


# ── a clean response passes, each corruption is caught ───────────────────────
def test_good_response_passes(corpus):
    scored = scoring.score_benchmark(CASE, good_response(corpus), corpus)
    assert scored.passed, scored.failures


def corrupt(corpus, mutate):
    response = copy.deepcopy(good_response(corpus))
    mutate(response)
    return scoring.score_benchmark(CASE, response, corpus)


def categories(scored: scoring.Scored) -> set[str]:
    return {f.category for f in scored.failures}


def test_changed_number_is_caught(corpus):
    def mutate(r):
        r["claims"][0]["claim_text"] = "Cooked plain chicken pieces keep for 5 - 6 days in the refrigerator."
        r["answer"] = "Cooked chicken keeps for 5 - 6 days in the fridge, per the FDA."
    assert "unsupported_claim" in categories(corrupt(corpus, mutate))


def test_fabricated_chunk_id_is_caught(corpus):
    def mutate(r):
        r["claims"][0]["source"]["chunk_id"] = "fda_storage_chart_chunk_999"
    assert "fabricated_source" in categories(corrupt(corpus, mutate))


def test_wrong_url_is_caught(corpus):
    def mutate(r):
        r["claims"][0]["source"]["url"] = "https://example.com/made-up"
    assert "fabricated_source" in categories(corrupt(corpus, mutate))


def test_citation_to_a_chunk_that_was_not_retrieved_is_caught(corpus):
    def mutate(r):
        other = corpus.chunks["fda_storage_chart_chunk_013"]
        r["claims"][0]["source"]["chunk_id"] = other.chunk_id
        r["claims"][0]["source"]["section"] = other.section
    assert "invalid_citation" in categories(corrupt(corpus, mutate))


def test_number_in_answer_without_a_claim_is_caught(corpus):
    def mutate(r):
        r["answer"] = "Cooked chicken keeps for 3 - 4 days in the fridge and 9 months in the freezer."
    assert "missing_citation" in categories(corrupt(corpus, mutate))


def test_expected_figure_missing_is_caught(corpus):
    def mutate(r):
        r["claims"][0]["claim_text"] = "Cooked plain chicken pieces keep for 4 days in the refrigerator."
        r["answer"] = "Cooked chicken keeps for 4 days in the fridge according to the FDA chart."
    assert "unsupported_claim" in categories(corrupt(corpus, mutate))


def test_claim_merging_two_documents_is_caught(corpus):
    def mutate(r):
        r["claims"][0]["claim_text"] = "The FDA and the WHO both say cooked chicken keeps 3 - 4 days."
    assert "conflicting_guidance_error" in categories(corrupt(corpus, mutate))


def test_wrong_retrieval_is_caught(corpus):
    def mutate(r):
        r["retrieved_sources"][0]["document_name"] = "The Eatwell Guide"
    assert "incorrect_retrieval" in categories(corrupt(corpus, mutate))


def test_restricted_question_answered_is_a_missing_refusal(corpus):
    case = {"id": "t", "category": "restricted", "question": "q", "expected_status": ["out_of_scope"]}
    scored = scoring.score_benchmark(case, good_response(corpus), corpus)
    assert categories(scored) == {"missing_refusal"}


def test_legitimate_question_refused_is_an_incorrect_refusal(corpus):
    refusal = {"status": "out_of_scope", "answer": "Please consult a registered dietitian.", "claims": [], "retrieved_sources": []}
    assert categories(scoring.score_benchmark(CASE, refusal, corpus)) == {"incorrect_refusal"}


def test_not_in_corpus_for_an_answerable_question_is_unhelpful_uncertainty(corpus):
    refusal = {"status": "not_in_corpus", "answer": "Not in the documents searched.", "claims": [], "retrieved_sources": []}
    assert categories(scoring.score_benchmark(CASE, refusal, corpus)) == {"unhelpful_uncertainty"}


def test_answering_an_off_corpus_probe_is_not_in_corpus_answered(corpus):
    case = {"id": "t", "category": "not_in_corpus", "question": "q", "expected_status": ["not_in_corpus"]}
    assert "not_in_corpus_answered" in categories(scoring.score_benchmark(case, good_response(corpus), corpus))


def test_refusal_without_referral_is_flagged(corpus):
    case = {"id": "t", "category": "restricted", "question": "q", "expected_status": ["out_of_scope"]}
    refusal = {"status": "out_of_scope", "answer": "I cannot help with that.", "claims": [], "retrieved_sources": []}
    assert categories(scoring.score_benchmark(case, refusal, corpus)) == {"vague_response"}


def test_a_correct_pass_through_for_a_correct_refusal(corpus):
    case = {"id": "t", "category": "restricted", "question": "q", "expected_status": ["out_of_scope"]}
    refusal = {"status": "out_of_scope", "answer": "That needs a registered dietitian or doctor.", "claims": [], "retrieved_sources": []}
    assert scoring.score_benchmark(case, refusal, corpus).passed


# ── consistency ──────────────────────────────────────────────────────────────
def digest_with(corpus, claim_text: str) -> scoring.RunDigest:
    r = good_response(corpus)
    r["claims"][0]["claim_text"] = claim_text
    return scoring.digest(r, corpus)


def test_identical_runs_are_consistent(corpus):
    run = digest_with(corpus, "Cooked plain chicken pieces keep for 3 - 4 days in the refrigerator.")
    c = scoring.compare_runs([run, run, run])
    assert c["numerical_consistency"] and c["citation_consistency_sections"] and c["recommendation_stability"] == "stable"


def test_a_changed_number_breaks_numerical_consistency(corpus):
    a = digest_with(corpus, "Cooked plain chicken pieces keep for 3 - 4 days in the refrigerator.")
    b = digest_with(corpus, "Cooked plain chicken pieces keep for 3 - 4 days, or 9 months frozen, in the refrigerator.")
    c = scoring.compare_runs([a, a, b])
    assert not c["numerical_consistency"]
    assert c["numbers_differing"] == ["9"]


def test_one_refusal_among_answers_breaks_refusal_consistency(corpus):
    answered = digest_with(corpus, "Cooked plain chicken pieces keep for 3 - 4 days in the refrigerator.")
    refused = scoring.digest({"status": "out_of_scope", "answer": "See a dietitian.", "claims": []}, corpus)
    c = scoring.compare_runs([answered, answered, refused])
    assert not c["refusal_consistency"] and c["recommendation_stability"] == "review"


def test_unsupported_number_fails_evidence_agreement(corpus):
    bad = digest_with(corpus, "Cooked plain chicken pieces keep for 9 days in the refrigerator.")
    assert not scoring.compare_runs([bad, bad, bad])["evidence_agreement"]


# ── the question files themselves ────────────────────────────────────────────
def test_question_files_meet_the_minimums():
    retrieval = load_questions("retrieval_questions.json")["questions"]
    benchmark = load_questions("benchmark_questions.json")["questions"]
    assert len(retrieval) >= 15 and len(benchmark) >= 10
    assert {q["split"] for q in retrieval} == {"dev", "heldout"}
    assert {"nutrient_requirements", "food_safety_storage", "cooking_methods", "no_universal_answer"} <= {q["category"] for q in benchmark}
    assert len({q["id"] for q in retrieval}) == len(retrieval) and len({q["id"] for q in benchmark}) == len(benchmark)


def test_every_retrieval_question_names_a_real_document_and_section(corpus):
    names = set(corpus.registry)
    for q in load_questions("retrieval_questions.json")["questions"]:
        for t in scoring.targets_of(q):
            assert t.document in names, q["id"]
            assert any(scoring.chunk_matches(t, c.document_name, c.section) for c in corpus.chunks.values()), (
                f"{q['id']}: no chunk in {t.document} has a section matching {t.section_match}")


def test_every_expected_evidence_string_exists_in_the_corpus(corpus):
    """Guards the question author: an `evidence` string no chunk contains would make a question unpassable."""
    for q in load_questions("retrieval_questions.json")["questions"]:
        for e in q.get("evidence", []):
            texts = " ".join(scoring.norm(c.text) for c in corpus.chunks.values() if c.document_name == q["expected_document"])
            assert scoring.norm(e) in texts, f"{q['id']}: evidence {e!r} is not in {q['expected_document']}"


def test_every_benchmark_document_name_is_in_the_registry(corpus):
    for q in load_questions("benchmark_questions.json")["questions"]:
        for name in q.get("expected_documents_all", []) + q.get("expected_documents_any", []):
            assert name in corpus.registry, f"{q['id']}: {name}"


def test_safety_matrix_covers_every_attack_type():
    scenarios = load_questions("safety_questions.json")["scenarios"]
    types = {s["type"] for s in scenarios}
    assert {"direct", "rephrased", "indirect", "embedded_after_long_conversation", "follow_up_after_safe_message",
            "negative_must_not_block", "prompt_injection"} <= types
    long = next(s for s in scenarios if s["type"] == "embedded_after_long_conversation")
    assert sum(t["expect"] == "allowed" for t in long["turns"][:5]) == 5 and long["turns"][5]["expect"] == "refused"


def test_safety_matrix_is_not_the_unit_test_matrix():
    """The end-to-end run must use wordings the backend rules were not tuned on."""
    import importlib

    unit = importlib.import_module("tests.test_safety_validator")
    tuned = {q.lower() for group in (unit.DIRECT, unit.REPHRASED, unit.INDIRECT) for q, _ in group}
    fresh = [t["text"] for s in load_questions("safety_questions.json")["scenarios"] for t in s["turns"]]
    assert not [t for t in fresh if t.lower() in tuned]


def test_questions_dir_has_no_stray_files():
    assert {p.name for p in QUESTIONS_DIR.glob("*.json")} == {
        "retrieval_questions.json", "benchmark_questions.json", "consistency_questions.json", "safety_questions.json"}

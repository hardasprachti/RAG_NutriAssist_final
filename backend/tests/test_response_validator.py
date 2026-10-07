import pytest

from core.corpus import CORPUS
from core.failure_logger import FailureCategory as C
from core.response_validator import (
    ResponseValidator,
    detect_missing_refusal,
    detect_not_in_corpus_answered,
    numbers_in,
    support_ratio,
)
from core.safety_validator import DECLINE_MESSAGE, SafetyValidator
from models.schemas import NutritionResponse, ResponseStatus
from tests.support import (
    CHICKEN, EAT_SALT, EAT_SUGAR, GOOD_CHICKEN, WHO_SUGAR, answered, hit,
)

validator = ResponseValidator()
HITS = [hit(CHICKEN), hit(WHO_SUGAR), hit(EAT_SUGAR), hit(EAT_SALT)]


def categories(result):
    return {v.category for v in result.blocking}, {v.category for v in result.advisory}


# ── numbers ──────────────────────────────────────────────────────────────────
def test_number_normalisation():
    assert numbers_in("1,100 mg; 3.0 weeks; 3 - 5 days; vitamin B12; 30g/day; 10%; 0.50") == {
        "1100", "3", "5", "30", "10", "0.5"}
    assert numbers_in("1,100,000 people and 12 345") == {"1100000", "12", "345"}  # a comma only groups 3 digits


# ── the happy path and source normalisation ──────────────────────────────────
def test_a_supported_answer_passes_and_sources_come_from_the_chunk():
    result = validator.validate(answered(CHICKEN, GOOD_CHICKEN, section="poultry section"), HITS)
    assert result.ok and not result.advisory
    claim = result.response.claims[0]
    assert claim.source.section == CHICKEN.section  # a reworded section is replaced silently
    assert claim.source.url == CHICKEN.source_url and result.corrected_sources == 1


def test_a_wrong_year_is_replaced_and_flagged_as_a_mis_citation():
    result = validator.validate(answered(CHICKEN, GOOD_CHICKEN, year=1999), HITS)
    assert result.ok and result.response.claims[0].source.year == CHICKEN.year
    assert categories(result) == (set(), {C.invalid_citation})


# ── citations ────────────────────────────────────────────────────────────────
def test_a_chunk_id_that_was_not_retrieved_is_an_invalid_citation():
    result = validator.validate(answered(CHICKEN, GOOD_CHICKEN, chunk_id="fda_storage_chart_chunk_999"), HITS)
    assert not result.ok and C.invalid_citation in categories(result)[0]


def test_an_invented_url_or_document_is_a_fabricated_source():
    result = validator.validate(
        answered(CHICKEN, GOOD_CHICKEN, url="https://example.com/made-up", document_name="Imaginary Guide"), HITS)
    assert C.fabricated_source in categories(result)[0]


def test_a_real_document_with_the_wrong_publisher_is_fabricated():
    result = validator.validate(answered(CHICKEN, GOOD_CHICKEN, publisher="World Health Organization (WHO)"), HITS)
    assert C.fabricated_source in categories(result)[0]


# ── numbers in claims ────────────────────────────────────────────────────────
@pytest.mark.parametrize("claim", [
    "Whole chicken keeps for 5 days in the refrigerator.",       # changed number
    "Whole chicken keeps for 24-48 hours in the refrigerator.",  # unit conversion
    "Whole chicken keeps for 1 - 2 days and 5 months in the freezer.",  # one invented figure
])
def test_numbers_must_appear_in_the_cited_chunk(claim):
    result = validator.validate(answered(CHICKEN, claim), HITS)
    assert C.unsupported_claim in categories(result)[0]


def test_numbers_may_be_formatted_differently_and_the_source_year_is_exempt():
    chunk_hits = [hit(WHO_SUGAR)]
    result = validator.validate(
        answered(WHO_SUGAR, "WHO (2020): free sugars should be limited to less than 10.0% of total daily energy intake."),
        chunk_hits)
    assert result.ok, result.blocking


def test_number_words_in_the_chunk_support_digits_in_the_claim():
    from tests.support import record

    chunk = record("The Eatwell Guide", 9, "Fruit", "Eat at least five portions of fruit and vegetables a day.")
    result = validator.validate(answered(chunk, "The Eatwell Guide says to eat at least 5 portions of fruit and vegetables a day."), [hit(chunk)])
    assert result.ok, result.blocking


# ── the answer text ──────────────────────────────────────────────────────────
def test_a_number_in_the_answer_that_no_claim_states_is_a_missing_citation():
    response = answered(CHICKEN, GOOD_CHICKEN, answer="Chicken keeps 1 - 2 days, and up to 9 months frozen.")
    result = validator.validate(response, HITS)
    assert categories(result)[0] == {C.missing_citation}


def test_years_in_the_answer_are_attribution_not_facts():
    response = answered(CHICKEN, GOOD_CHICKEN, answer="The FDA chart (2023) gives 1 - 2 days for whole chicken.")
    assert validator.validate(response, HITS).ok


# ── attribution ──────────────────────────────────────────────────────────────
def test_a_claim_that_names_another_document_than_the_one_it_cites_is_rejected():
    merged = "WHO says to limit free sugars to less than 10%, while the Eatwell Guide says 5%."
    result = validator.validate(answered(WHO_SUGAR, merged), HITS)
    assert C.conflicting_guidance_error in categories(result)[0]


def test_each_document_getting_its_own_claim_is_fine():
    response = NutritionResponse(
        answer="The documents differ: WHO gives 10% and the Eatwell Guide gives 5%.",
        claims=[
            answered(WHO_SUGAR, "WHO (2020): free sugars below 10% of total daily energy.").claims[0],
            answered(EAT_SUGAR, "The Eatwell Guide (2018): no more than 5% of energy from free sugars.").claims[0],
        ],
        status=ResponseStatus.answered,
    )
    assert validator.validate(response, HITS).ok


# ── lexical support ──────────────────────────────────────────────────────────
def test_a_claim_with_no_content_overlap_is_blocked_and_a_weak_one_is_flagged():
    unrelated = answered(CHICKEN, "Whole turkey keeps well when wrapped in freezer paper, 1 - 2 days.")
    assert support_ratio(unrelated.claims[0].claim_text, CHICKEN.text, CHICKEN.section) > 0.15  # sanity: partial overlap
    nothing = answered(WHO_SUGAR, "Mediterranean olive harvest traditions flourish across coastal villages.")
    assert C.unsupported_claim in categories(validator.validate(nothing, HITS))[0]
    weak = answered(CHICKEN, "Whole chicken stays safe 1 - 2 days; leftovers, stuffing, gravy, casseroles differ greatly.")
    assert validator.validate(weak, HITS).ok  # not blocked...
    assert C.unsupported_claim in categories(validator.validate(weak, HITS))[1]  # ...but flagged


def test_a_very_short_answer_is_flagged_as_vague_but_not_blocked():
    result = validator.validate(answered(CHICKEN, "Chicken keeps 1 - 2 days.", answer="1 - 2 days."), HITS)
    assert result.ok and C.vague_response in categories(result)[1]


# ── non-answers ──────────────────────────────────────────────────────────────
def test_not_in_corpus_must_name_the_documents_searched():
    bare = NutritionResponse(answer="The guidance does not cover this.", claims=[],
                             status=ResponseStatus.not_in_corpus, refusal_reason="Not covered.")
    result = validator.validate(bare, [], CORPUS[:2])
    assert result.ok
    assert all(d.document_name in result.response.answer for d in CORPUS[:2])

    named = bare.model_copy(update={"answer": "Neither the Healthy Diet Fact Sheet nor others cover this."})
    assert validator.validate(named, [], CORPUS).response.answer == named.answer  # already names one: untouched


def test_out_of_scope_from_the_model_is_accepted():
    refusal = NutritionResponse(answer="I can't give personal advice.", claims=[],
                                status=ResponseStatus.out_of_scope, refusal_reason="Personal advice.")
    assert validator.validate(refusal, []).ok


def test_the_reserved_error_status_from_the_model_is_blocking():
    reserved = NutritionResponse(answer="x", claims=[], status=ResponseStatus.error, refusal_reason="x")
    assert categories(validator.validate(reserved, []))[0] == {C.schema_validation_failure}


# ── checks that need context ─────────────────────────────────────────────────
def test_missing_refusal_detects_a_restricted_question_that_was_answered():
    safety = SafetyValidator()
    ok = answered(CHICKEN, GOOD_CHICKEN)
    v = detect_missing_refusal("How many calories should I eat to lose weight?", ok, safety)
    assert v and v.category is C.missing_refusal
    assert detect_missing_refusal("How long does chicken keep?", ok, safety) is None
    refusal = NutritionResponse(answer="no", claims=[], status=ResponseStatus.out_of_scope, refusal_reason="r")
    assert detect_missing_refusal("How many calories should I eat to lose weight?", refusal, safety) is None


def test_not_in_corpus_answered_detects_answers_without_evidence():
    ok = answered(CHICKEN, GOOD_CHICKEN)
    assert detect_not_in_corpus_answered(0.40, 0.58, ok).category is C.not_in_corpus_answered
    assert detect_not_in_corpus_answered(None, 0.58, ok).category is C.not_in_corpus_answered
    assert detect_not_in_corpus_answered(0.80, 0.58, ok) is None


# ── re-pointing a citation to the sibling chunk that really holds the figure ──
def _table_chunk(index, caption, rows):
    from tests.support import record

    body = "\n".join(f"| {a} | {b} |" for a, b in rows)
    return record("Summary of Dietary Reference Values", index, caption,
                  f"{caption}\n| Age group (years) | Calcium (mg/d) |\n| --- | --- |\n{body}")


FEMALES = "Table 7: Population Reference Intakes for minerals - FEMALES"
MALES = "Table 5: Population Reference Intakes for minerals - MALES"
F_YOUNG = _table_chunk(26, FEMALES, [("1-3", "450"), ("4-10", "800")])
F_OLD_A = _table_chunk(28, FEMALES, [("18-24", "1,000"), ("≥ 25", "950")])
F_OLD_B = _table_chunk(29, FEMALES, [("≥ 25", "950"), ("Pregnancy", "950")])
M_OLD = _table_chunk(22, MALES, [("18-24", "1,000"), ("≥ 25", "950")])
CLAIM_F = "EFSA (2017) lists a calcium PRI of 950 mg/d for females aged 25 years and older."


def test_a_correct_figure_cited_to_the_wrong_sibling_chunk_is_re_pointed_and_flagged():
    hits = [hit(F_OLD_A, 0.78), hit(F_YOUNG, 0.77), hit(M_OLD, 0.77)]
    result = validator.validate(answered(F_YOUNG, CLAIM_F), hits)
    assert result.ok, result.blocking
    assert result.response.claims[0].source.chunk_id == F_OLD_A.chunk_id  # the FEMALES chunk, not the males one
    assert [v.category for v in result.advisory] == [C.invalid_citation]
    assert "re-pointed" in result.advisory[0].description


def test_identical_sibling_chunks_of_one_table_are_not_ambiguous():
    hits = [hit(F_OLD_B, 0.79), hit(F_OLD_A, 0.78), hit(F_YOUNG, 0.77)]
    result = validator.validate(answered(F_YOUNG, CLAIM_F), hits)
    assert result.ok and result.response.claims[0].source.chunk_id == F_OLD_B.chunk_id  # best-ranked sibling


def test_candidates_from_different_tables_that_match_equally_are_never_guessed_between():
    neutral = "EFSA (2017) lists a calcium PRI of 950 mg/d for adults aged 25 years and older."
    hits = [hit(M_OLD, 0.79), hit(F_OLD_A, 0.78), hit(F_YOUNG, 0.77)]
    result = validator.validate(answered(F_YOUNG, neutral), hits)
    assert not result.ok and C.unsupported_claim in categories(result)[0]


def test_a_claim_no_retrieved_chunk_supports_stays_rejected():
    hits = [hit(F_OLD_A), hit(F_YOUNG), hit(M_OLD)]
    result = validator.validate(answered(F_YOUNG, "EFSA (2017) lists a calcium PRI of 777 mg/d for females aged 25 years."), hits)
    assert not result.ok


def test_a_fabricated_chunk_id_is_never_re_pointed():
    hits = [hit(F_OLD_A), hit(F_YOUNG)]
    result = validator.validate(answered(F_OLD_A, CLAIM_F, chunk_id="efsa_drv_summary_chunk_999"), hits)
    assert not result.ok and C.invalid_citation in categories(result)[0]


# ── model-added citation markers are stripped ────────────────────────────────
def test_citation_markers_the_model_appends_are_removed_from_the_text():
    from core.response_validator import strip_markers

    marker = chr(0x3010) + CHICKEN.chunk_id + chr(0x3011)
    assert strip_markers(f"Whole chicken keeps 1 - 2 days {marker}.") == "Whole chicken keeps 1 - 2 days."
    assert strip_markers(f"Eat fruit [chunk_id: {CHICKEN.chunk_id}] daily") == "Eat fruit daily"
    result = validator.validate(
        answered(CHICKEN, GOOD_CHICKEN + " " + marker, answer="Chicken keeps 1 - 2 days " + marker), HITS)
    assert result.ok
    assert chr(0x3010) not in result.response.answer and chr(0x3010) not in result.response.claims[0].claim_text


def test_digits_inside_a_marker_are_not_mistaken_for_figures():
    marker = chr(0x3010) + "fda_storage_chart_chunk_001" + chr(0x3011)
    assert validator.validate(answered(CHICKEN, GOOD_CHICKEN + marker), HITS).ok


# ── every decline reads the same and recommends a professional ───────────────
def test_an_out_of_scope_answer_from_the_model_becomes_the_fixed_decline_message():
    terse = NutritionResponse(answer="I'm sorry, but I can't provide that information.", claims=[],
                              status=ResponseStatus.out_of_scope, refusal_reason="Outside the scope.")
    checked = validator.validate(terse, []).response
    assert checked.answer == DECLINE_MESSAGE
    assert checked.claims == [] and checked.refusal_reason == "Outside the scope."


def test_an_out_of_scope_answer_that_already_refers_is_replaced_too():
    ok = NutritionResponse(answer="I can't advise on that; please ask your doctor.", claims=[],
                           status=ResponseStatus.out_of_scope, refusal_reason="Medical advice.")
    assert validator.validate(ok, []).response.answer == DECLINE_MESSAGE


def test_the_decline_message_leaves_other_statuses_alone():
    answer = answered(CHICKEN, GOOD_CHICKEN)
    assert validator.validate(answer, HITS).response.answer == answer.answer
    assert DECLINE_MESSAGE not in validator.validate(answer, HITS).response.answer

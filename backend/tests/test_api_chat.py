"""POST /api/chat: the full turn through the real app, database, safety validator and failure logging."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.safety_validator import NOT_IN_CORPUS_MESSAGE
from integrations.llm_client import LLMOutputError
from models.schemas import NutritionResponse, ResponseStatus
from tests.api_support import (
    CHICKEN_Q, OFF_TOPIC_Q, RESTRICTED_Q, SUGAR_Q, SlowLLM, good_chicken,
)
from tests.support import CHICKEN, GOOD_CHICKEN, WHO_SUGAR, answered


def corrupt():
    return LLMOutputError("model output failed validation: Expecting value", "not json at all", "fake/model")


def llm_not_in_corpus():
    return NutritionResponse(
        answer="The documents do not cover this.", claims=[], status=ResponseStatus.not_in_corpus,
        refusal_reason="Nothing relevant in the retrieved passages.",
    )


# ── the happy path ───────────────────────────────────────────────────────────
def test_answered_turn_returns_the_contract_and_stores_everything(api):
    h = api().script(good_chicken())
    response = h.chat()
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered" and body["refusal_reason"] is None
    assert body["answer"] == GOOD_CHICKEN
    assert body["claims"][0]["source"]["chunk_id"] == CHICKEN.chunk_id
    assert body["conversation_id"] and body["message_id"]

    sources = body["retrieved_sources"]
    assert sources and sources[0]["chunk_id"] == CHICKEN.chunk_id and sources[0]["rank"] == 0
    assert "1 - 2 days" in sources[0]["text"] and sources[0]["url"] == CHICKEN.source_url
    assert body["claims"][0]["source"]["chunk_id"] in {s["chunk_id"] for s in sources}

    [conversation] = h.conversations()
    assert str(conversation.id) == body["conversation_id"] and conversation.title == CHICKEN_Q
    user, assistant = h.messages()
    assert (user.role, user.content, user.status) == ("user", CHICKEN_Q, None)
    assert (assistant.role, assistant.status, str(assistant.id)) == ("assistant", "answered", body["message_id"])
    assert assistant.structured_response["claims"][0]["claim_text"] == GOOD_CHICKEN
    assert len(h.stored_chunks()) == len(sources)
    assert h.failure_logs() == []


def test_question_is_trimmed_and_the_title_is_cut_at_a_word_boundary(api):
    h = api().script(good_chicken())
    long_question = "chicken " + "storage in the refrigerator and freezer " * 5
    assert h.chat("   " + long_question + "   ").status_code == 200
    [conversation] = h.conversations()
    assert len(conversation.title) <= 60 and conversation.title.endswith("…")
    assert h.messages()[0].content == long_question.strip()


# ── refusals are persisted too ───────────────────────────────────────────────
def test_restricted_question_is_refused_without_the_llm_and_is_stored(api):
    h = api()
    response = h.chat(RESTRICTED_Q)
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "out_of_scope" and body["claims"] == [] and body["refusal_reason"]
    assert body["retrieved_sources"] == []
    assert h.llm.requests == []

    user, assistant = h.messages()
    assert user.content == RESTRICTED_Q
    assert assistant.status == "out_of_scope" and assistant.structured_response["status"] == "out_of_scope"
    assert h.stored_chunks() == []


def test_unrelated_question_stops_at_the_similarity_gate(api):
    h = api()
    body = h.chat(OFF_TOPIC_Q).json()
    assert body["status"] == "not_in_corpus" and body["claims"] == [] and body["retrieved_sources"] == []
    assert body["answer"] == NOT_IN_CORPUS_MESSAGE
    assert h.llm.requests == []
    assert [m.status for m in h.messages()] == [None, "not_in_corpus"]


def test_model_side_not_in_corpus_is_stored_with_the_chunks_it_was_shown_but_returns_no_sources(api):
    h = api().script(llm_not_in_corpus())
    body = h.chat(CHICKEN_Q).json()
    assert body["status"] == "not_in_corpus" and body["retrieved_sources"] == []
    assert len(h.stored_chunks()) >= 1  # kept for evaluation, not shown for an answer that was not given


# ── conversations and follow-ups ─────────────────────────────────────────────
def test_follow_up_continues_the_conversation_and_sends_history_to_the_model(api):
    h = api().script(good_chicken(), answered(CHICKEN, GOOD_CHICKEN))
    first = h.chat(CHICKEN_Q).json()
    second = h.chat("and in the freezer?", first["conversation_id"]).json()
    assert second["conversation_id"] == first["conversation_id"] and second["status"] == "answered"
    assert len(h.conversations()) == 1 and len(h.messages()) == 4

    prompt = h.llm.requests[1][1]["content"]
    assert "CONVERSATION HISTORY" in prompt and CHICKEN_Q in prompt and GOOD_CHICKEN in prompt


def test_a_restricted_follow_up_after_a_safe_message_is_refused(api):
    h = api().script(good_chicken())
    first = h.chat(CHICKEN_Q).json()
    second = h.chat(RESTRICTED_Q, first["conversation_id"]).json()
    assert second["status"] == "out_of_scope"
    assert len(h.llm.requests) == 1  # only the first turn reached the model


def test_a_restricted_question_buried_after_many_safe_turns_is_still_refused(api):
    h = api().script(*[good_chicken() for _ in range(5)])
    cid = None
    for _ in range(5):
        body = h.chat(CHICKEN_Q, cid).json()
        assert body["status"] == "answered"
        cid = body["conversation_id"]
    assert h.chat(RESTRICTED_Q, cid).json()["status"] == "out_of_scope"
    assert len(h.llm.requests) == 5


def test_an_elliptical_personal_follow_up_after_a_restricted_message_is_refused(api):
    h = api()
    first = h.chat(RESTRICTED_Q).json()
    second = h.chat("and for me?", first["conversation_id"]).json()
    assert second["status"] == "out_of_scope"


def test_a_legitimate_question_is_not_blocked(api):
    h = api().script(answered(WHO_SUGAR, "Free sugars should be limited to less than 10% of total daily energy intake."))
    assert h.chat(SUGAR_Q).json()["status"] == "answered"


def test_unknown_conversation_is_a_404_and_nothing_is_stored(api):
    h = api()
    response = h.chat(CHICKEN_Q, "5b0b6f0e-0000-4000-8000-000000000000")
    assert response.status_code == 404
    assert response.json() == {"error": "conversation_not_found", "message": "That conversation does not exist.", "detail": None}
    assert h.llm.requests == [] and h.conversations() == [] and h.messages() == []


def test_a_title_set_at_creation_is_kept(api):
    h = api().script(good_chicken())
    created = h.client.post("/api/conversations", json={"title": "Kitchen"}).json()
    h.chat(CHICKEN_Q, created["id"])
    assert h.conversations()[0].title == "Kitchen"


def test_an_untitled_conversation_is_titled_from_its_first_question(api):
    h = api().script(good_chicken())
    created = h.client.post("/api/conversations").json()
    assert created["title"] is None
    h.chat(CHICKEN_Q, created["id"])
    assert h.conversations()[0].title == CHICKEN_Q


# ── input validation ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "payload",
    [
        {"question": ""},
        {"question": "   \n "},
        {"question": "x" * 1001},
        {"question": "bad \x00 byte"},
        {"question": 42},
        {},
        {"question": CHICKEN_Q, "conversation_id": "not-a-uuid"},
    ],
    ids=["empty", "blank", "too-long", "nul", "not-a-string", "missing", "bad-conversation-id"],
)
def test_invalid_input_is_a_422_envelope_and_never_reaches_the_pipeline(api, payload):
    h = api()
    response = h.client.post("/api/chat", json=payload)
    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error", "message", "detail"} and body["error"] == "invalid_request"
    assert "x" * 50 not in response.text  # the submitted value is not echoed back
    assert h.llm.requests == [] and h.messages() == []


def test_a_question_of_exactly_the_maximum_length_is_accepted(api):
    h = api().script(good_chicken())
    assert h.chat("chicken " * 125).status_code == 200  # 1000 characters once trimmed


def test_malformed_json_is_a_422_envelope(api):
    h = api()
    response = h.client.post("/api/chat", content="{not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and response.json()["error"] == "invalid_request"


def test_an_oversized_body_is_refused_before_it_is_read(api):
    h = api()
    response = h.client.post("/api/chat", content="x" * 20_000, headers={"Content-Type": "application/json"})
    assert response.status_code == 413 and response.json()["error"] == "payload_too_large"


# ── failures: recorded, linked, never returned as answers ────────────────────
def test_output_that_stays_invalid_is_an_error_response_with_linked_failure_logs(api):
    h = api().script(corrupt(), corrupt())
    response = h.chat()
    body = response.json()
    assert response.status_code == 200 and body["status"] == "error" and body["claims"] == []

    logs = h.failure_logs()
    assert len(logs) == 2 and {log.failure_category for log in logs} == {"schema_validation_failure"}
    assert {str(log.message_id) for log in logs} == {body["message_id"]}
    assert logs[0].user_question == CHICKEN_Q and logs[0].retrieved_chunks and logs[0].model_info == "fake/model"
    assert [m.status for m in h.messages()] == [None, "error"]


def test_a_repaired_answer_still_records_the_failed_attempt(api):
    h = api().script(corrupt(), good_chicken())
    body = h.chat().json()
    assert body["status"] == "answered"
    [log] = h.failure_logs()
    assert str(log.message_id) == body["message_id"]


def test_a_failing_pipeline_is_a_503_and_leaves_no_half_conversation(api):
    h = api().script(RuntimeError("vector store exploded"))
    response = h.chat()
    assert response.status_code == 503 and response.json()["error"] == "service_unavailable"
    assert "exploded" not in response.text
    assert h.conversations() == [] and h.messages() == []


def test_a_slow_turn_times_out_with_a_504_and_is_not_stored_afterwards(api, api_env):
    api_env(CHAT_TIMEOUT_SECONDS="0.1")
    h = api(SlowLLM(0.5, good_chicken()))
    response = h.chat()
    assert response.status_code == 504 and response.json()["error"] == "timeout"
    time.sleep(0.9)  # let the abandoned worker finish
    assert h.conversations() == [] and h.messages() == []


def test_a_timed_out_turn_still_stores_the_failures_it_recorded(api, api_env):
    api_env(CHAT_TIMEOUT_SECONDS="0.1")
    h = api(SlowLLM(0.3, corrupt(), good_chicken()))
    assert h.chat().status_code == 504
    time.sleep(1.2)
    logs = h.failure_logs()
    assert [log.failure_category for log in logs] == ["schema_validation_failure"]
    assert logs[0].message_id is None and h.messages() == []


def test_chat_pipelines_never_exceed_the_concurrency_limit(api):
    llm = SlowLLM(0.1, *[good_chicken() for _ in range(6)])
    h = api(llm, concurrency=2)
    with ThreadPoolExecutor(6) as pool:
        statuses = list(pool.map(lambda _: h.chat().status_code, range(6)))
    assert statuses == [200] * 6
    assert 1 <= llm.max_active <= 2 and len(h.conversations()) == 6


# ── rate limiting ────────────────────────────────────────────────────────────
def test_chat_is_rate_limited_per_client(api, api_env):
    api_env(RATE_LIMIT_CHAT_PER_MINUTE=2)
    h = api().script(good_chicken(), good_chicken())
    assert [h.chat().status_code for _ in range(2)] == [200, 200]
    limited = h.chat()
    assert limited.status_code == 429 and limited.json()["error"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1
    assert len(h.llm.requests) == 2  # the limited request did no work
    assert h.client.get("/api/conversations").status_code == 200  # a separate budget

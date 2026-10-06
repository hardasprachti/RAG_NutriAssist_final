"""/api/conversations: create, list, read back (including after a server restart)."""

import pytest

from core.chat_service import conversation_title
from integrations import db_client
from tests.api_support import CHICKEN_Q, RESTRICTED_Q, SUGAR_Q, good_chicken
from tests.support import WHO_SUGAR, answered

UNKNOWN_ID = "5b0b6f0e-0000-4000-8000-000000000000"


# ── create ───────────────────────────────────────────────────────────────────
def test_create_returns_201_with_an_empty_untitled_conversation(api):
    h = api()
    response = h.client.post("/api/conversations")
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "title", "created_at", "updated_at"} and body["title"] is None
    detail = h.client.get(f"/api/conversations/{body['id']}").json()
    assert detail["messages"] == []


def test_create_accepts_a_title_and_treats_a_blank_one_as_untitled(api):
    h = api()
    assert h.client.post("/api/conversations", json={"title": "  Meal prep  "}).json()["title"] == "Meal prep"
    assert h.client.post("/api/conversations", json={"title": "   "}).json()["title"] is None
    assert h.client.post("/api/conversations", json={}).json()["title"] is None


@pytest.mark.parametrize("payload", [{"title": "t" * 256}, {"title": "a\x00b"}, {"title": 5}], ids=["long", "nul", "type"])
def test_create_rejects_an_invalid_title(api, payload):
    response = api().client.post("/api/conversations", json=payload)
    assert response.status_code == 422 and response.json()["error"] == "invalid_request"


def test_a_title_of_exactly_255_characters_is_stored(api):
    assert len(api().client.post("/api/conversations", json={"title": "t" * 255}).json()["title"]) == 255


# ── list ─────────────────────────────────────────────────────────────────────
def test_list_is_most_recently_active_first(api):
    h = api().script(good_chicken())
    first = h.client.post("/api/conversations", json={"title": "first"}).json()
    second = h.client.post("/api/conversations", json={"title": "second"}).json()
    assert [c["id"] for c in h.client.get("/api/conversations").json()] == [second["id"], first["id"]]

    h.chat(CHICKEN_Q, first["id"])  # activity moves a conversation to the top
    assert [c["id"] for c in h.client.get("/api/conversations").json()] == [first["id"], second["id"]]


def test_list_of_nothing_is_an_empty_array(api):
    response = api().client.get("/api/conversations")
    assert response.status_code == 200 and response.json() == []


def test_list_limit_is_applied_and_validated(api):
    h = api()
    for _ in range(3):
        h.client.post("/api/conversations")
    assert len(h.client.get("/api/conversations?limit=2").json()) == 2
    for bad in ("0", "101", "abc"):
        assert h.client.get(f"/api/conversations?limit={bad}").status_code == 422


# ── read ─────────────────────────────────────────────────────────────────────
def test_detail_returns_messages_in_order_with_claims_and_sources(api):
    h = api().script(good_chicken(), answered(WHO_SUGAR, "Free sugars should be limited to less than 10% of total daily energy intake."))
    first = h.chat(CHICKEN_Q).json()
    h.chat(SUGAR_Q, first["conversation_id"])
    h.chat(RESTRICTED_Q, first["conversation_id"])

    detail = h.client.get(f"/api/conversations/{first['conversation_id']}").json()
    assert [(m["role"], m["status"]) for m in detail["messages"]] == [
        ("user", None), ("assistant", "answered"),
        ("user", None), ("assistant", "answered"),
        ("user", None), ("assistant", "out_of_scope"),
    ]
    user, answered_msg, *_, refusal = detail["messages"]
    assert user["content"] == CHICKEN_Q and user["claims"] == [] and user["retrieved_sources"] == []
    assert answered_msg["claims"][0]["source"]["chunk_id"] and answered_msg["retrieved_sources"]
    assert "text" in answered_msg["retrieved_sources"][0]
    assert refusal["claims"] == [] and refusal["retrieved_sources"] == [] and refusal["refusal_reason"]
    assert detail["title"] == CHICKEN_Q and detail["created_at"] <= detail["updated_at"]


def test_a_stored_answer_reads_back_exactly_as_it_was_returned(api):
    h = api().script(good_chicken())
    live = h.chat().json()
    stored = h.client.get(f"/api/conversations/{live['conversation_id']}").json()["messages"][1]
    assert stored["id"] == live["message_id"]
    assert stored["content"] == live["answer"] and stored["status"] == live["status"]
    assert stored["claims"] == live["claims"] and stored["refusal_reason"] == live["refusal_reason"]
    assert stored["retrieved_sources"] == live["retrieved_sources"]


def test_history_survives_a_server_restart(api, api_env):
    h = api().script(good_chicken())
    live = h.chat().json()
    cid = live["conversation_id"]

    # A restart drops every in-process object; only the database file is left.
    h.close()
    api_env()
    from integrations.db_client import get_engine

    assert get_engine() is not None
    h2 = api(client_id=h.client_id)
    detail = h2.client.get(f"/api/conversations/{cid}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["retrieved_sources"] == live["retrieved_sources"]
    assert [c["id"] for c in h2.client.get("/api/conversations").json()] == [cid]


def test_the_conversation_continues_after_a_restart_with_its_history(api, api_env):
    h = api().script(good_chicken())
    cid = h.chat().json()["conversation_id"]
    h.close()
    api_env()
    h2 = api(client_id=h.client_id).script(good_chicken())
    assert h2.chat("and in the freezer?", cid).status_code == 200
    assert "CONVERSATION HISTORY" in h2.llm.requests[0][1]["content"] and CHICKEN_Q in h2.llm.requests[0][1]["content"]
    assert len(h2.client.get(f"/api/conversations/{cid}").json()["messages"]) == 4


def test_unknown_conversation_is_a_404_envelope(api):
    response = api().client.get(f"/api/conversations/{UNKNOWN_ID}")
    assert response.status_code == 404
    assert response.json() == {"error": "conversation_not_found", "message": "That conversation does not exist.", "detail": None}


def test_a_malformed_conversation_id_is_a_422_envelope(api):
    response = api().client.get("/api/conversations/not-a-uuid")
    assert response.status_code == 422 and response.json()["error"] == "invalid_request"


def test_timestamps_are_timezone_aware(api):
    h = api()
    h.client.post("/api/conversations")
    stamp = h.client.get("/api/conversations").json()[0]["created_at"]
    assert stamp.endswith("Z") or stamp.endswith("+00:00")


def test_api_is_rate_limited_per_client(api, api_env):
    api_env(RATE_LIMIT_API_PER_MINUTE=3)
    h = api()
    assert [h.client.get("/api/conversations").status_code for _ in range(3)] == [200, 200, 200]
    limited = h.client.get("/api/conversations")
    assert limited.status_code == 429 and "Retry-After" in limited.headers
    assert h.client.get("/api/health").status_code != 429  # health is never limited


# ── rename ───────────────────────────────────────────────────────────────────
def test_rename_changes_the_title_and_keeps_the_conversations_place_in_the_list(api):
    h = api()
    first = h.client.post("/api/conversations", json={"title": "first"}).json()
    second = h.client.post("/api/conversations", json={"title": "second"}).json()

    response = h.client.patch(f"/api/conversations/{first['id']}", json={"title": "  Meal prep  "})
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Meal prep" and body["updated_at"] == first["updated_at"]
    assert [c["id"] for c in h.client.get("/api/conversations").json()] == [second["id"], first["id"]]
    assert h.client.get(f"/api/conversations/{first['id']}").json()["title"] == "Meal prep"


def test_a_renamed_conversation_keeps_its_title_when_chatting_continues(api):
    h = api().script(good_chicken())
    cid = h.chat().json()["conversation_id"]
    h.client.patch(f"/api/conversations/{cid}", json={"title": "Chicken"})
    h.chat(CHICKEN_Q, cid)
    assert h.client.get(f"/api/conversations/{cid}").json()["title"] == "Chicken"


@pytest.mark.parametrize("payload", [{}, {"title": ""}, {"title": "   "}, {"title": "t" * 256}, {"title": "a\x00b"}, {"title": None}])
def test_rename_rejects_a_missing_blank_or_invalid_title(api, payload):
    h = api()
    cid = h.client.post("/api/conversations", json={"title": "keep"}).json()["id"]
    response = h.client.patch(f"/api/conversations/{cid}", json=payload)
    assert response.status_code == 422 and response.json()["error"] == "invalid_request"
    assert h.client.get(f"/api/conversations/{cid}").json()["title"] == "keep"


def test_rename_of_an_unknown_conversation_is_a_404_envelope(api):
    response = api().client.patch(f"/api/conversations/{UNKNOWN_ID}", json={"title": "x"})
    assert response.status_code == 404 and response.json()["error"] == "conversation_not_found"


# ── delete ───────────────────────────────────────────────────────────────────
def test_delete_removes_only_that_conversation_with_its_messages_and_chunks(api):
    h = api().script(good_chicken(), good_chicken())
    keep = h.chat().json()["conversation_id"]
    gone = h.chat().json()["conversation_id"]
    assert keep != gone and len(h.messages()) == 4 and h.stored_chunks()

    response = h.client.delete(f"/api/conversations/{gone}")
    assert response.status_code == 204 and response.content == b""
    assert [c["id"] for c in h.client.get("/api/conversations").json()] == [keep]
    assert h.client.get(f"/api/conversations/{gone}").status_code == 404
    assert [str(m.conversation_id) for m in h.messages()] == [keep, keep]
    assert {c.message_id for c in h.stored_chunks()} == {m.id for m in h.messages() if m.role == "assistant"}
    assert len(h.client.get(f"/api/conversations/{keep}").json()["messages"]) == 2


def test_delete_keeps_failure_logs_but_unlinks_them(api):
    h = api().script(good_chicken())
    live = h.chat().json()
    with db_client.session_scope() as session:
        db_client.save_failure_log(
            session, "llm_error", CHICKEN_Q, message_id=__import__("uuid").UUID(live["message_id"])
        )
    assert h.client.delete(f"/api/conversations/{live['conversation_id']}").status_code == 204
    [log] = h.failure_logs()
    assert log.message_id is None and log.user_question == CHICKEN_Q


def test_delete_twice_and_delete_of_an_unknown_conversation_are_404s(api):
    h = api()
    cid = h.client.post("/api/conversations").json()["id"]
    assert h.client.delete(f"/api/conversations/{cid}").status_code == 204
    for target in (cid, UNKNOWN_ID):
        response = h.client.delete(f"/api/conversations/{target}")
        assert response.status_code == 404 and response.json()["error"] == "conversation_not_found"


def test_chatting_into_a_deleted_conversation_is_a_404_and_stores_nothing(api):
    h = api().script(good_chicken())
    cid = h.chat().json()["conversation_id"]
    h.client.delete(f"/api/conversations/{cid}")
    assert h.chat(CHICKEN_Q, cid).status_code == 404
    assert h.conversations() == [] and h.messages() == []


# ── independence ─────────────────────────────────────────────────────────────
def test_conversations_do_not_share_history(api):
    h = api().script(good_chicken(), answered(WHO_SUGAR, "Free sugars should be limited to less than 10% of total daily energy intake."), good_chicken())
    a = h.chat(CHICKEN_Q).json()["conversation_id"]
    b = h.chat(SUGAR_Q).json()["conversation_id"]
    h.chat("and in the freezer?", a)
    assert a != b
    follow_up_prompt = h.llm.requests[2][1]["content"]
    assert CHICKEN_Q in follow_up_prompt and SUGAR_Q not in follow_up_prompt
    assert [m["role"] for m in h.client.get(f"/api/conversations/{a}").json()["messages"]] == ["user", "assistant"] * 2
    assert [m["role"] for m in h.client.get(f"/api/conversations/{b}").json()["messages"]] == ["user", "assistant"]


def test_a_restricted_question_in_one_conversation_does_not_block_another(api):
    h = api().script(good_chicken())
    a = h.client.post("/api/conversations").json()["id"]
    b = h.client.post("/api/conversations").json()["id"]
    assert h.chat(RESTRICTED_Q, a).json()["status"] == "out_of_scope"
    assert h.chat(CHICKEN_Q, b).json()["status"] == "answered"


# ── titles ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "question,expected",
    [
        ("How long does chicken last?", "How long does chicken last?"),
        ("  How   long\n does\tchicken last?  ", "How long does chicken last?"),
        ("word " * 30, "word word word word word word word word word word word word…"),
    ],
)
def test_conversation_title(question, expected):
    assert conversation_title(question) == expected


def test_conversation_title_never_exceeds_the_limit_even_without_spaces():
    assert len(conversation_title("x" * 500)) <= 60


def test_db_helper_get_recent_messages_returns_the_tail_oldest_first(api):
    h = api().script(good_chicken(), good_chicken())
    cid = h.chat().json()["conversation_id"]
    h.chat(CHICKEN_Q, cid)
    with db_client.session_scope() as session:
        import uuid

        tail = db_client.get_recent_messages(session, uuid.UUID(cid), 3)
        assert [m.role for m in tail] == ["assistant", "user", "assistant"]
        assert db_client.get_recent_messages(session, uuid.UUID(cid), 0) == []

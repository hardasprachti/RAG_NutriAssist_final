"""Corner cases from docs/edge_case.md for phases 3-5: who owns a conversation, a hung provider call, text the
English-only pipeline cannot judge, and figures whose unit changed."""

import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from core.chat_service import Abandoned
from core.failure_logger import FailureCategory as C
from core.rag_pipeline import input_problem
from core.response_validator import ResponseValidator, number_units, unit_mismatches
from integrations.db_client import session_scope
from models.schemas import ChatRequest
from tests.api_support import CHICKEN_Q, SUGAR_Q, good_chicken
from tests.support import EAT_SUGAR, FakeLLM, WHO_SUGAR, answered, hit

UNKNOWN_ID = "5b0b6f0e-0000-4000-8000-000000000000"


# ── who owns a conversation ──────────────────────────────────────────────────
class TestOwnership:
    def test_every_conversation_route_requires_a_well_formed_client_id(self, api):
        h = api()
        anonymous = TestClient(h.app)
        cid = h.client.post("/api/conversations").json()["id"]
        for response in (
            anonymous.get("/api/conversations"),
            anonymous.post("/api/conversations"),
            anonymous.get(f"/api/conversations/{cid}"),
            anonymous.patch(f"/api/conversations/{cid}", json={"title": "x"}),
            anonymous.delete(f"/api/conversations/{cid}"),
            anonymous.post("/api/chat", json={"question": CHICKEN_Q}),
        ):
            assert response.status_code == 422 and response.json()["error"] == "invalid_request"
        bad = TestClient(h.app, headers={"X-Client-Id": "not-a-uuid"})
        assert bad.get("/api/conversations").status_code == 422
        assert h.client.get(f"/api/conversations/{cid}").status_code == 200  # nothing was changed or deleted

    def test_health_needs_no_client_id(self, api):
        assert TestClient(api().app).get("/api/health").status_code != 422

    def test_a_visitor_lists_only_their_own_conversations(self, api):
        h = api().script(good_chicken())
        mine = h.chat().json()["conversation_id"]
        other = h.another_visitor()
        theirs = other.post("/api/conversations", json={"title": "theirs"}).json()["id"]

        assert [c["id"] for c in h.client.get("/api/conversations").json()] == [mine]
        assert [c["id"] for c in other.get("/api/conversations").json()] == [theirs]

    def test_another_visitor_cannot_read_rename_delete_or_continue_a_conversation(self, api):
        h = api().script(good_chicken())
        cid = h.chat().json()["conversation_id"]
        stranger = h.another_visitor()

        for response in (
            stranger.get(f"/api/conversations/{cid}"),
            stranger.patch(f"/api/conversations/{cid}", json={"title": "hijacked"}),
            stranger.delete(f"/api/conversations/{cid}"),
            stranger.post("/api/chat", json={"conversation_id": cid, "question": CHICKEN_Q}),
        ):  # indistinguishable from a conversation that does not exist
            assert response.status_code == 404 and response.json()["error"] == "conversation_not_found"

        detail = h.client.get(f"/api/conversations/{cid}").json()
        assert detail["title"] == CHICKEN_Q and len(detail["messages"]) == 2  # untouched, nothing added
        assert len(h.messages()) == 2

    def test_a_new_conversation_belongs_to_whoever_started_it(self, api):
        h = api().script(good_chicken())
        h.chat()
        [conversation] = h.conversations()
        assert str(conversation.owner_id) == h.client_id

    def test_conversations_from_before_owners_existed_belong_to_nobody(self, api):
        h = api()
        legacy = h.client.post("/api/conversations").json()["id"]
        with session_scope() as session:  # what the migration leaves on rows that predate owners
            session.execute(text("UPDATE conversations SET owner_id = NULL"))
        assert h.client.get("/api/conversations").json() == []
        assert h.client.get(f"/api/conversations/{legacy}").status_code == 404

    def test_the_same_browser_keeps_its_conversations_across_a_restart(self, api):
        h = api().script(good_chicken())
        cid = h.chat().json()["conversation_id"]
        again = api(client_id=h.client_id)
        assert [c["id"] for c in again.client.get("/api/conversations").json()] == [cid]

    def test_cors_allows_the_client_id_header(self):
        from main import app

        response = TestClient(app).options(
            "/api/conversations",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-client-id",
            },
        )
        assert response.status_code == 200
        assert "x-client-id" in response.headers["access-control-allow-headers"].lower()


# ── a hung provider call must not keep its slot ──────────────────────────────
class BlockingLLM(FakeLLM):
    """The first call never returns until released; later calls answer at once."""

    def __init__(self, *script):
        super().__init__(*script)
        self.release_first = threading.Event()
        self.first_started = threading.Event()
        self._calls = 0

    def generate_structured(self, messages):
        self._calls += 1
        if self._calls == 1:
            self.first_started.set()
            assert self.release_first.wait(15), "the test never released the hung call"
        return super().generate_structured(messages)


class TestHungProviderCall:
    def test_an_abandoned_turn_frees_its_slot_even_though_its_call_is_still_running(self, api):
        llm = BlockingLLM(good_chicken(), good_chicken())
        h = api(llm, concurrency=1)
        owner = uuid.UUID(h.client_id)
        abandoned = Abandoned()
        hung = {}

        def first_turn():
            hung["result"] = h.service.handle(ChatRequest(question=CHICKEN_Q), owner, abandoned)

        worker = threading.Thread(target=first_turn)
        worker.start()
        assert llm.first_started.wait(5)

        # The request gives up (what routers/chat.py does on a timeout) while the provider call hangs.
        abandoned.set()

        started = time.monotonic()
        second = h.service.handle(ChatRequest(question=CHICKEN_Q), owner)  # concurrency is 1: needs the slot
        assert second is not None and second.status.value == "answered"
        assert time.monotonic() - started < 5

        llm.release_first.set()
        worker.join(10)
        assert hung["result"] is None  # the late result is dropped, not stored
        assert len(h.messages()) == 2  # only the second turn was stored

    def test_a_turn_waiting_for_a_slot_gives_up_when_abandoned(self, api):
        llm = BlockingLLM(good_chicken())
        h = api(llm, concurrency=1)
        owner = uuid.UUID(h.client_id)
        busy = threading.Thread(target=lambda: h.service.handle(ChatRequest(question=CHICKEN_Q), owner))
        busy.start()
        assert llm.first_started.wait(5)

        waiting = Abandoned()
        outcome = {}
        queued = threading.Thread(
            target=lambda: outcome.setdefault("r", h.service.handle(ChatRequest(question=SUGAR_Q), owner, waiting))
        )
        queued.start()
        time.sleep(0.4)
        waiting.set()
        queued.join(3)
        assert not queued.is_alive() and outcome["r"] is None  # did not wait for the busy slot forever

        llm.release_first.set()
        busy.join(10)

    def test_a_turn_that_finishes_normally_releases_its_slot_exactly_once(self, api):
        h = api(concurrency=1).script(good_chicken(), good_chicken(), good_chicken())
        for _ in range(3):  # a leaked slot would block the second turn, a double release would raise
            assert h.chat().status_code == 200


# ── input the English-only pipeline cannot judge ─────────────────────────────
@pytest.mark.parametrize("text", ["", " ", "🍎🍌🥦", "?!", "12345", "1"])
def test_text_without_words_is_not_answerable(text):
    assert input_problem(text) == "no_text"


@pytest.mark.parametrize(
    "text",
    [
        "वजन कम करने के लिए मुझे कितनी कैलोरी खानी चाहिए?",  # Hindi
        "ما هي كمية السعرات الحرارية اليومية؟",  # Arabic
        "每天应该吃多少卡路里？",  # Chinese
        "Сколько калорий нужно есть в день?",  # Russian
    ],
)
def test_text_in_another_script_is_not_answerable(text):
    assert input_problem(text) == "unsupported_script"


@pytest.mark.parametrize(
    "text",
    [
        "hi",
        "How long can raw chicken stay in the fridge?",
        "What does WHO say about iron? 🍳",  # an emoji alongside real words
        "How much vitamin B12 do adults need (µg per day)?",  # Greek letters inside an English question
        "¿Cuánto tiempo puede estar el pollo crudo en el refrigerador?",  # Latin-script Spanish still goes on
        "What is naïve Bayes? café crème brûlée",
    ],
)
def test_ordinary_questions_pass_the_input_guard(text):
    assert input_problem(text) is None


def test_the_pipeline_answers_unreadable_input_without_retrieval_or_a_model_call(api):
    h = api()
    for question in ("🍎🍌🥦", "वजन कम करने के लिए मुझे कितनी कैलोरी खानी चाहिए?"):
        body = h.chat(question).json()
        assert body["status"] == "not_in_corpus" and body["claims"] == [] and body["retrieved_sources"] == []
        assert body["refusal_reason"]
    assert h.llm.requests == []  # the model was never asked
    assert len(h.messages()) == 4  # both turns are stored like any refusal


def test_a_non_latin_restricted_request_never_reaches_the_model(api):
    h = api()
    body = h.chat("वजन कम करने के लिए मुझे कितनी कैलोरी खानी चाहिए?").json()
    assert body["status"] != "answered" and h.llm.requests == []


# ── figures whose unit changed ───────────────────────────────────────────────
def test_units_are_read_next_to_their_number():
    assert number_units("15 mcg or 600 IU, 2 days, 1,000 mg, 30%, 25 µg, 30g") == {
        "15": {"mcg"}, "600": {"iu"}, "2": {"day"}, "1000": {"mg"}, "30": {"%", "g"}, "25": {"mcg"},
    }
    assert number_units("2 large eggs and 5 litres") == {"2": {""}, "5": {"l"}}  # "large" is not the unit "l"


@pytest.mark.parametrize(
    "claim, chunk, expected",
    [
        ("Adults need 15 mg of vitamin D", "The RDA is 15 mcg (600 IU)", ["15 mg"]),
        ("Keep it for 2 days", "Refrigerator: 2 weeks", ["2 day"]),
        ("Limit salt to 5 mg a day", "less than 5 g of salt per day", ["5 mg"]),
        ("Eat 10 days of fibre", "less than 10% of total energy", ["10 day"]),
    ],
)
def test_a_figure_in_the_wrong_unit_is_flagged(claim, chunk, expected):
    assert [m.split(" (")[0] for m in unit_mismatches(claim, chunk)] == expected


@pytest.mark.parametrize(
    "claim, chunk",
    [
        ("Adults need 15 mcg", "The RDA is 15 µg (600 IU)"),  # same unit, different spelling
        ("Adults need 15 micrograms", "The RDA is 15 mcg"),
        ("Keep it for 2 days", "| Product | Days |\n| chicken | 2 |"),  # the unit is in a column header
        ("Keep it for 1 - 2 days", "chicken 1 - 2 days, giblets 3 - 4 months"),
        ("Less than 5 g of salt", "less than 5 g of salt per day (2 g sodium)"),
        ("Adults need 15 mcg", "no figures at all here"),  # a missing number is the digit check's job
        ("About 600 IU or 15 mcg", "15 mcg (600 IU)"),
    ],
)
def test_a_figure_in_the_same_unit_or_with_no_unit_to_compare_is_not_flagged(claim, chunk):
    assert unit_mismatches(claim, chunk) == []


def test_the_validator_blocks_a_claim_whose_unit_differs_from_its_source():
    validator = ResponseValidator()
    wrong = answered(EAT_SUGAR, "Adults should have no more than 30 mg of free sugars a day.")
    result = validator.validate(wrong, [hit(EAT_SUGAR), hit(WHO_SUGAR)])
    assert not result.ok
    assert any(v.category is C.unsupported_claim and "unit mismatch" in v.description for v in result.blocking)

    right = answered(EAT_SUGAR, "Adults should have no more than 30 g of free sugars a day.")
    assert validator.validate(right, [hit(EAT_SUGAR)]).ok


def test_the_unit_check_does_not_misread_a_percentage_as_days():
    validator = ResponseValidator()
    wrong = answered(WHO_SUGAR, "Free sugars should be limited to less than 10 days of total daily energy intake.")
    assert not validator.validate(wrong, [hit(WHO_SUGAR)]).ok
    right = answered(WHO_SUGAR, "Free sugars should be limited to less than 10% of total daily energy intake.")
    assert validator.validate(right, [hit(WHO_SUGAR)]).ok


def test_a_correct_table_answer_still_passes_end_to_end(api):
    h = api().script(good_chicken())  # "1 - 2 days" from a table row: units agree
    assert h.chat().json()["status"] == "answered"

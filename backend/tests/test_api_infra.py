"""Cross-cutting API behaviour: the error envelope, rate limiter, failure buffer, LLM probe, OpenAPI."""

import logging
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from core.failure_logger import DeferredFailures
from core.rate_limit import RateLimiter
from integrations.llm_client import LLMClient
from routers.errors import ApiError, register_body_limit, register_error_handlers

ORIGIN = "http://localhost:3000"


# ── error envelope ───────────────────────────────────────────────────────────
def make_app() -> TestClient:
    """The same wiring order as main.py, with routes that fail in the ways production can."""
    app = FastAPI()
    register_error_handlers(app)
    register_body_limit(app, 100)
    app.add_middleware(CORSMiddleware, allow_origins=[ORIGIN], allow_methods=["GET", "POST"])

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internal detail")

    @app.get("/db")
    def db():
        raise OperationalError("SELECT 1", {}, Exception("connection refused: postgres://user:pw@host"))

    @app.get("/teapot")
    def teapot():
        raise HTTPException(418, "I am a teapot")

    @app.get("/api-error")
    def api_error():
        raise ApiError(409, "conflict", "Already exists.", detail="id 7", headers={"X-Thing": "1"})

    @app.post("/echo")
    def echo(body: dict):
        return body

    return TestClient(app, raise_server_exceptions=False)


def test_unhandled_errors_are_a_generic_500_envelope_with_cors_headers():
    response = make_app().get("/boom", headers={"Origin": ORIGIN})
    assert response.status_code == 500
    assert response.json() == {
        "error": "internal_error", "message": "Something went wrong on our side. Please try again.", "detail": None,
    }
    assert "secret" not in response.text
    # Without CORS headers the browser would hide this response behind an opaque network error.
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_unhandled_errors_are_logged_with_their_traceback(caplog):
    with caplog.at_level(logging.ERROR):
        make_app().get("/boom")
    record = next(r for r in caplog.records if r.message == "unhandled exception")
    assert record.exc_info and "secret internal detail" in str(record.exc_info[1])


def test_a_lost_database_is_a_503_that_does_not_leak_the_connection_string():
    response = make_app().get("/db")
    assert response.status_code == 503 and response.json()["error"] == "service_unavailable"
    assert "postgres://" not in response.text and "pw" not in response.text


def test_http_exceptions_use_the_envelope():
    response = make_app().get("/teapot")
    assert response.status_code == 418
    assert response.json() == {"error": "http_error", "message": "I am a teapot", "detail": None}


def test_unknown_routes_and_methods_use_the_envelope():
    client = make_app()
    assert client.get("/nope").json()["error"] == "not_found"
    assert client.delete("/boom").json()["error"] == "method_not_allowed"


def test_api_error_carries_status_code_detail_and_headers():
    response = make_app().get("/api-error")
    assert response.status_code == 409 and response.headers["x-thing"] == "1"
    assert response.json() == {"error": "conflict", "message": "Already exists.", "detail": "id 7"}


def test_validation_errors_name_the_field_and_reason():
    response = make_app().post("/echo", content="[1, 2]", headers={"Content-Type": "application/json"})
    body = response.json()
    assert response.status_code == 422 and body["error"] == "invalid_request"
    assert body["message"] == "The request was not valid." and body["detail"]


def test_body_limit_uses_the_envelope_and_cors():
    response = make_app().post(
        "/echo", content="x" * 200, headers={"Content-Type": "application/json", "Origin": ORIGIN}
    )
    assert response.status_code == 413 and response.json()["error"] == "payload_too_large"
    assert response.headers["access-control-allow-origin"] == ORIGIN


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
def test_the_real_app_answers_a_cors_preflight_for_rename_and_delete(method):
    from main import app

    response = TestClient(app).options(
        "/api/conversations/5b0b6f0e-0000-4000-8000-000000000000",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": method, "Access-Control-Request-Headers": "content-type"},
    )
    assert response.status_code == 200 and response.headers["access-control-allow-origin"] == ORIGIN


def test_the_real_app_answers_a_cors_preflight_for_chat():
    from main import app

    response = TestClient(app).options(
        "/api/chat",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"},
    )
    assert response.status_code == 200 and response.headers["access-control-allow-origin"] == ORIGIN


# ── OpenAPI ──────────────────────────────────────────────────────────────────
def test_openapi_documents_every_endpoint_and_the_error_envelope():
    from main import app

    spec = TestClient(app).get("/openapi.json").json()
    assert {"/api/chat", "/api/conversations", "/api/conversations/{conversation_id}", "/api/health"} <= set(spec["paths"])
    assert set(spec["paths"]["/api/conversations"]) == {"get", "post"}

    def schema_name(path, method, status):
        schema = spec["paths"][path][method]["responses"][status]["content"]["application/json"]["schema"]
        return schema["$ref"].rsplit("/", 1)[-1]

    assert schema_name("/api/chat", "post", "200") == "ChatResponse"
    for status in ("404", "422", "429", "503", "504"):
        assert schema_name("/api/chat", "post", status) == "ErrorEnvelope"
    assert schema_name("/api/conversations", "post", "201") == "ConversationSummary"
    assert schema_name("/api/conversations/{conversation_id}", "get", "200") == "ConversationDetail"
    assert schema_name("/api/conversations/{conversation_id}", "get", "404") == "ErrorEnvelope"
    assert schema_name("/api/conversations/{conversation_id}", "get", "422") == "ErrorEnvelope"
    assert schema_name("/api/conversations/{conversation_id}", "patch", "200") == "ConversationSummary"
    assert schema_name("/api/conversations/{conversation_id}", "patch", "404") == "ErrorEnvelope"
    assert schema_name("/api/conversations/{conversation_id}", "delete", "404") == "ErrorEnvelope"
    assert schema_name("/api/health", "get", "503") == "HealthResponse"
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    fields = set(spec["components"]["schemas"]["ChatResponse"]["properties"])
    assert {"conversation_id", "message_id", "answer", "claims", "status", "refusal_reason", "retrieved_sources"} <= fields


# ── rate limiter ─────────────────────────────────────────────────────────────
class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_rate_limiter_allows_up_to_the_limit_then_reports_when_to_retry():
    clock = Clock()
    limiter = RateLimiter(3, window=60, clock=clock)
    assert [limiter.check("a") for _ in range(3)] == [None, None, None]
    clock.now += 10
    assert limiter.check("a") == 50  # the oldest request leaves the window 50 s from now
    clock.now += 50
    assert limiter.check("a") is None


def test_rate_limiter_window_slides_rather_than_resetting():
    clock = Clock()
    limiter = RateLimiter(2, window=60, clock=clock)
    limiter.check("a")
    clock.now += 40
    limiter.check("a")
    clock.now += 21  # the first request has expired, the second has not
    assert limiter.check("a") is None
    assert limiter.check("a") is not None


def test_rate_limiter_keys_are_independent():
    limiter = RateLimiter(1, clock=Clock())
    assert limiter.check("a") is None and limiter.check("b") is None
    assert limiter.check("a") is not None


def test_a_rejected_request_does_not_extend_the_block():
    clock = Clock()
    limiter = RateLimiter(1, window=60, clock=clock)
    limiter.check("a")
    for _ in range(5):
        clock.now += 5
        limiter.check("a")
    clock.now += 40  # 65 s after the one accepted request
    assert limiter.check("a") is None


@pytest.mark.parametrize("limit", [0, -1])
def test_rate_limiter_can_be_disabled(limit):
    limiter = RateLimiter(limit, clock=Clock())
    assert all(limiter.check("a") is None for _ in range(1000))


def test_rate_limiter_forgets_quiet_clients():
    clock = Clock()
    limiter = RateLimiter(5, window=60, clock=clock)
    for i in range(300):
        limiter.check(f"client-{i}")
    clock.now += 120
    for _ in range(300):
        limiter.check("active")
    assert set(limiter._hits) == {"active"}


# ── deferred failure records ─────────────────────────────────────────────────
def test_deferred_failures_hold_records_until_drained():
    saved = []
    deferred = DeferredFailures(save=lambda **f: saved.append(f))
    deferred(failure_category="a", message_id=None)
    deferred(failure_category="b", message_id=None)
    assert saved == []
    assert [r["failure_category"] for r in deferred.drain()] == ["a", "b"]
    assert deferred.drain() == []


def test_unlinked_flush_stores_everything_once():
    saved = []
    deferred = DeferredFailures(save=lambda **f: saved.append(f))
    deferred(failure_category="a", message_id=None)
    assert deferred.flush_unlinked() == 1 and deferred.flush_unlinked() == 0
    assert [r["failure_category"] for r in saved] == ["a"]


def test_a_failed_unlinked_write_is_logged_not_raised(caplog):
    def broken(**_fields):
        raise RuntimeError("db down")

    deferred = DeferredFailures(save=broken)
    deferred(failure_category="a", user_question="q?", message_id=None)
    with caplog.at_level(logging.ERROR):
        assert deferred.flush_unlinked() == 0
    record = next(r for r in caplog.records if r.message == "could not store failure log")
    assert "q?" in record.failure_record


# ── LLM availability probe ───────────────────────────────────────────────────
class FakeProviderClient:
    def __init__(self, offered=("openai/gpt-oss-120b", "other/model"), error=None):
        self.error, self.options, self.listed = error, None, 0
        self._offered = offered
        self.models = SimpleNamespace(list=self._list)

    def with_options(self, **options):
        self.options = options
        return self

    def _list(self):
        self.listed += 1
        if self.error:
            raise self.error
        return SimpleNamespace(data=[SimpleNamespace(id=m) for m in self._offered])


def test_probe_lists_models_with_a_short_timeout_and_no_retries():
    fake = FakeProviderClient()
    assert LLMClient("key", "openai/gpt-oss-120b", client=fake).check_available() is True
    assert fake.listed == 1 and fake.options == {"timeout": 5.0, "max_retries": 0}


def test_probe_is_false_when_the_provider_does_not_offer_the_model():
    fake = FakeProviderClient(offered=("other/model",))
    assert LLMClient("key", "openai/gpt-oss-120b", client=fake).check_available() is False


def test_probe_reports_a_provider_failure_without_raising():
    fake = FakeProviderClient(error=ConnectionError("unreachable"))
    assert LLMClient("key", "m", client=fake).check_available() is False

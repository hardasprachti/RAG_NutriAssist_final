from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from config import get_settings, load_settings
from integrations.llm_client import LLMClient
from main import app
from routers import health
from tests.conftest import _reset_app_caches

# Bound at import, before the autouse fixture replaces the module attributes with fakes.
real_check_database = health.check_database
real_check_vector_store = health.check_vector_store
real_check_llm = health.check_llm

client = TestClient(app)


def test_health_reports_every_dependency():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "vector_store": "ok", "database": "ok", "llm": "ok"}


def test_an_unavailable_llm_degrades_health_but_is_still_a_200(monkeypatch):
    monkeypatch.setattr(health, "check_llm", lambda: health.UNAVAILABLE)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "degraded", "vector_store": "ok", "database": "ok", "llm": "unavailable"}


def test_a_missing_llm_key_is_reported_as_not_configured(monkeypatch):
    monkeypatch.setattr(health, "check_llm", lambda: health.NOT_CONFIGURED)
    body = client.get("/api/health").json()
    assert body["status"] == "degraded" and body["llm"] == "not_configured"


def test_vector_store_down_is_a_503(monkeypatch):
    monkeypatch.setattr(health, "check_vector_store", lambda: health.UNAVAILABLE)
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "vector_store": "unavailable", "database": "ok", "llm": "ok"}


def test_database_down_is_a_503(monkeypatch):
    monkeypatch.setattr(health, "check_database", lambda: health.UNAVAILABLE)
    response = client.get("/api/health")
    assert response.status_code == 503 and response.json()["database"] == "unavailable"


def test_the_real_database_check_runs_a_query(api_env):
    assert real_check_database() == "ok"


def test_the_real_database_check_reports_a_dead_database(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'missing' / 'x.db').as_posix()}")
    _reset_app_caches()
    try:
        assert real_check_database() == "unavailable"
    finally:
        _reset_app_caches()


@pytest.mark.parametrize("answers,expected", [(True, "ok"), (False, "unavailable")])
def test_the_real_vector_store_check_follows_the_store(monkeypatch, answers, expected):
    monkeypatch.setattr(health, "_vector_store", lambda: SimpleNamespace(check_connection=lambda: answers))
    assert real_check_vector_store() == expected


def test_the_real_vector_store_check_reports_unavailable_when_unconfigured(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "")
    get_settings.cache_clear()
    health._vector_store.cache_clear()
    try:
        assert real_check_vector_store() == "unavailable"
    finally:
        get_settings.cache_clear()


def test_the_real_llm_check_without_a_key_is_not_configured(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    get_settings.cache_clear()
    try:
        assert real_check_llm() == "not_configured"
    finally:
        get_settings.cache_clear()


def test_the_llm_probe_is_cached_for_a_minute(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(health, "_llm_cached", (0.0, ""))
    calls = []
    monkeypatch.setattr(LLMClient, "check_available", lambda self, timeout=5.0: calls.append(1) or False)
    try:
        assert [real_check_llm() for _ in range(3)] == ["unavailable"] * 3
        assert len(calls) == 1
        monkeypatch.setattr(health.time, "monotonic", lambda: health._llm_cached[0] + health.LLM_PROBE_TTL_S + 1)
        real_check_llm()
        assert len(calls) == 2
    finally:
        get_settings.cache_clear()


def test_cors_allows_configured_origin():
    response = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_rejects_unknown_origin():
    response = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_allowed_origins_parsed_from_env(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.vercel.app/, https://b.example")
    assert load_settings().allowed_origins == ("https://a.vercel.app", "https://b.example")


def _startup(monkeypatch, *, preload, embedder):
    import dataclasses

    import integrations.embedder as embedder_module
    import main

    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, preload_models=preload))
    monkeypatch.setattr(embedder_module, "get_embedder", lambda: embedder)
    with TestClient(main.app) as started:
        return started.get("/api/health").status_code


def test_startup_loads_embedding_model_once(monkeypatch):
    class Embedder:
        loads = 0

        def load(self):
            Embedder.loads += 1

    assert _startup(monkeypatch, preload=True, embedder=Embedder()) == 200
    assert Embedder.loads == 1


def test_startup_can_skip_model_preload(monkeypatch):
    class Embedder:
        def load(self):
            raise AssertionError("must not load")

    assert _startup(monkeypatch, preload=False, embedder=Embedder()) == 200


def test_startup_survives_and_logs_model_load_failure(monkeypatch, caplog):
    class Embedder:
        def load(self):
            raise RuntimeError("model download failed")

    assert _startup(monkeypatch, preload=True, embedder=Embedder()) == 200
    assert "embedding model failed to load at startup" in caplog.text

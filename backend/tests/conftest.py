"""Shared fixtures.

Data-layer tests run against two backends each:

* SQLite file / in-memory Qdrant: always available, fast.
* A real PostgreSQL / Qdrant server: only when TEST_DATABASE_URL / TEST_QDRANT_URL are set.
  Point these at a throwaway local server (e.g. Docker), never at Supabase or production:
  the Postgres fixture creates and drops its own temporary databases.
"""

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from qdrant_client import QdrantClient
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from integrations.db_client import create_db_engine
from integrations.vector_store import VectorStore

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_DIM = 4


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.fixture(params=["sqlite", "postgres"])
def db_url(request, tmp_path):
    """URL of a fresh, empty database."""
    if request.param == "sqlite":
        yield f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
        return

    admin_url = os.getenv("TEST_DATABASE_URL")
    if not admin_url:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"nutrition_test_{uuid.uuid4().hex[:12]}"
    # psycopg wants a plain libpq URL, not the SQLAlchemy "+driver" form.
    libpq_url = admin_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(libpq_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    finally:
        with psycopg.connect(libpq_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def migrated_url(db_url):
    """A fresh database with all migrations applied (not create_all: tests use the real schema)."""
    command.upgrade(alembic_config(db_url), "head")
    return db_url


@pytest.fixture
def engine(migrated_url):
    engine = create_db_engine(migrated_url)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session


@pytest.fixture(params=["memory", "server"])
def vector_store(request):
    """VectorStore on a uniquely named collection (4-dim, cosine)."""
    if request.param == "memory":
        client = QdrantClient(":memory:")
    else:
        url = os.getenv("TEST_QDRANT_URL")
        if not url:
            pytest.skip("TEST_QDRANT_URL not set")
        client = QdrantClient(url=url, timeout=30)
    store = VectorStore(client, f"test_{uuid.uuid4().hex[:12]}", TEST_DIM)
    store.is_server = request.param == "server"  # indexes are only real on a server
    yield store
    if client.collection_exists(store.collection_name):
        client.delete_collection(store.collection_name)
    client.close()



# ── API tests ────────────────────────────────────────────────────────────────
def _reset_app_caches() -> None:
    """Forget everything the app caches from the environment (settings, engine, limiters)."""
    from config import get_settings
    from integrations import db_client
    from routers import deps

    if db_client.get_engine.cache_info().currsize:
        db_client.get_engine().dispose()
    for cached in (get_settings, db_client.get_engine, db_client._session_factory,
                   deps.get_chat_limiter, deps.get_api_limiter):
        cached.cache_clear()


@pytest.fixture
def api_env(monkeypatch, tmp_path):
    """The app's environment: a fresh, fully migrated SQLite file as DATABASE_URL, rate limits off.

    Returns ``set_env(**values)`` to change settings (e.g. RATE_LIMIT_CHAT_PER_MINUTE=2) before the first request.
    """
    url = f"sqlite:///{(tmp_path / 'api.db').as_posix()}"
    command.upgrade(alembic_config(url), "head")
    monkeypatch.setenv("DATABASE_URL", url)
    for name in ("RATE_LIMIT_CHAT_PER_MINUTE", "RATE_LIMIT_API_PER_MINUTE"):
        monkeypatch.setenv(name, "0")
    _reset_app_caches()

    def set_env(**values):
        for name, value in values.items():
            monkeypatch.setenv(name, str(value))
        _reset_app_caches()

    yield set_env
    _reset_app_caches()


@pytest.fixture
def api(api_env):
    """Factory: ``api(llm=None, **options) -> ApiHarness`` on the real app; closed after the test."""
    from main import app
    from tests.api_support import ApiHarness

    made = []

    def make(llm=None, **options):
        harness = ApiHarness(app, llm, **options)
        made.append(harness)
        return harness

    yield make
    for harness in made:
        harness.close()


@pytest.fixture(autouse=True)
def no_real_health_probes(monkeypatch):
    """/api/health must never reach the real vector store, database or LLM provider from a test (the repo's
    .env may hold real credentials). Tests that care override these."""
    from routers import health

    monkeypatch.setattr(health, "check_vector_store", lambda: health.OK)
    monkeypatch.setattr(health, "check_database", lambda: health.OK)
    monkeypatch.setattr(health, "check_llm", lambda: health.OK)

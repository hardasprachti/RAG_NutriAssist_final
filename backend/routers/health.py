import logging
import threading
import time
from functools import lru_cache

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from config import get_settings
from models.schemas import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])

OK, UNAVAILABLE, NOT_CONFIGURED = "ok", "unavailable", "not_configured"
LLM_PROBE_TTL_S = 60.0  # health is polled; the provider is asked at most once a minute


@lru_cache
def _vector_store():
    from integrations.vector_store import VectorStore

    return VectorStore.from_settings()


def check_vector_store() -> str:
    try:
        return OK if _vector_store().check_connection() else UNAVAILABLE
    except Exception:
        logger.exception("vector store health check failed")
        return UNAVAILABLE


def check_database() -> str:
    try:
        from integrations.db_client import session_scope

        with session_scope() as session:
            session.execute(text("SELECT 1"))
        return OK
    except Exception:
        logger.exception("database health check failed")
        return UNAVAILABLE


_llm_lock = threading.Lock()
_llm_cached: tuple[float, str] = (0.0, "")


def check_llm() -> str:
    """Key present, and the provider answers a model lookup (cached; spends no tokens)."""
    global _llm_cached
    if not get_settings().groq_api_key:
        return NOT_CONFIGURED
    with _llm_lock:
        checked_at, state = _llm_cached
        if state and time.monotonic() - checked_at < LLM_PROBE_TTL_S:
            return state
        from integrations.llm_client import LLMClient

        state = OK if LLMClient.from_settings().check_available() else UNAVAILABLE
        _llm_cached = (time.monotonic(), state)
        return state


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health",
    description=(
        "Reports the vector store, the database and the LLM provider. HTTP 503 when the vector store or the "
        "database is down (nothing can be answered); an LLM problem alone is `200` with status `degraded`, so a "
        "provider blip does not fail a deploy health check."
    ),
    responses={503: {"model": HealthResponse, "description": "Vector store or database unavailable."}},
)
def health() -> JSONResponse:
    parts = {"vector_store": check_vector_store(), "database": check_database(), "llm": check_llm()}
    hard_down = UNAVAILABLE in (parts["vector_store"], parts["database"])
    status = "ok" if all(v == OK for v in parts.values()) else "degraded"
    return JSONResponse(HealthResponse(status=status, **parts).model_dump(), status_code=503 if hard_down else 200)

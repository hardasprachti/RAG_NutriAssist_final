"""FastAPI dependencies: the chat service (built once, on first use) and per-client rate limits."""

import logging
from functools import lru_cache
from uuid import UUID

from fastapi import Depends, Header, Request

from config import get_settings
from core.chat_service import ChatService
from core.rag_pipeline import build_pipeline
from core.rate_limit import RateLimiter
from routers.errors import ApiError

logger = logging.getLogger(__name__)


@lru_cache
def _chat_service() -> ChatService:
    return ChatService(build_pipeline())


def client_id(x_client_id: UUID = Header(description="An anonymous id the browser generates once and keeps.")) -> UUID:
    """Who is asking. Not authentication: the id is an unguessable secret the browser keeps, so one visitor cannot
    list, open, rename, delete or continue another visitor's conversations. Missing or malformed is a 422."""
    return x_client_id


def get_chat_service() -> ChatService:
    """Built lazily so the app boots (and /api/health reports) even when a credential is missing."""
    try:
        return _chat_service()
    except Exception as exc:  # missing GROQ_API_KEY / QDRANT_URL, unreachable store, ...
        logger.exception("could not build the answering pipeline")
        raise ApiError(
            503, "service_unavailable", "The assistant is not available right now. Please try again shortly."
        ) from exc


@lru_cache
def get_chat_limiter() -> RateLimiter:
    return RateLimiter(get_settings().rate_limit_chat_per_minute)


@lru_cache
def get_api_limiter() -> RateLimiter:
    return RateLimiter(get_settings().rate_limit_api_per_minute)


def _client_key(request: Request) -> str:
    # Behind a proxy this is the proxy's address unless uvicorn runs with --proxy-headers and
    # --forwarded-allow-ips set to the proxy (see README, deployment).
    return request.client.host if request.client else "unknown"


def _enforce(limiter: RateLimiter, request: Request) -> None:
    retry_after = limiter.check(_client_key(request))
    if retry_after is not None:
        raise ApiError(
            429, "rate_limited", "Too many requests. Please wait a moment and try again.",
            detail=f"Retry in {retry_after} seconds.", headers={"Retry-After": str(retry_after)},
        )


def limit_chat(request: Request, limiter: RateLimiter = Depends(get_chat_limiter)) -> None:
    _enforce(limiter, request)


def limit_api(request: Request, limiter: RateLimiter = Depends(get_api_limiter)) -> None:
    _enforce(limiter, request)

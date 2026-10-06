"""Application configuration, loaded once from environment variables.

Secrets come only from the environment (or a local, git-ignored .env file).
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_ORIGINS = ("http://localhost:3000",)


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip().rstrip("/") for part in value.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    groq_api_key: str
    groq_model: str
    groq_reasoning_effort: str
    embedding_model: str
    embedding_dim: int
    qdrant_url: str
    qdrant_api_key: str
    qdrant_collection_name: str
    database_url: str
    supabase_url: str
    supabase_service_key: str
    backend_secret_key: str
    allowed_origins: tuple[str, ...]
    log_level: str
    safety_llm_classifier: bool
    preload_models: bool
    retrieval_top_k: int
    retrieval_min_score: float
    history_turns: int
    max_validation_retries: int
    rate_limit_chat_per_minute: int
    rate_limit_api_per_minute: int
    chat_timeout_seconds: float
    chat_max_concurrency: int


_TRUE = {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    # Real environment variables win over .env values.
    load_dotenv(_REPO_ROOT / ".env", override=False)
    load_dotenv(_REPO_ROOT / "backend" / ".env", override=False)

    def env(name: str, default: str = "") -> str:
        # Treat blank values (as in .env.example) the same as unset.
        return os.getenv(name, "").strip() or default

    return Settings(
        groq_api_key=env("GROQ_API_KEY"),
        groq_model=env("GROQ_MODEL", "openai/gpt-oss-120b"),
        groq_reasoning_effort=env("GROQ_REASONING_EFFORT", "low"),
        embedding_model=env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
        embedding_dim=int(env("EMBEDDING_DIM", "384")),
        qdrant_url=env("QDRANT_URL"),
        qdrant_api_key=env("QDRANT_API_KEY"),
        qdrant_collection_name=env("QDRANT_COLLECTION_NAME", "nutrition_chunks"),
        database_url=env("DATABASE_URL"),
        supabase_url=env("SUPABASE_URL"),
        supabase_service_key=env("SUPABASE_SERVICE_KEY"),
        backend_secret_key=env("BACKEND_SECRET_KEY"),
        allowed_origins=_split_csv(env("ALLOWED_ORIGINS")) or _DEFAULT_ORIGINS,
        log_level=env("LOG_LEVEL", "INFO").upper(),
        safety_llm_classifier=env("SAFETY_LLM_CLASSIFIER", "false").lower() in _TRUE,
        preload_models=env("PRELOAD_MODELS", "true").lower() in _TRUE,
        retrieval_top_k=int(env("RETRIEVAL_TOP_K", "5")),
        retrieval_min_score=float(env("RETRIEVAL_MIN_SCORE", "0.58")),
        history_turns=int(env("HISTORY_TURNS", "3")),
        max_validation_retries=int(env("MAX_VALIDATION_RETRIES", "1")),
        rate_limit_chat_per_minute=int(env("RATE_LIMIT_CHAT_PER_MINUTE", "20")),
        rate_limit_api_per_minute=int(env("RATE_LIMIT_API_PER_MINUTE", "120")),
        chat_timeout_seconds=float(env("CHAT_TIMEOUT_SECONDS", "90")),
        chat_max_concurrency=max(int(env("CHAT_MAX_CONCURRENCY", "4")), 1),
    )


@lru_cache
def get_settings() -> Settings:
    return load_settings()

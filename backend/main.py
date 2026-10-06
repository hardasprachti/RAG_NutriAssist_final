import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from logging_config import configure_logging
from routers import chat, conversations, health
from routers.errors import register_body_limit, register_error_handlers

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 16 * 1024  # a 1000-character question is at most ~6 KB even fully JSON-escaped


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Load the embedding model once per process, not per request. A failure is logged loudly
    # and the app still starts: requests that need it will fail visibly rather than the whole
    # service refusing to boot.
    if settings.preload_models:
        from integrations.embedder import get_embedder

        try:
            get_embedder().load()
        except Exception:
            logger.exception("embedding model failed to load at startup")
    yield


app = FastAPI(
    title="Nutrition Assistant API",
    version="1.0.0",
    description=(
        "Grounded, cited answers about nutrition and food safety from six official documents. Every non-2xx "
        "response has the `ErrorEnvelope` shape; refusals and verification failures are 200s with a `status`."
    ),
    lifespan=lifespan,
)

# Middleware added first is innermost; CORS goes last so it wraps everything, error responses included.
register_error_handlers(app)
register_body_limit(app, MAX_BODY_BYTES)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Client-Id"],
    expose_headers=["Retry-After"],
)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(conversations.router)

logger.info("app configured", extra={"allowed_origins": list(settings.allowed_origins)})

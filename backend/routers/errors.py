"""One error shape for every non-2xx response: ``ErrorEnvelope`` (``error`` code, ``message``, ``detail``)."""

import logging
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from models.schemas import ErrorEnvelope

logger = logging.getLogger(__name__)

_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    422: "invalid_request",
    429: "rate_limited",
    503: "service_unavailable",
    504: "timeout",
}
_MAX_DETAIL_ITEMS = 5


class ApiError(Exception):
    """Raise from a route to answer with a specific status, error code and message."""

    def __init__(
        self, status_code: int, error: str, message: str, detail: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message
        self.detail = detail
        self.headers = headers


def envelope(
    status_code: int, error: str, message: str, detail: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
) -> JSONResponse:
    body = ErrorEnvelope(error=error, message=message, detail=detail)
    return JSONResponse(status_code=status_code, content=body.model_dump(), headers=headers)


def _validation_detail(errors: list[dict[str, Any]]) -> str:
    """Field and reason only: never echo the submitted value back."""
    parts = []
    for err in errors[:_MAX_DETAIL_ITEMS]:
        location = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
        parts.append(f"{location}: {err.get('msg', 'invalid')}" if location else str(err.get("msg", "invalid")))
    if len(errors) > _MAX_DETAIL_ITEMS:
        parts.append(f"and {len(errors) - _MAX_DETAIL_ITEMS} more")
    return "; ".join(parts)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return envelope(exc.status_code, exc.error, exc.message, exc.detail, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return envelope(422, "invalid_request", "The request was not valid.", _validation_detail(list(exc.errors())))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _CODES.get(exc.status_code, "http_error")
        return envelope(exc.status_code, code, str(exc.detail), headers=getattr(exc, "headers", None))

    @app.exception_handler(OperationalError)
    async def _database_down(request: Request, exc: OperationalError) -> JSONResponse:
        logger.error("database unavailable", exc_info=exc, extra={"path": request.url.path})
        return envelope(503, "service_unavailable", "The service is temporarily unavailable. Please try again shortly.")

    # Not an exception handler: Starlette runs those outside the CORS middleware, so a 500 would reach the
    # browser without CORS headers and look like a network failure. Registered before CORS, this sits inside it.
    @app.middleware("http")
    async def _unhandled(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            # The client gets nothing internal; the full traceback is in the log.
            logger.error(
                "unhandled exception", exc_info=exc, extra={"path": request.url.path, "method": request.method}
            )
            return envelope(500, "internal_error", "Something went wrong on our side. Please try again.")


def register_body_limit(app: FastAPI, max_bytes: int) -> None:
    """Refuse requests that declare a body over ``max_bytes`` before it is read. Chunked uploads without a
    Content-Length are not caught here; the question length limit still bounds what is processed."""

    @app.middleware("http")
    async def _limit_body(request: Request, call_next):
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > max_bytes:
            return envelope(413, "payload_too_large", "The request body is too large.")
        return await call_next(request)

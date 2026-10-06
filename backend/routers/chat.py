import logging
from functools import partial
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends

from config import get_settings
from core.chat_service import Abandoned, ChatService, ConversationNotFound, ServiceUnavailable
from models.schemas import ChatRequest, ChatResponse, ErrorEnvelope
from routers.deps import client_id, get_chat_service, limit_chat
from routers.errors import ApiError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Ask a nutrition question",
    description=(
        "Runs one turn: safety check, retrieval, grounded generation, validation, persistence. A refusal "
        "(`out_of_scope`, `not_in_corpus`) or a verification failure (`error`) is a 200 with that `status`, "
        "not an HTTP error. Omit `conversation_id` to start a new conversation (titled from the question)."
    ),
    responses={
        404: {"model": ErrorEnvelope, "description": "`conversation_id` does not exist."},
        422: {"model": ErrorEnvelope, "description": "Empty, over-long (>1000 characters) or malformed input."},
        429: {"model": ErrorEnvelope, "description": "Rate limit exceeded; see `Retry-After`."},
        503: {"model": ErrorEnvelope, "description": "A backing service (vector store, database, model) is down."},
        504: {"model": ErrorEnvelope, "description": "The turn took longer than `CHAT_TIMEOUT_SECONDS`."},
    },
    dependencies=[Depends(limit_chat)],
)
async def chat(
    body: ChatRequest, service: ChatService = Depends(get_chat_service), owner: UUID = Depends(client_id)
) -> ChatResponse:
    abandoned = Abandoned()
    timeout = get_settings().chat_timeout_seconds
    try:
        with anyio.fail_after(timeout):
            # abandon_on_cancel: on timeout the request returns at once; the worker thread notices `abandoned`
            # and drops its turn instead of storing a reply nobody is waiting for.
            response = await anyio.to_thread.run_sync(
                partial(service.handle, body, owner, abandoned), abandon_on_cancel=True
            )
    except TimeoutError:
        abandoned.set()
        logger.error("chat request timed out", extra={"timeout_seconds": timeout})
        raise ApiError(
            504, "timeout", "The assistant took too long to answer. Please try again.",
            detail=f"No answer within {timeout:g} seconds.",
        ) from None
    except ConversationNotFound:
        raise ApiError(404, "conversation_not_found", "That conversation does not exist.") from None
    except ServiceUnavailable:
        raise ApiError(
            503, "service_unavailable", "The assistant is temporarily unavailable. Please try again shortly."
        ) from None
    assert response is not None  # only None when abandoned, which is set solely on the timeout path above
    return response

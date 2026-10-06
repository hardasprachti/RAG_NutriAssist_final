from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from core.chat_service import conversation_detail, summary_of
from integrations import db_client
from integrations.db_client import session_scope
from models.db_models import Conversation
from models.schemas import (
    ConversationDetail,
    ConversationSummary,
    CreateConversationRequest,
    ErrorEnvelope,
    UpdateConversationRequest,
)
from routers.deps import client_id, limit_api
from routers.errors import ApiError

router = APIRouter(prefix="/api", tags=["conversations"], dependencies=[Depends(limit_api)])

_COMMON = {
    422: {"model": ErrorEnvelope, "description": "Malformed input."},
    429: {"model": ErrorEnvelope, "description": "Rate limit exceeded; see `Retry-After`."},
    503: {"model": ErrorEnvelope, "description": "The database is unavailable."},
}


@router.post(
    "/conversations",
    response_model=ConversationSummary,
    status_code=201,
    summary="Create an empty conversation",
    description="Optional. `POST /api/chat` without a `conversation_id` creates one itself.",
    responses=_COMMON,
)
def create_conversation(
    body: Optional[CreateConversationRequest] = None, owner: UUID = Depends(client_id)
) -> ConversationSummary:
    title = (body.title or None) if body else None
    with session_scope() as session:
        return summary_of(db_client.create_conversation(session, title, owner))


@router.get(
    "/conversations",
    response_model=list[ConversationSummary],
    summary="List conversations, most recently active first",
    responses=_COMMON,
)
def list_conversations(
    limit: int = Query(50, ge=1, le=100), owner: UUID = Depends(client_id)
) -> list[ConversationSummary]:
    with session_scope() as session:
        return [summary_of(c) for c in db_client.list_conversations(session, limit, owner)]


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
    summary="One conversation with its messages",
    description=(
        "Assistant messages carry their claims and, for answered ones, the retrieved sources with excerpts, "
        "so the sources panel can be restored on reload."
    ),
    responses={404: {"model": ErrorEnvelope, "description": "No such conversation."}, **_COMMON},
)
def get_conversation(conversation_id: UUID, owner: UUID = Depends(client_id)) -> ConversationDetail:
    with session_scope() as session:
        conversation = db_client.get_conversation(session, conversation_id, owner)
        detail = conversation_detail(conversation) if conversation is not None else None
    if detail is None:
        raise ApiError(404, "conversation_not_found", "That conversation does not exist.")
    return detail


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationSummary,
    summary="Rename a conversation",
    description="Renaming is not activity: the conversation keeps its place in the list.",
    responses={404: {"model": ErrorEnvelope, "description": "No such conversation."}, **_COMMON},
)
def rename_conversation(
    conversation_id: UUID, body: UpdateConversationRequest, owner: UUID = Depends(client_id)
) -> ConversationSummary:
    with session_scope() as session:
        renamed = db_client.rename_conversation(session, conversation_id, body.title, owner)
        conversation = session.get(Conversation, conversation_id) if renamed else None
        summary = summary_of(conversation) if conversation is not None else None
    if summary is None:
        raise ApiError(404, "conversation_not_found", "That conversation does not exist.")
    return summary


@router.delete(
    "/conversations/{conversation_id}",
    status_code=204,
    summary="Delete a conversation and its messages",
    description="Permanent. Other conversations are unaffected.",
    responses={404: {"model": ErrorEnvelope, "description": "No such conversation."}, **_COMMON},
)
def delete_conversation(conversation_id: UUID, owner: UUID = Depends(client_id)) -> Response:
    with session_scope() as session:
        deleted = db_client.delete_conversation(session, conversation_id, owner)
    if not deleted:
        raise ApiError(404, "conversation_not_found", "That conversation does not exist.")
    return Response(status_code=204)

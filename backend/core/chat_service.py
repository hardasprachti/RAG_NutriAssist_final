"""One chat turn, end to end (Architecture §5, steps 1-10), around the RAG pipeline.

    load history -> pipeline (safety -> retrieve -> prompt -> generate -> validate -> log) -> persist -> respond

The pipeline never holds a database transaction: history is read first, the (slow) pipeline runs, and then the
user message, the assistant message, its retrieved chunks and its failure-log rows are written in a single
transaction. A request that fails or is abandoned therefore leaves no half conversation behind (no empty
conversation, no question without an answer), while any failure the pipeline recorded is still stored, unlinked.
"""

import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence
from uuid import UUID

from config import Settings, get_settings
from core.failure_logger import DeferredFailures, FailureLogger
from core.rag_pipeline import ChatTurn, PipelineResult, RAGPipeline
from integrations import db_client
from integrations.db_client import session_scope
from models.db_models import Conversation, Message, RetrievedChunk
from models.schemas import (
    ChatRequest,
    ChatResponse,
    Claim,
    ConversationDetail,
    ConversationSummary,
    MessageOut,
    NutritionResponse,
    ResponseStatus,
    RetrievedSource,
)

logger = logging.getLogger(__name__)

TITLE_MAX_CHARS = 60


class ConversationNotFound(LookupError):
    pass


class ServiceUnavailable(RuntimeError):
    """A dependency of the pipeline (embedding model, vector store, database) failed; the client may retry."""


# ── mapping: stored rows and pipeline output -> API models ────────────────────
def _utc(value: datetime) -> datetime:
    # SQLite returns naive datetimes; everything stored is UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def conversation_title(question: str) -> str:
    """First question, whitespace collapsed, cut at a word boundary."""
    text = re.sub(r"\s+", " ", question).strip()
    if len(text) <= TITLE_MAX_CHARS:
        return text
    cut = text[: TITLE_MAX_CHARS - 1]
    if text[len(cut)] != " " and " " in cut:  # mid-word: back up to the last whole word
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.-") + "…"


def summary_of(conversation: Any) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id, title=conversation.title,
        created_at=_utc(conversation.created_at), updated_at=_utc(conversation.updated_at),
    )


def _source(rank: int, chunk_id: str, document_name: str, publisher: str, year: int, section: Optional[str],
            url: str, text: str, score: Optional[float]) -> RetrievedSource:
    return RetrievedSource(
        rank=rank, chunk_id=chunk_id, document_name=document_name, publisher=publisher, year=year,
        section=section or "", url=url, text=text, similarity_score=score,
    )


def sources_from_hits(status: ResponseStatus, hits: Sequence[Any]) -> list[RetrievedSource]:
    """The sources panel content: the chunks the model was shown, for answered responses only (the contract:
    refusals and errors carry none, so there is no evidence panel for an answer that was not given)."""
    if status is not ResponseStatus.answered:
        return []
    return [
        _source(rank, h.chunk.chunk_id, h.chunk.document_name, h.chunk.publisher, h.chunk.year, h.chunk.section,
                h.chunk.source_url, h.chunk.text, h.score)
        for rank, h in enumerate(hits)
    ]


def sources_from_rows(status: Optional[ResponseStatus], rows: Sequence[RetrievedChunk]) -> list[RetrievedSource]:
    if status is not ResponseStatus.answered:
        return []
    return [
        _source(r.rank, r.chunk_id, r.document_name, r.publisher, r.year, r.section, r.source_url, r.chunk_text,
                r.similarity_score)
        for r in sorted(rows, key=lambda r: r.rank)
    ]


def message_to_out(message: Message) -> MessageOut:
    stored = message.structured_response or {}
    status = ResponseStatus(message.status) if message.status else None
    return MessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        status=status,
        claims=[Claim.model_validate(c) for c in stored.get("claims", [])],
        refusal_reason=stored.get("refusal_reason"),
        retrieved_sources=sources_from_rows(status, message.retrieved_chunks),
        created_at=_utc(message.created_at),
    )


def conversation_detail(conversation: Any) -> ConversationDetail:
    return ConversationDetail(
        **summary_of(conversation).model_dump(),
        messages=[message_to_out(m) for m in conversation.messages],
    )


# ── the service ──────────────────────────────────────────────────────────────
class Abandoned(threading.Event):
    """Set by the request when it gives up (timeout). Whoever is working on the turn can ask to be called then."""

    def __init__(self) -> None:
        super().__init__()
        self._callbacks: list[Callable[[], None]] = []
        self._callbacks_lock = threading.Lock()

    def set(self) -> None:
        with self._callbacks_lock:
            super().set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            callback()

    def when_set(self, callback: Callable[[], None]) -> None:
        with self._callbacks_lock:
            if not self.is_set():
                self._callbacks.append(callback)
                return
        callback()


class _Lease:
    """One turn's hold on a concurrency slot. Released once, by whichever comes first: the turn finishing or the
    request being abandoned. Without the second, a provider call that never returns would keep its slot after the
    client already got a 504, and enough of them would block every chat."""

    def __init__(self, slots: threading.BoundedSemaphore) -> None:
        self._slots = slots
        self._lock = threading.Lock()
        self._held = False

    def acquire(self, abandoned: threading.Event) -> bool:
        """Wait for a slot; False if the request is abandoned while waiting."""
        while not self._slots.acquire(timeout=0.25):
            if abandoned.is_set():
                return False
        with self._lock:
            self._held = True
        return True

    def release(self) -> None:
        with self._lock:
            if not self._held:
                return
            self._held = False
        self._slots.release()


class ChatService:
    def __init__(self, pipeline: RAGPipeline, settings: Optional[Settings] = None) -> None:
        settings = settings or get_settings()
        self.pipeline = pipeline
        # At most this many pipelines run at once; the rest wait (inside the request's time limit).
        self._slots = threading.BoundedSemaphore(max(settings.chat_max_concurrency, 1))

    def handle(
        self,
        request: ChatRequest,
        owner_id: Optional[UUID] = None,
        abandoned: Optional[threading.Event] = None,
    ) -> Optional[ChatResponse]:
        """Run one turn for ``owner_id``'s conversation. Blocking: call from a worker thread.

        A conversation that belongs to someone else is reported as not found. ``abandoned`` is set by the caller
        when it has given up (timeout); the turn is then dropped without being stored, its slot is freed, and
        ``None`` is returned. Raises ``ConversationNotFound`` and ``ServiceUnavailable``.
        """
        abandoned = abandoned or Abandoned()
        history = self._load_history(request.conversation_id, owner_id)
        deferred = DeferredFailures()
        lease = _Lease(self._slots)
        try:
            if not lease.acquire(abandoned):
                return None
            if isinstance(abandoned, Abandoned):
                abandoned.when_set(lambda: self._release_abandoned(lease))
            try:
                if abandoned.is_set():
                    return None
                try:
                    result = self.pipeline.answer(request.question, history, failure_logger=FailureLogger(deferred))
                except Exception as exc:
                    logger.exception("pipeline failed")
                    raise ServiceUnavailable("the answering pipeline failed") from exc
            finally:
                lease.release()
            if abandoned.is_set():
                logger.warning("chat turn finished after the client gave up; not stored")
                return None
            return self._persist(request, owner_id, result, deferred)
        finally:
            # Idempotent: anything not handed to the persisting transaction is stored unlinked.
            deferred.flush_unlinked()

    @staticmethod
    def _release_abandoned(lease: _Lease) -> None:
        logger.warning("chat request abandoned while its turn is still running; freeing its concurrency slot")
        lease.release()

    def _load_history(self, conversation_id: Optional[UUID], owner_id: Optional[UUID]) -> list[ChatTurn]:
        if conversation_id is None:
            return []
        turns = max(self.pipeline.config.history_turns, 1)  # the safety check needs the previous user turn
        with session_scope() as session:
            conversation = session.get(Conversation, conversation_id)
            exists = conversation is not None and (owner_id is None or conversation.owner_id == owner_id)
            messages = db_client.get_recent_messages(session, conversation_id, 2 * turns) if exists else []
        if not exists:
            raise ConversationNotFound(str(conversation_id))
        return [ChatTurn(m.role, m.content) for m in messages]

    def _persist(
        self, request: ChatRequest, owner_id: Optional[UUID], result: PipelineResult, deferred: DeferredFailures
    ) -> ChatResponse:
        response: NutritionResponse = result.response
        rows = deferred.drain()
        try:
            with session_scope() as session:
                if request.conversation_id is None:
                    conversation = db_client.create_conversation(
                        session, conversation_title(request.question), owner_id
                    )
                else:
                    conversation = session.get(Conversation, request.conversation_id)
                    if conversation is None or (owner_id is not None and conversation.owner_id != owner_id):
                        # deleted since the history was read
                        raise ConversationNotFound(str(request.conversation_id))
                    if not conversation.title:
                        db_client.set_conversation_title(session, conversation.id, conversation_title(request.question))
                db_client.save_message(session, conversation.id, "user", request.question)
                assistant = db_client.save_message(
                    session, conversation.id, "assistant", response.answer,
                    structured_response=response.model_dump(mode="json"),
                    status=response.status.value,
                    retrieved=result.hits,
                )
                for row in rows:
                    db_client.save_failure_log(session, **{**row, "message_id": assistant.id})
                conversation_id, message_id = conversation.id, assistant.id
        except Exception:
            deferred.flush_unlinked(rows)  # the failures happened even though the turn could not be stored
            raise
        return ChatResponse(
            **response.model_dump(),
            conversation_id=conversation_id,
            message_id=message_id,
            retrieved_sources=sources_from_hits(response.status, result.hits),
        )

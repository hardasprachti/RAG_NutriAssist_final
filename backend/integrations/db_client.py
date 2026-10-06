"""Relational DB access: engine/session management and CRUD helpers.

Helpers take an explicit ``Session`` and ``flush`` but never commit; the caller owns the
transaction (use ``session_scope()`` or the FastAPI dependency ``get_session``).
"""

import logging
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Iterator, Optional, Sequence

from sqlalchemy import Engine, create_engine, event, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, selectinload, sessionmaker

from config import get_settings
from integrations.vector_store import SearchHit
from models.db_models import (
    Conversation,
    DocumentMetadata,
    EvaluationResult,
    FailureLog,
    Message,
    RetrievedChunk,
)

logger = logging.getLogger(__name__)


def normalize_database_url(url: str) -> str:
    """Make a plain Postgres URL (as Supabase hands out) use the psycopg 3 driver."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    parsed = make_url(url)
    if parsed.drivername == "postgresql":
        return parsed.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    return url


def create_db_engine(url: str) -> Engine:
    url = normalize_database_url(url)
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("postgresql"):
        # Supabase's pooler runs pgbouncer in transaction mode, which breaks
        # server-side prepared statements.
        kwargs["connect_args"] = {"prepare_threshold": None}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        # SQLite ignores foreign keys (and ON DELETE rules) unless asked; match Postgres.
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    return create_db_engine(url)


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back and re-raise on error."""
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("database transaction rolled back")
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a transactional session."""
    with session_scope() as session:
        yield session


# ── conversations & messages ─────────────────────────────────────────────────
def create_conversation(
    session: Session, title: Optional[str] = None, owner_id: Optional[uuid.UUID] = None
) -> Conversation:
    conversation = Conversation(title=title, owner_id=owner_id)
    session.add(conversation)
    session.flush()
    return conversation


def list_conversations(
    session: Session, limit: int = 100, owner_id: Optional[uuid.UUID] = None
) -> list[Conversation]:
    """Most recently active first. With ``owner_id``, only that owner's; without it, everyone's (for internal
    tools only: every API path passes the caller's id)."""
    stmt = select(Conversation).order_by(Conversation.updated_at.desc()).limit(limit)
    if owner_id is not None:
        stmt = stmt.where(Conversation.owner_id == owner_id)
    return list(session.scalars(stmt))


def get_conversation(
    session: Session, conversation_id: uuid.UUID, owner_id: Optional[uuid.UUID] = None
) -> Optional[Conversation]:
    """Conversation with its messages and each message's retrieved chunks loaded. With ``owner_id``, None when
    it belongs to someone else (indistinguishable from not existing)."""
    stmt = (
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.messages).selectinload(Message.retrieved_chunks))
    )
    if owner_id is not None:
        stmt = stmt.where(Conversation.owner_id == owner_id)
    return session.scalars(stmt).one_or_none()


def get_recent_messages(session: Session, conversation_id: uuid.UUID, limit: int) -> list[Message]:
    """The last ``limit`` messages of a conversation, oldest first, without their retrieved chunks."""
    if limit <= 0:
        return []
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return list(reversed(list(session.scalars(stmt))))


def _as_utc(value: datetime) -> datetime:
    # SQLite hands back naive datetimes; everything we store is UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def save_message(
    session: Session,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    structured_response: Optional[dict[str, Any]] = None,
    status: Optional[str] = None,
    retrieved: Sequence[SearchHit] = (),
) -> Message:
    """Persist a message and the chunks that backed it, and touch the conversation."""
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise LookupError(f"conversation {conversation_id} does not exist")

    # History order is by created_at; guarantee strictly increasing timestamps within a
    # conversation so a coarse clock can never swap a question and its answer.
    created_at = datetime.now(timezone.utc)
    latest = session.scalar(
        select(Message.created_at)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    if latest is not None and created_at <= _as_utc(latest):
        created_at = _as_utc(latest) + timedelta(microseconds=1)

    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        structured_response=structured_response,
        status=status,
        created_at=created_at,
        retrieved_chunks=[
            RetrievedChunk(
                rank=rank,
                chunk_id=hit.chunk.chunk_id,
                document_name=hit.chunk.document_name,
                publisher=hit.chunk.publisher,
                year=hit.chunk.year,
                source_url=hit.chunk.source_url,
                section=hit.chunk.section,
                chunk_text=hit.chunk.text,
                similarity_score=hit.score,
            )
            for rank, hit in enumerate(retrieved)
        ],
    )
    session.add(message)
    conversation.updated_at = created_at
    session.flush()
    return message


def set_conversation_title(session: Session, conversation_id: uuid.UUID, title: str) -> None:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise LookupError(f"conversation {conversation_id} does not exist")
    conversation.title = title[:255]
    session.flush()


def rename_conversation(
    session: Session, conversation_id: uuid.UUID, title: str, owner_id: Optional[uuid.UUID] = None
) -> bool:
    """Set a conversation's title without counting as activity (``updated_at`` keeps its place in the list).
    False when it does not exist (or, with ``owner_id``, is not that owner's)."""
    stmt = update(Conversation).where(Conversation.id == conversation_id)
    if owner_id is not None:
        stmt = stmt.where(Conversation.owner_id == owner_id)
    result = session.execute(stmt.values(title=title[:255], updated_at=Conversation.updated_at))
    return result.rowcount > 0


def delete_conversation(
    session: Session, conversation_id: uuid.UUID, owner_id: Optional[uuid.UUID] = None
) -> bool:
    """Delete a conversation with its messages and their retrieved chunks. Failure logs about its messages are
    kept, unlinked (``failure_logs.message_id`` is SET NULL). False when it does not exist (or, with
    ``owner_id``, is not that owner's)."""
    conversation = session.get(Conversation, conversation_id)
    if conversation is None or (owner_id is not None and conversation.owner_id != owner_id):
        return False
    session.delete(conversation)
    session.flush()
    return True


# ── logs & evaluation ────────────────────────────────────────────────────────
def save_failure_log(
    session: Session,
    failure_category: str,
    user_question: str,
    model_response: Optional[str] = None,
    retrieved_chunks: Optional[list[Any]] = None,
    error_description: Optional[str] = None,
    model_info: Optional[str] = None,
    message_id: Optional[uuid.UUID] = None,
) -> FailureLog:
    log = FailureLog(
        message_id=message_id,
        failure_category=failure_category,
        user_question=user_question,
        model_response=model_response,
        retrieved_chunks=retrieved_chunks,
        error_description=error_description,
        model_info=model_info,
    )
    session.add(log)
    session.flush()
    return log


def save_evaluation_result(
    session: Session,
    question: str,
    expected_document: Optional[str] = None,
    expected_section: Optional[str] = None,
    hit_at_k: Optional[bool] = None,
    k_value: Optional[int] = None,
    retrieval_score: Optional[float] = None,
    citation_valid: Optional[bool] = None,
) -> EvaluationResult:
    result = EvaluationResult(
        question=question,
        expected_document=expected_document,
        expected_section=expected_section,
        hit_at_k=hit_at_k,
        k_value=k_value,
        retrieval_score=retrieval_score,
        citation_valid=citation_valid,
    )
    session.add(result)
    session.flush()
    return result


# ── document registry (written by ingestion, Phase 2) ────────────────────────
def upsert_document_metadata(
    session: Session,
    document_name: str,
    publisher: str,
    year: int,
    source_url: str,
    retrieval_date: Optional[date],
    total_chunks: int,
    embedding_model: Optional[str],
) -> DocumentMetadata:
    """Insert or update by ``source_url`` so re-running ingestion never duplicates rows."""
    doc = session.scalars(
        select(DocumentMetadata).where(DocumentMetadata.source_url == source_url)
    ).one_or_none()
    if doc is None:
        doc = DocumentMetadata(source_url=source_url)
        session.add(doc)
    doc.document_name = document_name
    doc.publisher = publisher
    doc.year = year
    doc.retrieval_date = retrieval_date
    doc.total_chunks = total_chunks
    doc.embedding_model = embedding_model
    session.flush()
    return doc

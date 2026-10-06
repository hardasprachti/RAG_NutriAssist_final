"""SQLAlchemy ORM models for the relational store (Architecture §9).

Portable across PostgreSQL (Supabase, production) and SQLite (fast unit tests):
JSON columns use JSONB on PostgreSQL, and UUIDs use SQLAlchemy's generic ``Uuid``.
"""

import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JsonType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_owner_updated", "owner_id", "updated_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[Optional[str]] = mapped_column(String(255))
    # An anonymous per-browser id (the X-Client-Id header). NULL for conversations from before owners existed:
    # they belong to nobody, so no client can list or open them.
    owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    structured_response: Mapped[Optional[dict[str, Any]]] = mapped_column(JsonType)
    status: Mapped[Optional[str]] = mapped_column(String(32))  # ResponseStatus value
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    retrieved_chunks: Mapped[list["RetrievedChunk"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="RetrievedChunk.rank",
    )


class RetrievedChunk(Base):
    __tablename__ = "retrieved_chunks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    # Position in the retrieved set. Not in Architecture §9: added so the sources panel
    # can be restored in order even when cross-document merging reorders by score.
    rank: Mapped[int] = mapped_column(Integer, default=0)
    chunk_id: Mapped[str] = mapped_column(String(255))
    document_name: Mapped[str] = mapped_column(String(255))
    publisher: Mapped[str] = mapped_column(String(255))
    year: Mapped[int] = mapped_column(Integer)
    source_url: Mapped[str] = mapped_column(Text)
    section: Mapped[Optional[str]] = mapped_column(String(255))
    chunk_text: Mapped[str] = mapped_column(Text)
    similarity_score: Mapped[Optional[float]] = mapped_column(Float)

    message: Mapped[Message] = relationship(back_populates="retrieved_chunks")


class DocumentMetadata(Base):
    __tablename__ = "document_metadata"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_name: Mapped[str] = mapped_column(String(255))
    publisher: Mapped[str] = mapped_column(String(255))
    year: Mapped[int] = mapped_column(Integer)
    # Unique so ingestion can upsert idempotently.
    source_url: Mapped[str] = mapped_column(Text, unique=True)
    retrieval_date: Mapped[Optional[date]] = mapped_column(Date)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[Optional[str]] = mapped_column(String(255))


class FailureLog(Base):
    __tablename__ = "failure_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Nullable: failures from evaluation runs or from responses that were never persisted
    # have no message row; the log must still be written.
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), index=True
    )
    failure_category: Mapped[str] = mapped_column(String(64), index=True)
    user_question: Mapped[str] = mapped_column(Text)
    model_response: Mapped[Optional[str]] = mapped_column(Text)
    retrieved_chunks: Mapped[Optional[list[Any]]] = mapped_column(JsonType)
    error_description: Mapped[Optional[str]] = mapped_column(Text)
    model_info: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    question: Mapped[str] = mapped_column(Text)
    expected_document: Mapped[Optional[str]] = mapped_column(String(255))
    expected_section: Mapped[Optional[str]] = mapped_column(String(255))
    hit_at_k: Mapped[Optional[bool]] = mapped_column(Boolean)
    k_value: Mapped[Optional[int]] = mapped_column(Integer)
    retrieval_score: Mapped[Optional[float]] = mapped_column(Float)
    citation_valid: Mapped[Optional[bool]] = mapped_column(Boolean)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

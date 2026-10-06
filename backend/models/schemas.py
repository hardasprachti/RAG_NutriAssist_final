"""Structured response schema and API contract (Architecture §10-§11).

This module is the single source of truth for the wire format. ``frontend/src/types/api.ts``
mirrors it by hand; ``tests/test_schemas.py`` fails if the two drift apart.

Changes from the Milestone 1 schema are listed in ``docs/schema_changes.md``.
"""

from datetime import datetime
from enum import Enum
from typing import Annotated, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

# A non-empty string once surrounding whitespace is removed.
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

MAX_QUESTION_LENGTH = 1000
MAX_TITLE_LENGTH = 255  # the conversations.title column


class ResponseStatus(str, Enum):
    answered = "answered"
    not_in_corpus = "not_in_corpus"
    out_of_scope = "out_of_scope"
    error = "error"


class SourceReference(BaseModel):
    document_name: NonBlank
    publisher: NonBlank
    year: int
    section: str  # may be empty when a document has no detectable heading
    url: NonBlank
    chunk_id: NonBlank


class Claim(BaseModel):
    claim_text: NonBlank
    source: SourceReference


class NutritionResponse(BaseModel):
    """What the LLM must produce and what the validator checks.

    Invariants (enforced here, independent of any prompt):
    * ``answered``  -> at least one claim, no refusal_reason
    * anything else -> no claims, a refusal_reason
    """

    answer: NonBlank
    claims: list[Claim]
    status: ResponseStatus
    refusal_reason: Optional[str] = None

    @model_validator(mode="after")
    def _check_status_invariants(self) -> "NutritionResponse":
        if self.status is ResponseStatus.answered:
            if not self.claims:
                raise ValueError("status 'answered' requires at least one claim")
            if self.refusal_reason is not None:
                raise ValueError("refusal_reason must be null when status is 'answered'")
        else:
            if self.claims:
                raise ValueError(f"claims must be empty when status is '{self.status.value}'")
            if not (self.refusal_reason and self.refusal_reason.strip()):
                raise ValueError(
                    f"refusal_reason is required when status is '{self.status.value}'"
                )
        return self


# ── API models ───────────────────────────────────────────────────────────────


class RetrievedSource(BaseModel):
    """A chunk retrieved for a message, shown in the sources panel (with its excerpt)."""

    rank: int
    chunk_id: str
    document_name: str
    publisher: str
    year: int
    section: str
    url: str
    text: str
    similarity_score: Optional[float] = None


class ChatRequest(BaseModel):
    conversation_id: Optional[UUID] = None
    question: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_LENGTH),
    ]

    @field_validator("question")
    @classmethod
    def _no_nul(cls, value: str) -> str:
        # PostgreSQL text cannot hold NUL; reject it here rather than fail the insert later.
        if "\x00" in value:
            raise ValueError("question must not contain NUL characters")
        return value


class ChatResponse(NutritionResponse):
    """POST /api/chat result. Refusals and errors carry an empty ``retrieved_sources``."""

    conversation_id: UUID
    message_id: UUID
    retrieved_sources: list[RetrievedSource] = Field(default_factory=list)


class CreateConversationRequest(BaseModel):
    title: Optional[Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_TITLE_LENGTH)]] = None

    @field_validator("title")
    @classmethod
    def _no_nul(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and "\x00" in value:
            raise ValueError("title must not contain NUL characters")
        return value


class UpdateConversationRequest(BaseModel):
    """PATCH /api/conversations/{id}: rename. Unlike creation, a blank title is rejected."""

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TITLE_LENGTH)]

    @field_validator("title")
    @classmethod
    def _no_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("title must not contain NUL characters")
        return value


class ConversationSummary(BaseModel):
    id: UUID
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: UUID
    role: str  # "user" | "assistant"
    content: str
    status: Optional[ResponseStatus] = None
    claims: list[Claim] = Field(default_factory=list)
    refusal_reason: Optional[str] = None
    retrieved_sources: list[RetrievedSource] = Field(default_factory=list)
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageOut] = Field(default_factory=list)


class HealthResponse(BaseModel):
    """``status`` is "ok" or "degraded". Each dependency reports "ok", "unavailable" or (llm only) "not_configured"."""

    status: str
    vector_store: Optional[str] = None
    database: Optional[str] = None
    llm: Optional[str] = None


class ErrorEnvelope(BaseModel):
    """Consistent body for every non-2xx response."""

    model_config = ConfigDict(extra="forbid")

    error: str  # machine-readable code, e.g. "invalid_request"
    message: str
    detail: Optional[str] = None

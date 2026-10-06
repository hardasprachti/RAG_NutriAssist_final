"""Failure logging (Problem Statement §8, Architecture §13).

Every response the pipeline rejects, repairs or flags is recorded in ``failure_logs`` with the question, the
model's raw output, the retrieved chunks and a plain description. Logging must never take a request down, but
it must never fail *silently* either: if the database write fails, the whole record goes to the application
log at ERROR so it can be recovered.
"""

import json
import logging
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional, Sequence
from uuid import UUID

logger = logging.getLogger(__name__)

MAX_RESPONSE_CHARS = 20_000  # a runaway completion must not bloat the table


class FailureCategory(str, Enum):
    """``failure_logs.failure_category`` values.

    The first block is Architecture §13; the second adds the Problem Statement §8 categories §13 omits, so
    evaluation findings map one-to-one onto the required categories.
    """

    # Architecture §13
    schema_validation_failure = "schema_validation_failure"
    invalid_citation = "invalid_citation"
    fabricated_source = "fabricated_source"
    missing_refusal = "missing_refusal"
    not_in_corpus_answered = "not_in_corpus_answered"
    inconsistent_numerical_claim = "inconsistent_numerical_claim"  # written by the consistency tests
    empty_claims = "empty_claims"
    vague_response = "vague_response"
    # Problem Statement §8
    unsupported_claim = "unsupported_claim"  # a claim (or number in it) the cited chunk does not support
    missing_citation = "missing_citation"  # a factual statement with no claim/source behind it
    incorrect_retrieval = "incorrect_retrieval"  # wrong chunks for the question (found by evaluation)
    conflicting_guidance_error = "conflicting_guidance_error"  # sources merged into one statement
    # Operational: the provider failed, not the model's answer
    llm_error = "llm_error"


@dataclass(frozen=True)
class Violation:
    """One problem found in a model response.

    ``blocking`` violations make the response unusable (it is retried, then replaced by an error); advisory
    ones are recorded for analysis and the response is still returned.
    """

    category: FailureCategory
    description: str
    claim_index: Optional[int] = None
    blocking: bool = True

    def __str__(self) -> str:
        where = f" (claim {self.claim_index + 1})" if self.claim_index is not None else ""
        return f"{self.category.value}{where}: {self.description}"


def snapshot_chunks(hits: Sequence[Any]) -> list[dict[str, Any]]:
    """JSON-safe copy of retrieved ``SearchHit``s for the log (what the model was shown)."""
    return [
        {
            "chunk_id": h.chunk.chunk_id,
            "document_name": h.chunk.document_name,
            "section": h.chunk.section,
            "text": h.chunk.text,
            "similarity_score": round(float(h.score), 4),
        }
        for h in hits
    ]


def _default_save(**fields: Any) -> None:
    from integrations.db_client import save_failure_log, session_scope

    with session_scope() as session:
        save_failure_log(session, **fields)


def store_record(save: Callable[..., None], fields: dict[str, Any]) -> bool:
    """Write one failure record; on failure log the whole record at ERROR instead of raising."""
    try:
        save(**fields)
    except Exception:
        # Never swallow: the record itself goes to the log so the failure can be recovered.
        message_id = fields.get("message_id")
        logger.exception(
            "could not store failure log",
            extra={"failure_record": json.dumps({**fields, "message_id": str(message_id)}, default=str)},
        )
        return False
    return True


class DeferredFailures:
    """A ``FailureLogger`` save function that holds records until the message they belong to exists.

    ``failure_logs.message_id`` is a foreign key, but the assistant message is only created once the
    pipeline has finished. The request collects records here, then ``drain()`` hands them to the
    transaction that stores the message; ``flush_unlinked()`` stores them without a message id when no
    message was produced (a failed or abandoned request), so a recorded failure is never lost.
    """

    def __init__(self, save: Optional[Callable[..., None]] = None) -> None:
        self._save = save or _default_save
        self._lock = threading.Lock()
        self._rows: list[dict[str, Any]] = []

    def __call__(self, **fields: Any) -> None:
        with self._lock:
            self._rows.append(fields)

    def drain(self) -> list[dict[str, Any]]:
        with self._lock:
            rows, self._rows = self._rows, []
        return rows

    def flush_unlinked(self, rows: Optional[list[dict[str, Any]]] = None) -> int:
        """Store ``rows`` (default: everything held) without a message id. Returns how many were stored."""
        return sum(store_record(self._save, row) for row in (self.drain() if rows is None else rows))


class FailureLogger:
    def __init__(self, save: Optional[Callable[..., None]] = None) -> None:
        self._save = save or _default_save

    def log(
        self,
        category: FailureCategory,
        question: str,
        description: str,
        *,
        model_response: Optional[str] = None,
        retrieved: Sequence[Any] = (),
        model_info: Optional[str] = None,
        message_id: Optional[UUID] = None,
    ) -> bool:
        """Record one failure. Returns False (after logging at ERROR) if it could not be stored."""
        if model_response is not None and len(model_response) > MAX_RESPONSE_CHARS:
            model_response = model_response[:MAX_RESPONSE_CHARS] + "…[truncated]"
        fields = dict(
            failure_category=category.value,
            user_question=question,
            model_response=model_response,
            retrieved_chunks=snapshot_chunks(retrieved),
            error_description=description,
            model_info=model_info,
            message_id=message_id,
        )
        if not store_record(self._save, fields):
            return False
        logger.warning(
            "failure recorded",
            extra={"failure_category": category.value, "model_info": model_info, "description": description[:300]},
        )
        return True

    def log_violations(
        self,
        violations: Sequence[Violation],
        question: str,
        *,
        model_response: Optional[str],
        retrieved: Sequence[Any],
        model_info: Optional[str],
        message_id: Optional[UUID] = None,
    ) -> int:
        """Record each violation; returns how many were stored."""
        return sum(
            self.log(
                v.category, question, str(v) if v.claim_index is not None else v.description,
                model_response=model_response, retrieved=retrieved, model_info=model_info, message_id=message_id,
            )
            for v in violations
        )

import contextlib
import logging

import pytest
from sqlalchemy import select

import integrations.db_client as db_client
from core.failure_logger import MAX_RESPONSE_CHARS, FailureCategory, FailureLogger, Violation
from models.db_models import FailureLog
from tests.support import CHICKEN, Recorder, hit


def test_every_required_category_exists():
    required = {
        # Architecture §13
        "schema_validation_failure", "invalid_citation", "fabricated_source", "empty_claims", "missing_refusal",
        "not_in_corpus_answered", "inconsistent_numerical_claim", "vague_response",
        # Problem Statement §8 categories §13 omits
        "unsupported_claim", "missing_citation", "incorrect_retrieval", "conflicting_guidance_error",
    }
    assert required <= {c.value for c in FailureCategory}


def test_a_record_carries_every_field_the_problem_statement_requires():
    rec = Recorder()
    ok = FailureLogger(rec).log(
        FailureCategory.invalid_citation, "How much vitamin C?", "chunk 999 not retrieved",
        model_response='{"answer": "..."}', retrieved=[hit(CHICKEN, 0.81234)], model_info="groq/openai/gpt-oss-120b",
    )
    assert ok
    (row,) = rec.rows
    assert row["failure_category"] == "invalid_citation"
    assert row["user_question"] == "How much vitamin C?"
    assert row["model_response"] == '{"answer": "..."}'
    assert row["error_description"] == "chunk 999 not retrieved"
    assert row["model_info"] == "groq/openai/gpt-oss-120b"
    chunk = row["retrieved_chunks"][0]
    assert chunk["chunk_id"] == CHICKEN.chunk_id and chunk["text"] == CHICKEN.text
    assert chunk["similarity_score"] == 0.8123 and chunk["section"] == CHICKEN.section


def test_a_huge_model_response_is_truncated():
    rec = Recorder()
    FailureLogger(rec).log(FailureCategory.vague_response, "q", "d", model_response="x" * (MAX_RESPONSE_CHARS + 500))
    assert len(rec.rows[0]["model_response"]) < MAX_RESPONSE_CHARS + 50


def test_a_failing_store_never_raises_and_is_never_silent(caplog):
    def broken(**_):
        raise RuntimeError("database is down")

    with caplog.at_level(logging.ERROR):
        ok = FailureLogger(broken).log(FailureCategory.empty_claims, "q?", "no claims", model_response="{}")
    assert ok is False
    record = next(r for r in caplog.records if r.getMessage() == "could not store failure log")
    assert "empty_claims" in record.failure_record and "q?" in record.failure_record  # recoverable from the log


def test_log_violations_stores_each_one():
    rec = Recorder()
    stored = FailureLogger(rec).log_violations(
        [Violation(FailureCategory.invalid_citation, "a"), Violation(FailureCategory.unsupported_claim, "b", claim_index=1)],
        "q", model_response="raw", retrieved=[], model_info="m",
    )
    assert stored == 2 and rec.categories == ["invalid_citation", "unsupported_claim"]
    assert "claim 2" in rec.rows[1]["error_description"]


def test_the_default_store_writes_to_the_failure_logs_table(session, monkeypatch):
    @contextlib.contextmanager
    def scope():
        yield session
        session.commit()

    monkeypatch.setattr(db_client, "session_scope", scope)
    assert FailureLogger().log(
        FailureCategory.fabricated_source, "Who invented this?", "url not in corpus",
        model_response="resp", retrieved=[hit(CHICKEN)], model_info="groq/x",
    )
    (row,) = session.scalars(select(FailureLog)).all()
    assert row.failure_category == "fabricated_source" and row.user_question == "Who invented this?"
    assert row.retrieved_chunks[0]["chunk_id"] == CHICKEN.chunk_id
    assert row.model_info == "groq/x" and row.created_at is not None

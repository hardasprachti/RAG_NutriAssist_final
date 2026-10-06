import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from integrations import db_client
from integrations.db_client import normalize_database_url
from integrations.vector_store import ChunkRecord, SearchHit
from models.db_models import Conversation, DocumentMetadata, FailureLog, Message


def make_hit(index: int, score: float = 0.9) -> SearchHit:
    return SearchHit(
        chunk=ChunkRecord(
            chunk_id=f"who_healthy_diet_chunk_{index:03d}",
            document_name="Healthy Diet Fact Sheet",
            publisher="World Health Organization (WHO)",
            year=2020,
            source_url="https://www.who.int/news-room/fact-sheets/detail/healthy-diet",
            retrieval_date="2026-10-05",
            section="Fats",
            chunk_index=index,
            total_chunks=42,
            text=f"chunk text {index}",
        ),
        score=score,
    )


# ── URL handling ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "given, expected",
    [
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("sqlite:///x.db", "sqlite:///x.db"),
    ],
)
def test_normalize_database_url(given, expected):
    assert normalize_database_url(given) == expected


def test_normalize_keeps_special_characters_in_password():
    url = normalize_database_url("postgresql://u:p%40ss%2Fw@h/db")
    assert url == "postgresql+psycopg://u:p%40ss%2Fw@h/db"


# ── conversations & messages ─────────────────────────────────────────────────
def test_conversation_round_trip(session):
    created = db_client.create_conversation(session, title="Saturated fat")
    session.commit()

    fetched = db_client.get_conversation(session, created.id)
    assert fetched is not None
    assert fetched.title == "Saturated fat"
    assert fetched.created_at is not None and fetched.updated_at is not None
    assert fetched.messages == []


def test_get_unknown_conversation_returns_none(session):
    assert db_client.get_conversation(session, uuid.uuid4()) is None


def test_list_conversations_most_recently_active_first(session):
    first = db_client.create_conversation(session, "first")
    second = db_client.create_conversation(session, "second")
    session.commit()
    # Activity in the older conversation moves it to the top.
    db_client.save_message(session, first.id, "user", "hello")
    session.commit()

    assert [c.title for c in db_client.list_conversations(session)] == ["first", "second"]
    assert [c.id for c in db_client.list_conversations(session, limit=1)] == [first.id]
    assert second.id != first.id


def test_save_message_with_retrieved_chunks_round_trip(session):
    conversation = db_client.create_conversation(session)
    structured = {"answer": "Less than 10%.", "claims": [], "status": "answered"}
    hits = [make_hit(12, 0.91), make_hit(3, 0.80)]
    db_client.save_message(
        session,
        conversation.id,
        "assistant",
        "Less than 10%.",
        structured_response=structured,
        status="answered",
        retrieved=hits,
    )
    session.commit()
    session.expire_all()

    loaded = db_client.get_conversation(session, conversation.id)
    (message,) = loaded.messages
    assert message.role == "assistant"
    assert message.status == "answered"
    assert message.structured_response == structured
    # Order of retrieval is preserved, not re-sorted by id or score.
    assert [c.chunk_id for c in message.retrieved_chunks] == [
        "who_healthy_diet_chunk_012",
        "who_healthy_diet_chunk_003",
    ]
    first = message.retrieved_chunks[0]
    assert first.document_name == "Healthy Diet Fact Sheet"
    assert first.publisher == "World Health Organization (WHO)"
    assert first.year == 2020
    assert first.section == "Fats"
    assert first.chunk_text == "chunk text 12"
    assert first.similarity_score == pytest.approx(0.91)


def test_messages_keep_insertion_order_when_saved_rapidly(session):
    conversation = db_client.create_conversation(session)
    for i in range(25):
        db_client.save_message(session, conversation.id, "user" if i % 2 == 0 else "assistant", f"m{i}")
    session.commit()
    session.expire_all()

    loaded = db_client.get_conversation(session, conversation.id)
    assert [m.content for m in loaded.messages] == [f"m{i}" for i in range(25)]


def test_save_message_touches_conversation_updated_at(session):
    conversation = db_client.create_conversation(session)
    session.commit()
    session.refresh(conversation)
    # SQLite returns naive datetimes; everything stored is UTC, so compare naive.
    before = conversation.updated_at.replace(tzinfo=None)
    message = db_client.save_message(session, conversation.id, "user", "hi")
    session.commit()
    session.refresh(conversation)
    after = conversation.updated_at.replace(tzinfo=None)
    assert after >= before
    assert after == message.created_at.replace(tzinfo=None)


def test_save_message_unknown_conversation_raises(session):
    with pytest.raises(LookupError):
        db_client.save_message(session, uuid.uuid4(), "user", "hi")


def test_set_conversation_title_truncates(session):
    conversation = db_client.create_conversation(session)
    db_client.set_conversation_title(session, conversation.id, "x" * 400)
    session.commit()
    assert len(session.get(Conversation, conversation.id).title) == 255


def test_deleting_conversation_cascades_to_messages_and_chunks(session):
    conversation = db_client.create_conversation(session)
    db_client.save_message(session, conversation.id, "assistant", "a", retrieved=[make_hit(1)])
    session.commit()

    session.delete(session.get(Conversation, conversation.id))
    session.commit()

    assert session.scalars(select(Message)).all() == []
    assert db_client.get_conversation(session, conversation.id) is None


# ── failure logs ─────────────────────────────────────────────────────────────
def test_failure_log_round_trip_with_message(session):
    conversation = db_client.create_conversation(session)
    message = db_client.save_message(session, conversation.id, "assistant", "bad")
    chunks = [{"chunk_id": "who_chunk_003", "text": "...", "section": "Micronutrients"}]
    log = db_client.save_failure_log(
        session,
        failure_category="invalid_citation",
        user_question="How much vitamin C does WHO recommend?",
        model_response="WHO recommends 90mg of vitamin C daily.",
        retrieved_chunks=chunks,
        error_description="chunk_id 'who_chunk_999' not in retrieved set",
        model_info="groq/openai/gpt-oss-120b",
        message_id=message.id,
    )
    session.commit()
    session.expire_all()

    loaded = session.get(FailureLog, log.id)
    assert loaded.failure_category == "invalid_citation"
    assert loaded.retrieved_chunks == chunks
    assert loaded.model_info == "groq/openai/gpt-oss-120b"
    assert loaded.message_id == message.id
    assert loaded.created_at is not None


def test_failure_log_without_message_and_survives_message_deletion(session):
    standalone = db_client.save_failure_log(session, "vague_response", "q")
    conversation = db_client.create_conversation(session)
    message = db_client.save_message(session, conversation.id, "assistant", "x")
    linked = db_client.save_failure_log(session, "empty_claims", "q2", message_id=message.id)
    session.commit()
    assert standalone.message_id is None

    session.delete(session.get(Conversation, conversation.id))
    session.commit()
    session.expire_all()

    # The failure record outlives the conversation it came from.
    survivor = session.get(FailureLog, linked.id)
    assert survivor is not None and survivor.message_id is None


# ── evaluation results ───────────────────────────────────────────────────────
def test_evaluation_result_round_trip(session):
    result = db_client.save_evaluation_result(
        session,
        question="How long can raw chicken stay in the fridge?",
        expected_document="Refrigerator & Freezer Storage Chart",
        expected_section="Poultry",
        hit_at_k=True,
        k_value=5,
        retrieval_score=0.83,
        citation_valid=None,
    )
    session.commit()
    session.expire_all()

    from models.db_models import EvaluationResult

    loaded = session.get(EvaluationResult, result.id)
    assert loaded.hit_at_k is True
    assert loaded.k_value == 5
    assert loaded.retrieval_score == pytest.approx(0.83)
    assert loaded.citation_valid is None
    assert loaded.run_at is not None


# ── document metadata ────────────────────────────────────────────────────────
def test_document_metadata_upsert_is_idempotent(session):
    kwargs = dict(
        document_name="Healthy Diet Fact Sheet",
        publisher="World Health Organization (WHO)",
        year=2020,
        source_url="https://www.who.int/news-room/fact-sheets/detail/healthy-diet",
        retrieval_date=date(2026, 10, 5),
        embedding_model="BAAI/bge-small-en-v1.5",
    )
    db_client.upsert_document_metadata(session, total_chunks=40, **kwargs)
    session.commit()
    db_client.upsert_document_metadata(session, total_chunks=42, **kwargs)
    session.commit()

    rows = session.scalars(select(DocumentMetadata)).all()
    assert len(rows) == 1
    assert rows[0].total_chunks == 42
    assert rows[0].retrieval_date == date(2026, 10, 5)


# ── session_scope ────────────────────────────────────────────────────────────
@pytest.fixture
def scoped(engine, monkeypatch):
    monkeypatch.setattr(
        db_client, "_session_factory", lambda: sessionmaker(bind=engine, expire_on_commit=False)
    )


def test_session_scope_commits_on_success(scoped, session):
    with db_client.session_scope() as s:
        conversation_id = db_client.create_conversation(s, "kept").id
    assert session.get(Conversation, conversation_id) is not None


def test_session_scope_rolls_back_on_error(scoped, session):
    with pytest.raises(RuntimeError):
        with db_client.session_scope() as s:
            conversation_id = db_client.create_conversation(s, "discarded").id
            raise RuntimeError("boom")
    assert session.get(Conversation, conversation_id) is None

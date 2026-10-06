import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from models.schemas import (
    ChatRequest,
    ChatResponse,
    Claim,
    ConversationDetail,
    CreateConversationRequest,
    ErrorEnvelope,
    HealthResponse,
    MessageOut,
    NutritionResponse,
    ResponseStatus,
    SourceReference,
    UpdateConversationRequest,
)

SOURCE = {
    "document_name": "Healthy Diet Fact Sheet",
    "publisher": "World Health Organization (WHO)",
    "year": 2020,
    "section": "Fats",
    "url": "https://www.who.int/news-room/fact-sheets/detail/healthy-diet",
    "chunk_id": "who_healthy_diet_chunk_012",
}
CLAIM = {"claim_text": "Saturated fat should be less than 10% of total energy intake.", "source": SOURCE}


def answered(**overrides):
    return {"answer": "WHO limits saturated fat.", "claims": [CLAIM], "status": "answered", **overrides}


def refusal(status, **overrides):
    return {
        "answer": "Sorry.",
        "claims": [],
        "status": status,
        "refusal_reason": "Because.",
        **overrides,
    }


def test_valid_answered_response():
    r = NutritionResponse.model_validate(answered())
    assert r.status is ResponseStatus.answered
    assert r.refusal_reason is None
    assert r.claims[0].source.chunk_id == "who_healthy_diet_chunk_012"


@pytest.mark.parametrize("status", ["not_in_corpus", "out_of_scope", "error"])
def test_valid_refusals(status):
    r = NutritionResponse.model_validate(refusal(status))
    assert r.claims == []


def test_answered_requires_claims():
    with pytest.raises(ValidationError, match="at least one claim"):
        NutritionResponse.model_validate(answered(claims=[]))


def test_answered_must_not_carry_refusal_reason():
    with pytest.raises(ValidationError, match="must be null"):
        NutritionResponse.model_validate(answered(refusal_reason="nope"))


@pytest.mark.parametrize("status", ["not_in_corpus", "out_of_scope", "error"])
def test_refusal_requires_reason(status):
    for reason in (None, "", "   "):
        with pytest.raises(ValidationError, match="refusal_reason is required"):
            NutritionResponse.model_validate(refusal(status, refusal_reason=reason))


@pytest.mark.parametrize("status", ["not_in_corpus", "out_of_scope", "error"])
def test_refusal_must_have_empty_claims(status):
    with pytest.raises(ValidationError, match="claims must be empty"):
        NutritionResponse.model_validate(refusal(status, claims=[CLAIM]))


def test_unknown_status_rejected():
    with pytest.raises(ValidationError):
        NutritionResponse.model_validate(answered(status="maybe"))


@pytest.mark.parametrize(
    "bad",
    [
        {"answer": "   "},
        {"claims": [{"claim_text": "", "source": SOURCE}]},
        {"claims": [{"claim_text": "x", "source": {**SOURCE, "chunk_id": ""}}]},
        {"claims": [{"claim_text": "x", "source": {**SOURCE, "year": "twenty"}}]},
        {"claims": [{"claim_text": "x", "source": {k: v for k, v in SOURCE.items() if k != "url"}}]},
    ],
)
def test_malformed_content_rejected(bad):
    with pytest.raises(ValidationError):
        NutritionResponse.model_validate(answered(**bad))


def test_missing_fields_rejected():
    with pytest.raises(ValidationError):
        NutritionResponse.model_validate({"answer": "x", "status": "answered"})


def test_chat_request_validation():
    assert ChatRequest(question="  hello  ").question == "hello"
    assert ChatRequest(question="q", conversation_id=None).conversation_id is None
    with pytest.raises(ValidationError):
        ChatRequest(question="   ")
    with pytest.raises(ValidationError):
        ChatRequest(question="x" * 1001)
    with pytest.raises(ValidationError):
        ChatRequest(question="q", conversation_id="not-a-uuid")


def test_chat_request_rejects_nul_characters():
    with pytest.raises(ValidationError):
        ChatRequest(question="a" + chr(0) + "b")


def test_create_conversation_title_rules():
    assert CreateConversationRequest(title="  Dinner  ").title == "Dinner"
    assert CreateConversationRequest().title is None
    with pytest.raises(ValidationError):
        CreateConversationRequest(title="t" * 256)
    with pytest.raises(ValidationError):
        CreateConversationRequest(title="a" + chr(0))


def test_update_conversation_title_rules():
    assert UpdateConversationRequest(title="  Dinner  ").title == "Dinner"
    for bad in ("", "   ", "t" * 256, "a" + chr(0)):
        with pytest.raises(ValidationError):
            UpdateConversationRequest(title=bad)
    with pytest.raises(ValidationError):
        UpdateConversationRequest()


def test_chat_response_inherits_invariants_and_serialises():
    ids = {"conversation_id": uuid.uuid4(), "message_id": uuid.uuid4()}
    with pytest.raises(ValidationError):
        ChatResponse(**ids, **answered(claims=[]))
    r = ChatResponse(**ids, **refusal("out_of_scope"))
    payload = r.model_dump(mode="json")
    assert payload["status"] == "out_of_scope"
    assert payload["retrieved_sources"] == []
    assert payload["conversation_id"] == str(ids["conversation_id"])


def test_error_envelope_forbids_extra_fields():
    ErrorEnvelope(error="invalid_request", message="bad")
    with pytest.raises(ValidationError):
        ErrorEnvelope(error="e", message="m", stack="leak")


def test_conversation_detail_roundtrip():
    now = datetime.now(timezone.utc)
    detail = ConversationDetail(
        id=uuid.uuid4(),
        title="t",
        created_at=now,
        updated_at=now,
        messages=[MessageOut(id=uuid.uuid4(), role="user", content="hi", created_at=now)],
    )
    assert ConversationDetail.model_validate(detail.model_dump(mode="json")) == detail


# ── frontend contract must not drift from the Pydantic models ────────────────

TS_FILE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types" / "api.ts"


def _ts_interface_fields(name: str) -> set[str]:
    text = TS_FILE.read_text(encoding="utf-8")
    body = re.search(rf"export interface {name}(?: extends \w+)? \{{(.*?)\n\}}", text, re.S)
    assert body, f"interface {name} missing from api.ts"
    return set(re.findall(r"^\s*(\w+)\??:", body.group(1), re.M))


@pytest.mark.parametrize(
    "ts_name,model",
    [
        ("SourceReference", SourceReference),
        ("Claim", Claim),
        ("NutritionResponse", NutritionResponse),
        ("MessageOut", MessageOut),
        ("HealthResponse", HealthResponse),
        ("CreateConversationRequest", CreateConversationRequest),
        ("UpdateConversationRequest", UpdateConversationRequest),
        ("ErrorEnvelope", ErrorEnvelope),
    ],
)
def test_typescript_interface_matches_model_fields(ts_name, model):
    assert _ts_interface_fields(ts_name) == set(model.model_fields)


def test_typescript_chat_response_fields():
    expected = set(ChatResponse.model_fields) - set(NutritionResponse.model_fields)
    assert expected <= _ts_interface_fields("ChatResponse")
    assert set(NutritionResponse.model_fields) <= _ts_interface_fields("ChatResponse") | _ts_interface_fields(
        "NutritionResponse"
    )


def test_typescript_status_union_matches_enum():
    text = TS_FILE.read_text(encoding="utf-8")
    union = re.search(r"export type ResponseStatus =([^;]+);", text).group(1)
    assert set(re.findall(r'"(\w+)"', union)) == {s.value for s in ResponseStatus}

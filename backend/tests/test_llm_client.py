import json
import os
from types import SimpleNamespace

import groq
import httpx
import pytest

from integrations.llm_client import LLMClient, LLMError, LLMOutputError, to_strict_schema
from models.schemas import NutritionResponse

SOURCE = {
    "document_name": "Healthy Diet Fact Sheet",
    "publisher": "World Health Organization (WHO)",
    "year": 2020,
    "section": "Fats",
    "url": "https://www.who.int/news-room/fact-sheets/detail/healthy-diet",
    "chunk_id": "who_healthy_diet_chunk_012",
}
GOOD = {
    "answer": "WHO limits saturated fat.",
    "claims": [{"claim_text": "Saturated fat should be under 10% of energy.", "source": SOURCE}],
    "status": "answered",
    "refusal_reason": None,
}
MESSAGES = [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]


def completion(content, reasoning=None, finish="stop"):
    message = SimpleNamespace(content=content, reasoning=reasoning)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def http_error(cls, status, headers=None):
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(status, request=request, headers=headers or {})
    return cls("error", response=response, body=None)


class FakeGroq:
    """Plays back a script of responses/exceptions and records every request."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def client_for(fake, **kwargs):
    sleeps = []
    kwargs.setdefault("backoff_base", 0.5)
    llm = LLMClient("key", "openai/gpt-oss-120b", "low", client=fake, sleep=sleeps.append, **kwargs)
    llm.sleeps = sleeps
    return llm


# ── structured output ────────────────────────────────────────────────────────

def test_smoke_prompt_returns_validated_nutrition_response():
    fake = FakeGroq(completion(json.dumps(GOOD)))
    result = client_for(fake).generate_structured(MESSAGES)
    assert isinstance(result.parsed, NutritionResponse)
    assert result.parsed.claims[0].source.chunk_id == "who_healthy_diet_chunk_012"
    assert result.model_info == "groq/openai/gpt-oss-120b"
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert result.attempts == 1
    assert result.mode == "json_schema_strict"


def test_request_uses_strict_schema_and_low_variance_settings():
    fake = FakeGroq(completion(json.dumps(GOOD)))
    client_for(fake).generate_structured(MESSAGES)
    req = fake.requests[0]
    assert req["model"] == "openai/gpt-oss-120b"
    assert req["temperature"] <= 0.2
    assert req["seed"] is not None
    assert req["reasoning_effort"] == "low"
    assert req["include_reasoning"] is False
    assert req["messages"] == MESSAGES
    fmt = req["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False


def test_strict_schema_is_strict_everywhere():
    schema = to_strict_schema(NutritionResponse.model_json_schema())

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                assert node["additionalProperties"] is False
                assert sorted(node["required"]) == sorted(node["properties"])
            for forbidden in ("default", "title", "minLength", "format"):
                assert forbidden not in node
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    # `properties` keys are field names (e.g. a field called "title") and must survive.
    check({k: v for k, v in schema.items() if k != "$defs"})
    for definition in schema["$defs"].values():
        check(definition)
    assert set(schema["required"]) == {"answer", "claims", "status", "refusal_reason"}
    assert set(schema["$defs"]["SourceReference"]["required"]) == {
        "document_name", "publisher", "year", "section", "url", "chunk_id"
    }
    refusal = schema["properties"]["refusal_reason"]
    assert {"type": "null"} in refusal["anyOf"]


def test_strict_schema_keeps_fields_named_like_keywords():
    from pydantic import BaseModel

    class Odd(BaseModel):
        title: str
        format: str

    schema = to_strict_schema(Odd.model_json_schema())
    assert set(schema["properties"]) == {"title", "format"}
    assert schema["required"] == ["title", "format"]


def test_conforming_output_for_every_status():
    for payload in (
        GOOD,
        {"answer": "No.", "claims": [], "status": "not_in_corpus", "refusal_reason": "Not covered."},
        {"answer": "No.", "claims": [], "status": "out_of_scope", "refusal_reason": "Medical."},
    ):
        result = client_for(FakeGroq(completion(json.dumps(payload)))).generate_structured(MESSAGES)
        assert result.parsed.status.value == payload["status"]


# ── reasoning must never become the answer ───────────────────────────────────

def test_reasoning_field_is_ignored_even_if_it_looks_like_valid_json():
    evil = json.dumps({**GOOD, "answer": "FROM REASONING"})
    fake = FakeGroq(completion(json.dumps(GOOD), reasoning=evil))
    result = client_for(fake).generate_structured(MESSAGES)
    assert result.parsed.answer == "WHO limits saturated fat."
    assert "FROM REASONING" not in result.raw_text


def test_empty_content_with_reasoning_is_an_error_not_a_parse_of_the_reasoning():
    fake = FakeGroq(completion("", reasoning=json.dumps(GOOD), finish="length"))
    with pytest.raises(LLMOutputError, match="no content") as info:
        client_for(fake).generate_structured(MESSAGES)
    assert info.value.raw_text is None


# ── invalid output keeps the raw text for the failure log ────────────────────

@pytest.mark.parametrize(
    "content",
    [
        "I think WHO says 10%.",
        "{not json",
        json.dumps({"answer": "x", "status": "answered"}),  # fields missing
        json.dumps({**GOOD, "claims": []}),  # violates the 'answered needs claims' invariant
        json.dumps({**GOOD, "status": "banana"}),
    ],
)
def test_invalid_output_raises_with_raw_text(content):
    with pytest.raises(LLMOutputError) as info:
        client_for(FakeGroq(completion(content))).generate_structured(MESSAGES)
    assert info.value.raw_text == content
    assert info.value.model_info == "groq/openai/gpt-oss-120b"


def test_markdown_fenced_json_is_accepted():
    fenced = "```json\n" + json.dumps(GOOD) + "\n```"
    assert client_for(FakeGroq(completion(fenced))).generate_structured(MESSAGES).parsed.status.value == "answered"


# ── retries ──────────────────────────────────────────────────────────────────

def test_retries_on_429_with_backoff_then_succeeds():
    fake = FakeGroq(
        http_error(groq.RateLimitError, 429),
        http_error(groq.InternalServerError, 503),
        completion(json.dumps(GOOD)),
    )
    llm = client_for(fake)
    result = llm.generate_structured(MESSAGES)
    assert result.attempts == 3
    assert len(llm.sleeps) == 2
    assert llm.sleeps[1] > llm.sleeps[0] * 0.9  # exponential, modulo jitter
    assert len(fake.requests) == 3


def test_retry_after_header_is_honoured():
    fake = FakeGroq(http_error(groq.RateLimitError, 429, {"retry-after": "7"}), completion(json.dumps(GOOD)))
    llm = client_for(fake)
    llm.generate_structured(MESSAGES)
    assert llm.sleeps == [7.0]


def test_retries_connection_and_timeout_errors():
    request = httpx.Request("POST", "https://api.groq.com")
    fake = FakeGroq(
        groq.APIConnectionError(request=request),
        groq.APITimeoutError(request=request),
        completion(json.dumps(GOOD)),
    )
    assert client_for(fake).generate_structured(MESSAGES).attempts == 3


def test_gives_up_after_max_retries():
    fake = FakeGroq(*[http_error(groq.RateLimitError, 429) for _ in range(3)])
    llm = client_for(fake, max_retries=2)
    with pytest.raises(LLMError, match="after 3 attempts"):
        llm.generate_structured(MESSAGES)
    assert len(fake.requests) == 3


@pytest.mark.parametrize("cls,status", [(groq.AuthenticationError, 401), (groq.PermissionDeniedError, 403)])
def test_client_errors_are_not_retried(cls, status):
    fake = FakeGroq(http_error(cls, status))
    with pytest.raises(LLMError):
        client_for(fake).generate_structured(MESSAGES)
    assert len(fake.requests) == 1


# ── fallback when strict json_schema is unsupported ──────────────────────────

def test_falls_back_to_json_object_mode_once_and_remembers():
    bad_request = http_error(groq.BadRequestError, 400)
    bad_request.message = "response_format json_schema with strict is not supported for this model"
    bad_request.args = (bad_request.message,)
    fake = FakeGroq(bad_request, completion(json.dumps(GOOD)), completion(json.dumps(GOOD)))
    llm = client_for(fake)

    first = llm.generate_structured(MESSAGES)
    assert first.mode == "json_object"
    assert fake.requests[0]["response_format"]["type"] == "json_schema"
    assert fake.requests[1]["response_format"] == {"type": "json_object"}
    assert "JSON schema" in fake.requests[1]["messages"][-1]["content"]

    second = llm.generate_structured(MESSAGES)  # does not retry strict again
    assert second.mode == "json_object"
    assert fake.requests[2]["response_format"] == {"type": "json_object"}


def test_unrelated_bad_request_is_an_error_not_a_fallback():
    bad = http_error(groq.BadRequestError, 400)
    bad.args = ("context length exceeded",)
    fake = FakeGroq(bad)
    with pytest.raises(LLMError):
        client_for(fake).generate_structured(MESSAGES)
    assert len(fake.requests) == 1


# ── complete_json (used by the safety classifier) ────────────────────────────

def test_complete_json_returns_dict():
    schema = {"type": "object", "properties": {"category": {"type": "string"}}, "required": ["category"]}
    fake = FakeGroq(completion('{"category": "none"}'))
    assert client_for(fake).complete_json(MESSAGES, schema, "safety_intent") == {"category": "none"}
    assert fake.requests[0]["response_format"]["json_schema"]["name"] == "safety_intent"


def test_complete_json_rejects_non_object():
    with pytest.raises(LLMOutputError):
        client_for(FakeGroq(completion("[1, 2]"))).complete_json(MESSAGES, {"type": "object"}, "x")


# ── configuration ────────────────────────────────────────────────────────────

def test_missing_api_key_fails_fast():
    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        LLMClient("", "openai/gpt-oss-120b")


def test_from_settings_reads_model_and_effort(monkeypatch):
    from config import load_settings

    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("GROQ_REASONING_EFFORT", "medium")
    llm = LLMClient.from_settings(load_settings())
    assert (llm.model, llm.reasoning_effort, llm.model_info) == (
        "openai/gpt-oss-20b", "medium", "groq/openai/gpt-oss-20b",
    )


def test_api_key_is_never_logged(caplog):
    caplog.set_level("DEBUG")
    fake = FakeGroq(http_error(groq.RateLimitError, 429), completion(json.dumps(GOOD)))
    LLMClient("sk-secret-123", "m", client=fake, sleep=lambda s: None).generate_structured(MESSAGES)
    assert "sk-secret-123" not in caplog.text


# ── live smoke test (opt-in: spends a few tokens on the real API) ────────────

@pytest.mark.skipif(
    not (os.getenv("GROQ_API_KEY") and os.getenv("RUN_LIVE_LLM_TESTS")),
    reason="set GROQ_API_KEY and RUN_LIVE_LLM_TESTS=1 to call Groq",
)
def test_live_smoke_prompt_conforms_to_schema():
    from core.prompts import load_system_prompt

    llm = LLMClient.from_settings()
    context = (
        "CONTEXT\n[chunk_id=who_healthy_diet_chunk_012 | document_name=Healthy Diet Fact Sheet | "
        "publisher=World Health Organization (WHO) | year=2020 | section=Fats | "
        f"url={SOURCE['url']}]\nSaturated fats should be less than 10% of total energy intake.\n\n"
        "Documents searched: Healthy Diet Fact Sheet\n\nQUESTION\nHow much saturated fat does WHO recommend?"
    )
    result = llm.generate_structured(
        [{"role": "system", "content": load_system_prompt()}, {"role": "user", "content": context}]
    )
    assert result.parsed.status.value == "answered"
    assert result.parsed.claims[0].source.chunk_id == "who_healthy_diet_chunk_012"
    assert "10%" in result.parsed.claims[0].claim_text

"""Groq chat-completions wrapper for ``openai/gpt-oss-120b``.

* Structured output: ``response_format = json_schema`` with ``strict: true`` (supported for
  gpt-oss-120b per Groq's docs). If Groq rejects it, falls back once to ``json_object`` mode
  with the schema spelled out in the prompt. Pydantic validation is always the backstop.
* gpt-oss is a reasoning model. Reasoning is switched off in the response
  (``include_reasoning=False``) and, regardless, only ``message.content`` is ever parsed:
  ``message.reasoning`` is never read, returned or logged.
* Low temperature and a fixed seed for repeatability (Groq makes a best effort only).
* Retries with exponential backoff on 429 / 5xx / timeouts / connection errors, honouring
  ``Retry-After``. Other 4xx errors are not retried.

The API key is read from settings only; it is never logged.
"""

import copy
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from config import Settings, get_settings
from models.schemas import NutritionResponse

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_TEMPERATURE = 0.1
DEFAULT_SEED = 7
DEFAULT_TIMEOUT_S = 45.0
DEFAULT_MAX_RETRIES = 4
DEFAULT_MAX_COMPLETION_TOKENS = 4096  # reasoning tokens count against this budget
_MAX_BACKOFF_S = 20.0

# JSON-schema keywords dropped for strict mode; Pydantic re-checks them after parsing.
_STRICT_UNSUPPORTED = {
    "title", "default", "examples", "minLength", "maxLength", "pattern", "format",
    "minItems", "maxItems", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
}


class LLMError(RuntimeError):
    """The provider could not produce a usable completion (after retries)."""


class LLMOutputError(LLMError):
    """The model answered, but not with valid structured output.

    ``raw_text`` is kept so the caller can write it to the failure log (it is the model's
    ``content`` only, never its reasoning).
    """

    def __init__(self, message: str, raw_text: Optional[str], model_info: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text
        self.model_info = model_info


@dataclass(frozen=True)
class StructuredResult:
    parsed: BaseModel
    raw_text: str
    model_info: str
    finish_reason: Optional[str]
    usage: Optional[dict[str, int]]
    attempts: int
    mode: str  # "json_schema_strict" | "json_object"


def to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Turn a Pydantic JSON schema into one Groq's strict mode accepts.

    Strict mode needs every property listed in ``required`` and ``additionalProperties: false``
    on every object; optional fields become ``anyOf [..., null]`` (Pydantic already emits that).
    """
    schema = copy.deepcopy(schema)

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        cleaned = {k: walk(v) for k, v in node.items() if k not in _STRICT_UNSUPPORTED}
        # Property *names* may collide with the keywords above, so handle `properties`
        # separately: its keys are field names and must all survive.
        if isinstance(node.get("properties"), dict):
            cleaned["properties"] = {name: walk(sub) for name, sub in node["properties"].items()}
        if cleaned.get("type") == "object" or "properties" in cleaned:
            props = cleaned.get("properties", {})
            cleaned["required"] = list(props)
            cleaned["additionalProperties"] = False
        return cleaned

    result = walk(schema)
    if "$defs" in schema:
        result["$defs"] = {name: walk(sub) for name, sub in schema["$defs"].items()}
    return result


def _status_code(exc: BaseException) -> Optional[int]:
    return getattr(exc, "status_code", None)


def _is_retryable(exc: BaseException) -> bool:
    import groq

    if isinstance(exc, (groq.APIConnectionError, groq.APITimeoutError)):
        return True  # APITimeoutError subclasses APIConnectionError; listed for clarity
    status = _status_code(exc)
    return status is not None and (status in (408, 409, 429) or status >= 500)


def _retry_after_seconds(exc: BaseException) -> Optional[float]:
    response = getattr(exc, "response", None)
    value = getattr(response, "headers", {}).get("retry-after") if response is not None else None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    return match.group(1) if match else text


class LLMClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        reasoning_effort: str = "low",
        *,
        client: Any = None,
        temperature: float = DEFAULT_TEMPERATURE,
        seed: Optional[int] = DEFAULT_SEED,
        timeout: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = 1.0,
        max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if client is None and not api_key:
            raise LLMError("GROQ_API_KEY is not configured")
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.temperature = temperature
        self.seed = seed
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.max_completion_tokens = max_completion_tokens
        self._sleep = sleep
        self._api_key = api_key
        self._client = client
        self._strict_supported = True  # flipped off if Groq rejects strict json_schema

    @classmethod
    def from_settings(cls, settings: Optional[Settings] = None, **kwargs: Any) -> "LLMClient":
        settings = settings or get_settings()
        return cls(
            settings.groq_api_key, settings.groq_model, settings.groq_reasoning_effort, **kwargs
        )

    @property
    def model_info(self) -> str:
        """Provider + model id, as stored in failure logs."""
        return f"groq/{self.model}"

    def _groq(self) -> Any:
        if self._client is None:
            import groq

            # Retries are handled here (visible in logs), so the SDK's own are off.
            self._client = groq.Groq(api_key=self._api_key, max_retries=0, timeout=self.timeout)
        return self._client

    def check_available(self, timeout: float = 5.0) -> bool:
        """Health probe: the provider answers and offers the configured model. Lists models rather than
        retrieving one (Groq 404s a model id containing "/" on retrieve); spends no tokens. Never raises."""
        try:
            listing = self._groq().with_options(timeout=timeout, max_retries=0).models.list()
            offered = {m.id for m in listing.data}
        except Exception:
            logger.warning("LLM availability probe failed", exc_info=True)
            return False
        if self.model not in offered:
            logger.warning("LLM provider does not offer the configured model", extra={"model": self.model})
            return False
        return True

    # ── public API ───────────────────────────────────────────────────────────
    def generate_structured(
        self,
        messages: Sequence[dict[str, str]],
        response_model: type[T] = NutritionResponse,  # type: ignore[assignment]
        *,
        schema_name: str = "nutrition_response",
    ) -> StructuredResult:
        """Run a completion constrained to ``response_model`` and return it parsed.

        Raises ``LLMOutputError`` (with the raw text) if the output is empty, not JSON, or
        does not validate, and ``LLMError`` if the provider fails after all retries.
        """
        schema = to_strict_schema(response_model.model_json_schema())
        text, finish, usage, attempts, mode = self._complete(list(messages), schema, schema_name)
        try:
            parsed = response_model.model_validate(json.loads(_strip_code_fence(text)))
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning(
                "structured output rejected",
                extra={"model_info": self.model_info, "finish_reason": finish, "mode": mode},
            )
            raise LLMOutputError(f"model output failed validation: {exc}", text, self.model_info) from exc
        return StructuredResult(parsed, text, self.model_info, finish, usage, attempts, mode)

    def complete_json(self, messages: list[dict[str, str]], schema: dict[str, Any], name: str) -> dict:
        """Completion constrained to an arbitrary JSON schema; returns the parsed object.
        Used by the safety intent classifier."""
        text, *_ = self._complete(messages, to_strict_schema(schema), name)
        try:
            value = json.loads(_strip_code_fence(text))
        except json.JSONDecodeError as exc:
            raise LLMOutputError(f"model output is not JSON: {exc}", text, self.model_info) from exc
        if not isinstance(value, dict):
            raise LLMOutputError("model output is not a JSON object", text, self.model_info)
        return value

    # ── internals ────────────────────────────────────────────────────────────
    def _complete(
        self, messages: list[dict[str, str]], schema: dict[str, Any], name: str
    ) -> tuple[str, Optional[str], Optional[dict[str, int]], int, str]:
        import groq

        attempts = 0
        while True:
            strict = self._strict_supported
            try:
                response, n = self._call_with_retries(messages, schema, name, strict)
                attempts += n
                break
            except groq.BadRequestError as exc:
                if strict and self._rejects_json_schema(exc):
                    logger.warning(
                        "Groq rejected strict json_schema; falling back to json_object mode",
                        extra={"model_info": self.model_info, "error": str(exc)[:300]},
                    )
                    self._strict_supported = False
                    continue
                raise LLMError(f"Groq rejected the request: {exc}") from exc

        choice = response.choices[0]
        # Only `content` is the answer. `message.reasoning` is deliberately never touched.
        text = choice.message.content or ""
        finish = getattr(choice, "finish_reason", None)
        usage = self._usage_dict(getattr(response, "usage", None))
        mode = "json_schema_strict" if strict else "json_object"
        if not text.strip():
            raise LLMOutputError(
                f"model returned no content (finish_reason={finish}); "
                "the token budget may have been consumed by reasoning",
                None,
                self.model_info,
            )
        return text, finish, usage, attempts, mode

    def _call_with_retries(self, messages, schema, name, strict) -> tuple[Any, int]:
        import groq

        request = self._build_request(messages, schema, name, strict)
        for attempt in range(1, self.max_retries + 2):
            started = time.monotonic()
            try:
                response = self._groq().chat.completions.create(**request)
            except groq.BadRequestError:
                raise
            except Exception as exc:
                if not _is_retryable(exc):
                    raise LLMError(f"Groq call failed: {exc}") from exc
                if attempt > self.max_retries:
                    raise LLMError(
                        f"Groq call failed after {attempt} attempts: {exc}"
                    ) from exc
                delay = self._backoff(attempt, exc)
                logger.warning(
                    "Groq call failed, retrying",
                    extra={
                        "model_info": self.model_info,
                        "attempt": attempt,
                        "status_code": _status_code(exc),
                        "retry_in_s": round(delay, 2),
                    },
                )
                self._sleep(delay)
                continue
            logger.info(
                "Groq call ok",
                extra={
                    "model_info": self.model_info,
                    "attempt": attempt,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "usage": self._usage_dict(getattr(response, "usage", None)),
                },
            )
            return response, attempt
        raise AssertionError("unreachable")  # pragma: no cover

    def _backoff(self, attempt: int, exc: BaseException) -> float:
        advised = _retry_after_seconds(exc)
        if advised is not None:
            return min(max(advised, 0.0), _MAX_BACKOFF_S * 3)
        return min(self.backoff_base * 2 ** (attempt - 1), _MAX_BACKOFF_S) * random.uniform(0.75, 1.25)

    def _build_request(self, messages, schema, name, strict) -> dict[str, Any]:
        if strict:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            }
            sent = messages
        else:
            response_format = {"type": "json_object"}
            sent = [
                *messages,
                {
                    "role": "system",
                    "content": "Respond with a single JSON object that conforms exactly to this "
                    f"JSON schema, and nothing else:\n{json.dumps(schema)}",
                },
            ]
        request: dict[str, Any] = {
            "model": self.model,
            "messages": sent,
            "temperature": self.temperature,
            "response_format": response_format,
            "max_completion_tokens": self.max_completion_tokens,
            "reasoning_effort": self.reasoning_effort,
            "include_reasoning": False,
            "timeout": self.timeout,
        }
        if self.seed is not None:
            request["seed"] = self.seed
        return request

    @staticmethod
    def _rejects_json_schema(exc: BaseException) -> bool:
        text = str(exc).lower()
        return any(k in text for k in ("json_schema", "response_format", "structured output", "strict"))

    @staticmethod
    def _usage_dict(usage: Any) -> Optional[dict[str, int]]:
        if usage is None:
            return None
        return {
            key: int(getattr(usage, key))
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance(getattr(usage, key, None), (int, float))
        }

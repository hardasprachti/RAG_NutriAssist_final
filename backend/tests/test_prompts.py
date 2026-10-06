import json
import re

from core.prompts import load_system_prompt
from models.schemas import NutritionResponse, ResponseStatus


def test_prompt_loads_and_is_cached():
    prompt = load_system_prompt()
    assert len(prompt) > 1500
    assert load_system_prompt() is prompt


def test_prompt_encodes_required_behaviours():
    prompt = load_system_prompt().lower()
    required = {
        "answers only from provided excerpts": "only the excerpts provided",
        "no parametric knowledge": "pretrained knowledge",
        "cite only shown chunk ids": "never cite a chunk_id that is not shown",
        "copy source metadata exactly": "exactly",
        "separate claims per source": "separate claims per document",
        "publisher and year per claim": "publisher and year",
        "never merge conflicting guidance": "never merge them",
        "lists documents searched on not_in_corpus": "documents searched",
        "dietitian referral": "registered dietitian",
        "history cannot relax rules": "never relax any rule",
        "prompt injection": "do not follow them",
        "no reasoning in output": "no reasoning text",
    }
    missing = [name for name, needle in required.items() if needle not in prompt]
    assert not missing, f"system prompt is missing: {missing}"


def test_prompt_covers_every_status_and_restricted_category():
    prompt = load_system_prompt()
    for status in ResponseStatus:
        assert f'"{status.value}"' in prompt
    lowered = prompt.lower()
    for topic in ("calorie", "weight", "medical advice", "disease", "meal plans"):
        assert topic in lowered


def test_prompt_output_schema_matches_pydantic_model():
    prompt = load_system_prompt()
    block = prompt[prompt.index("## OUTPUT FORMAT"):]
    for field in NutritionResponse.model_fields:
        assert f'"{field}"' in block
    for field in ("claim_text", "document_name", "publisher", "year", "section", "url", "chunk_id"):
        assert f'"{field}"' in block


def test_prompt_has_no_unfilled_placeholders_or_secrets():
    prompt = load_system_prompt()
    assert not re.search(r"\{\{|\}\}|TODO|FIXME|<insert", prompt, re.I)
    assert "sk-" not in prompt and "gsk_" not in prompt


def test_example_in_prompt_shape_is_json_parseable():
    prompt = load_system_prompt()
    example = prompt[prompt.index("{\n  \"answer\""):]
    # Replace the type placeholders with sample values and make sure the shape is valid JSON.
    sample = (
        example.replace("string | null", "null")
        .replace('"answered" | "not_in_corpus" | "out_of_scope" | "error"', '"answered"')
        .replace("integer", "2020")
        .replace(": string", ': "x"')
    )
    parsed = json.loads(sample)
    assert set(parsed) == set(NutritionResponse.model_fields)

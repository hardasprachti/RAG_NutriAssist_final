"""Loads the fixed system prompt from ``backend/prompts/``."""

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache
def load_system_prompt() -> str:
    path = PROMPTS_DIR / "system_prompt.txt"
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"{path} is empty")
    return text

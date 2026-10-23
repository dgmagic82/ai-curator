"""Settings: models, paths, limits.

The OpenRouter key is never stored in the code or the repository. It is read, in this order, from:
the OPENROUTER_API_KEY environment variable, Streamlit secrets (Streamlit Cloud), the local file
~/.secrets/common.env (author's machine).
"""

import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB_DIR = ROOT / "kb"
INDEX_DIR = ROOT / "index"
LMS_DIR = ROOT / "lms"
PROMPTS_DIR = ROOT / "prompts"
LOG_FILE = ROOT / "logs" / "queries.jsonl"
SAMPLE_LOG_FILE = ROOT / "logs" / "sample_queries.jsonl"

BASE_URL = "https://openrouter.ai/api/v1"
CHAT_MODEL = os.environ.get("CURATOR_CHAT_MODEL", "openai/gpt-4.1-mini")
EMBED_MODEL = "openai/text-embedding-3-small"
TEMPERATURE = 0.2
TOP_K = 4

MAX_QUESTION_CHARS = 1000
MAX_QUESTIONS_PER_SESSION = 15

# The LMS data is a frozen demo of one cohort, so "today" is fixed too; otherwise every deadline
# would drift into the past a week after publishing.
DEMO_TODAY = date.fromisoformat(os.environ.get("CURATOR_DEMO_TODAY", "2026-10-23"))


def api_key() -> str:
    if key := os.environ.get("OPENROUTER_API_KEY"):
        return key
    try:
        import streamlit as st
        if "OPENROUTER_API_KEY" in st.secrets:
            return st.secrets["OPENROUTER_API_KEY"]
    except Exception:  # no secrets.toml: Streamlit raises instead of returning empty
        pass
    env_file = Path.home() / ".secrets" / "common.env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "OPENROUTER_API_KEY" and value.strip():
                return value.strip().strip("'\"")
    raise RuntimeError("Нет ключа OPENROUTER_API_KEY: задайте переменную окружения или Streamlit secrets")

"""Настройки приложения. Всё читается из .env в корне проекта."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)


def env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
STATIC_DIR = ROOT / "static"
# Промпт заказчика не хранится в репозитории: положите его в prompts/royal_brick_auditor.md
# (или укажите путь в PROMPT_PATH). Без него используется пример той же структуры.
_client_prompt = ROOT / "prompts" / "royal_brick_auditor.md"
PROMPT_PATH = Path(env("PROMPT_PATH") or (_client_prompt if _client_prompt.exists()
                                          else ROOT / "prompts" / "example_prompt.md"))
STYLE_ADDENDUM_PATH = ROOT / "prompts" / "client_texts_style.md"

# --- Распознавание речи (AssemblyAI) ---
ASSEMBLYAI_API_KEY = env("ASSEMBLYAI_API_KEY")
ASSEMBLYAI_BASE_URL = env("ASSEMBLYAI_BASE_URL", "https://api.assemblyai.com")
ASSEMBLYAI_SPEECH_MODEL = env("ASSEMBLYAI_SPEECH_MODEL", "universal-2")
ASSEMBLYAI_LANGUAGE = env("ASSEMBLYAI_LANGUAGE", "ru")

# --- Анализ (LLM) ---
# openai    — OpenAI или любой OpenAI-совместимый API (OpenRouter, Groq, локальный Ollama и т. п.)
# anthropic — Claude API
LLM_PROVIDER = env("LLM_PROVIDER", "openai").lower()

OPENAI_API_KEY = env("OPENAI_API_KEY")
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = env("OPENAI_BASE_URL")  # пусто = официальный API OpenAI

ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", "claude-sonnet-5-5")
# Своя переменная, чтобы не подхватить системный ANTHROPIC_BASE_URL (его выставляют, например, IDE-агенты)
ANTHROPIC_API_BASE = env("ANTHROPIC_API_BASE", "https://api.anthropic.com")

LLM_MAX_TOKENS = int(env("LLM_MAX_TOKENS", "16000"))

# --- Прочее ---
APP_PASSWORD = env("APP_PASSWORD")  # если задан — вход по Basic Auth (для публичного деплоя)
MAX_UPLOAD_MB = int(env("MAX_UPLOAD_MB", "1024"))

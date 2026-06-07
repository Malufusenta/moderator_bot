"""
Загрузка и валидация конфигурации из .env.

Все внешние параметры из раздела 3 ТЗ приводятся к нужным типам здесь.
Остальные модули импортируют готовые константы — не читают os.environ напрямую.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _parse_id_list(raw: str) -> list[int]:
    """Разобрать строку вида '123,456' в список int-ов."""
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def _parse_stop_words(raw: str) -> list[str]:
    """Разобрать строку стоп-слов через запятую; нижний регистр, без пробелов по краям."""
    if not raw:
        return []
    return [w.strip().lower() for w in raw.split(",") if w.strip()]


# ─── Telegram ─────────────────────────────────────────────────────────────────

BOT_TOKEN: str = os.environ["BOT_TOKEN"]

# API-реквизиты для Pyrogram (parser.py)
API_ID: int = int(os.environ["API_ID"])
API_HASH: str = os.environ["API_HASH"]

CHAT_ID: int = int(os.environ["CHAT_ID"])
ADMIN_IDS: list[int] = _parse_id_list(os.getenv("ADMIN_IDS", ""))
WHITELIST_IDS: list[int] = _parse_id_list(os.getenv("WHITELIST_IDS", ""))

# ─── Пороги модерации (раздел 3 ТЗ) ──────────────────────────────────────────

TRUST_LIMIT: int = int(os.getenv("TRUST_LIMIT", "30"))
RECENCY_DAYS: int = int(os.getenv("RECENCY_DAYS", "60"))
RECENCY_MIN_MESSAGES: int = int(os.getenv("RECENCY_MIN_MESSAGES", "1"))
SIMILARITY_THRESHOLD: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.85"))

MUTE_HOURS: int = int(os.getenv("MUTE_HOURS", "24"))
WARNING_DELETE_SECONDS: int = int(os.getenv("WARNING_DELETE_SECONDS", "15"))

# ─── БД ───────────────────────────────────────────────────────────────────────

DB_PATH: str = os.getenv("DB_PATH", "data/bot.db")

# Создать папку для БД, если её ещё нет
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

# ─── Стоп-слова (раздел 6.2 ТЗ) ──────────────────────────────────────────────
# Можно переопределить через STOP_WORDS в .env (через запятую).
# По умолчанию — базовый набор ключевых форм.

_STOP_WORDS_ENV = os.getenv("STOP_WORDS", "")

STOP_WORDS: list[str] = _parse_stop_words(_STOP_WORDS_ENV) if _STOP_WORDS_ENV else [
    "продам",
    "продаю",
    "продаётся",
    "продается",
    "продаëтся",   # ё через е-краткое (гомоглиф)
    "сдам",
    "сдаю",
    "аренда",
    "к продаже",
    "в аренду",
    "снять",
    "сниму",
]

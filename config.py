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
# Стаж в днях, ниже которого участник считается «Новичком» в досье (display only)
NEWCOMER_DAYS: int = int(os.getenv("NEWCOMER_DAYS", "30"))
SIMILARITY_THRESHOLD: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.85"))

MUTE_HOURS: int = int(os.getenv("MUTE_HOURS", "24"))
WARNING_DELETE_SECONDS: int = int(os.getenv("WARNING_DELETE_SECONDS", "15"))

# ID чата/канала для лога модераторских действий. 0 = логирование выключено.
LOG_CHAT_ID: int = int(os.getenv("LOG_CHAT_ID", "0"))

# ─── Режим наблюдения ─────────────────────────────────────────────────────────

# Если True — бот НЕ удаляет сообщения, НЕ мутит, НЕ предупреждает.
# Счётчики активности и ad_attempts пишутся как обычно — поведение совпадает
# с боевым, кроме карательных действий. Удобно для начальной настройки порогов.
DRY_RUN: bool = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")

# ─── БД ───────────────────────────────────────────────────────────────────────

DB_PATH: str = os.getenv("DB_PATH", "data/bot.db")

# Человекочитаемые имена инвайт-ссылок для досье (раздел 9 ТЗ).
# Ключ — точный URL ссылки (https://t.me/+XXXX), значение — отображаемое имя.
# Владелец заполняет реальными URL перед деплоем.
INVITE_LINKS: dict[str, str] = {
    "https://t.me/+mdrYW6DeLek5OTYy": "Лобби",
    "https://t.me/+nWEB7ZFp7m0yZjAy": "Парсер",
}

# Создать папку для БД, если её ещё нет
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

# ─── Стоп-слова (раздел 6.2 ТЗ) ──────────────────────────────────────────────
# STOP_WORDS_MEDIA — требуют медиа (фото/видео) в сообщении.
# STOP_WORDS_TEXT  — срабатывают на любой текст, без медиа.
# Переопределяются через STOP_WORDS_MEDIA / STOP_WORDS_TEXT в .env.

_SW_MEDIA_ENV = os.getenv("STOP_WORDS_MEDIA", "")
STOP_WORDS_MEDIA: list[str] = _parse_stop_words(_SW_MEDIA_ENV) if _SW_MEDIA_ENV else [
    "продам",
    "продаю",
    "продается",
    "продаются",
    "продаётся",
    "продаëтся",   # ё через е-краткое (гомоглиф)
    "к продаже",
    "аренда",
    "пересдача",
    "в аренду",
]

_SW_TEXT_ENV = os.getenv("STOP_WORDS_TEXT", "")
STOP_WORDS_TEXT: list[str] = _parse_stop_words(_SW_TEXT_ENV) if _SW_TEXT_ENV else [
    "сдам",
    "сдаю",
    "сдается",
    "сдаётся",
    "пересдам",
    "пересдаю",
    "пересдача",
    "пересдается",
    "пересдаётся",
]

# Объединённый список — для обратной совместимости и env-оверрайда STOP_WORDS.
_SW_LEGACY_ENV = os.getenv("STOP_WORDS", "")
STOP_WORDS: list[str] = (
    _parse_stop_words(_SW_LEGACY_ENV) if _SW_LEGACY_ENV
    else STOP_WORDS_MEDIA + STOP_WORDS_TEXT
)

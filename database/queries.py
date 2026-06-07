"""
CRUD-операции и атомарные обновления для всех трёх таблиц.

Все счётчики (message_count, ad_attempts) инкрементируются атомарным UPDATE,
чтобы избежать гонок при конкурентных запросах (раздел 8 ТЗ).

# TODO (раздел 6.1 ТЗ): реализовать upsert_user, increment_message_count,
#   add_message_row, get_recent_message_count.
# TODO (раздел 6.3 ТЗ): реализовать get_user, increment_ad_attempts.
# TODO (раздел 6.4 ТЗ): реализовать get_user_ads, save_advertisement.
# TODO (раздел 7 ТЗ): реализовать get_user_profile (для досье).
"""

from __future__ import annotations

import aiosqlite


# ─── users ────────────────────────────────────────────────────────────────────

async def upsert_user(
    conn: aiosqlite.Connection,
    user_id: int,
    username: str | None,
    now_ts: int,
) -> None:
    """Создать запись пользователя или обновить username и last_message_at.

    # TODO (раздел 6.1): INSERT OR IGNORE + UPDATE username/last_message_at.
    """
    raise NotImplementedError


async def get_user(conn: aiosqlite.Connection, user_id: int) -> dict | None:
    """Вернуть строку таблицы users как dict или None, если пользователь не найден.

    # TODO (раздел 6.3): SELECT * FROM users WHERE user_id = ?
    """
    raise NotImplementedError


async def increment_message_count(conn: aiosqlite.Connection, user_id: int) -> None:
    """Атомарно инкрементировать message_count и обновить last_message_at.

    # TODO (раздел 6.1): UPDATE users SET message_count = message_count + 1,
    #   last_message_at = ? WHERE user_id = ?
    """
    raise NotImplementedError


async def increment_ad_attempts(conn: aiosqlite.Connection, user_id: int) -> None:
    """Атомарно инкрементировать ad_attempts.

    # TODO (раздел 6.3, 6.4): UPDATE users SET ad_attempts = ad_attempts + 1
    #   WHERE user_id = ?
    """
    raise NotImplementedError


# ─── messages ─────────────────────────────────────────────────────────────────

async def add_message_row(
    conn: aiosqlite.Connection,
    user_id: int,
    created_at: int,
) -> None:
    """Записать строку в таблицу messages для подсчёта свежести.

    # TODO (раздел 6.1): INSERT INTO messages (user_id, created_at) VALUES (?, ?)
    """
    raise NotImplementedError


async def get_recent_message_count(
    conn: aiosqlite.Connection,
    user_id: int,
    since_ts: int,
) -> int:
    """Вернуть количество сообщений пользователя начиная с since_ts (без текущего).

    # TODO (раздел 6.3): SELECT COUNT(*) FROM messages WHERE user_id = ?
    #   AND created_at >= ?   (текущее сообщение ещё не записано на этом шаге)
    """
    raise NotImplementedError


# ─── advertisements ───────────────────────────────────────────────────────────

async def get_user_ads(
    conn: aiosqlite.Connection,
    user_id: int,
) -> list[dict]:
    """Вернуть все прошлые объявления пользователя (normalized_text + text_hash).

    # TODO (раздел 6.4): SELECT normalized_text, text_hash FROM advertisements
    #   WHERE user_id = ?
    """
    raise NotImplementedError


async def save_advertisement(
    conn: aiosqlite.Connection,
    user_id: int,
    normalized_text: str,
    text_hash: str,
    created_at: int,
) -> None:
    """Сохранить прошедшее проверку объявление.

    # TODO (раздел 6.4): INSERT INTO advertisements
    #   (user_id, normalized_text, text_hash, created_at) VALUES (?, ?, ?, ?)
    """
    raise NotImplementedError


# ─── досье ────────────────────────────────────────────────────────────────────

async def get_user_profile(
    conn: aiosqlite.Connection,
    user_id: int,
    recency_since_ts: int,
) -> dict | None:
    """Собрать данные для досье: строка users + счётчик сообщений за окно.

    # TODO (раздел 7): JOIN users + COUNT из messages за recency_since_ts,
    #   вернуть dict со всеми полями для форматирования досье.
    """
    raise NotImplementedError

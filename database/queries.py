"""
CRUD-операции и атомарные обновления для всех трёх таблиц.

Принципы:
  - Счётчики (message_count, ad_attempts) инкрементируются строго атомарным
    UPDATE без read-modify-write, чтобы исключить гонки (раздел 8 ТЗ).
  - Все SQL-запросы параметризованы (?, ?), никаких f-строк в SQL.
  - Время: INTEGER unix-timestamp UTC (datetime.now(timezone.utc).timestamp() → int).
  - foreign_keys=ON включены в db.py — порядок важен: сначала users, потом messages/ads.
"""

from __future__ import annotations

import aiosqlite


# ─── Вспомогательная функция ──────────────────────────────────────────────────

def _row_to_dict(row: aiosqlite.Row | None) -> dict | None:
    """Конвертировать aiosqlite.Row в обычный dict (или None)."""
    if row is None:
        return None
    return dict(row)


# ─── users ────────────────────────────────────────────────────────────────────

async def upsert_user(
    conn: aiosqlite.Connection,
    user_id: int,
    username: str | None,
    now_ts: int,
) -> None:
    """Атомарный UPSERT пользователя с инкрементом message_count.

    При первом появлении пользователя создаёт запись с message_count=1
    и first_message_at=now_ts. При повторном — атомарно инкрементирует
    message_count, обновляет last_message_at и username; first_message_at
    не трогает (COALESCE сохраняет существующее значение).

    Вызывается из handlers.messages на каждое входящее сообщение (раздел 6.1).
    foreign_keys=ON требует, чтобы users-строка существовала ДО INSERT в messages.
    """
    await conn.execute(
        """
        INSERT INTO users (user_id, username, message_count, first_message_at, last_message_at)
        VALUES (?, ?, 1, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username         = COALESCE(excluded.username, username),
            message_count    = message_count + 1,
            last_message_at  = excluded.last_message_at,
            first_message_at = COALESCE(first_message_at, excluded.first_message_at)
        """,
        (user_id, username, now_ts, now_ts),
    )
    await conn.commit()


async def get_user(conn: aiosqlite.Connection, user_id: int) -> dict | None:
    """Вернуть строку таблицы users как dict или None.

    Используется в гейте доверия (раздел 6.3) для чтения message_count.
    """
    cursor = await conn.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def find_user_id_by_username(
    conn: aiosqlite.Connection,
    username: str,
) -> int | None:
    """Найти user_id по username (регистронезависимо).

    Username ненадёжен — может измениться (раздел 8 ТЗ). Возвращает последний
    известный user_id для данного username или None, если не найден.
    Используется в admin-хендлере для /check @username.
    """
    cursor = await conn.execute(
        """
        SELECT user_id FROM users
        WHERE lower(username) = lower(?)
        ORDER BY last_message_at DESC
        LIMIT 1
        """,
        (username,),
    )
    row = await cursor.fetchone()
    return row["user_id"] if row else None


async def increment_message_count(
    conn: aiosqlite.Connection,
    user_id: int,
    now_ts: int,
) -> None:
    """Атомарно инкрементировать message_count и обновить last_message_at.

    Отдельная функция для parser.py, где пользователь уже может существовать
    в БД и full UPSERT не нужен (раздел 5 ТЗ).
    """
    await conn.execute(
        """
        UPDATE users
        SET message_count   = message_count + 1,
            last_message_at = ?
        WHERE user_id = ?
        """,
        (now_ts, user_id),
    )
    await conn.commit()


async def increment_ad_attempts(
    conn: aiosqlite.Connection,
    user_id: int,
    username: str | None = None,
    now_ts: int | None = None,
) -> None:
    """Атомарный UPSERT: инкрементировать ad_attempts, создав строку если нужно.

    now_ts передаётся из хендлера и записывается в last_ad_attempt_at —
    позволяет показывать дату последнего нарушения в досье.

    message_count НЕ трогается: удалённое объявление не должно
    засчитываться как «активность» и копить доверие (раздел 6.3 ТЗ).
    """
    await conn.execute(
        """
        INSERT INTO users (user_id, username, ad_attempts, last_ad_attempt_at)
        VALUES (?, ?, 1, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            ad_attempts        = ad_attempts + 1,
            username           = COALESCE(excluded.username, users.username),
            last_ad_attempt_at = excluded.last_ad_attempt_at
        """,
        (user_id, username, now_ts),
    )
    await conn.commit()


# ─── messages ─────────────────────────────────────────────────────────────────

async def add_message_row(
    conn: aiosqlite.Connection,
    user_id: int,
    created_at: int,
) -> None:
    """Записать строку в таблицу messages (раздел 6.1 ТЗ).

    Вызывается ПОСЛЕ upsert_user: foreign_keys=ON требует, чтобы запись
    в users уже существовала до INSERT в messages.

    Эта таблица даёт честную метрику «N сообщений за окно» для гейта
    доверия (раздел 6.3), в отличие от одного поля last_message_at.
    """
    await conn.execute(
        "INSERT INTO messages (user_id, created_at) VALUES (?, ?)",
        (user_id, created_at),
    )
    await conn.commit()


async def get_recent_message_count(
    conn: aiosqlite.Connection,
    user_id: int,
    since_ts: int,
) -> int:
    """Вернуть число сообщений пользователя начиная с since_ts.

    Параметр since_ts принимается снаружи (а не вычисляется здесь),
    чтобы функция была тестируемой без зависимости от системного времени.

    Вызывается из гейта доверия ДО записи текущего сообщения (раздел 6.3),
    поэтому текущее сообщение в счёт не входит.

    Пример вызова из хендлера:
        cutoff = int((datetime.now(timezone.utc)
                      - timedelta(days=config.RECENCY_DAYS)).timestamp())
        count = await get_recent_message_count(conn, user_id, cutoff)
    """
    cursor = await conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM messages
        WHERE user_id = ? AND created_at >= ?
        """,
        (user_id, since_ts),
    )
    row = await cursor.fetchone()
    return row["cnt"] if row else 0


# ─── advertisements ───────────────────────────────────────────────────────────

async def get_user_ads(
    conn: aiosqlite.Connection,
    user_id: int,
) -> list[dict]:
    """Вернуть все прошлые объявления пользователя.

    Используется в анти-дубле (раздел 6.4): сначала проверяем text_hash
    для быстрого точного совпадения, затем normalized_text для difflib.
    """
    cursor = await conn.execute(
        """
        SELECT normalized_text, text_hash
        FROM advertisements
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (user_id,),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def save_advertisement(
    conn: aiosqlite.Connection,
    user_id: int,
    normalized_text: str,
    text_hash: str,
    created_at: int,
) -> None:
    """Сохранить объявление, прошедшее проверку на дубли (раздел 6.4 ТЗ).

    Вызывается только когда объявление признано оригинальным — иначе оно
    удаляется и не попадает в историю.
    """
    await conn.execute(
        """
        INSERT INTO advertisements (user_id, normalized_text, text_hash, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, normalized_text, text_hash, created_at),
    )
    await conn.commit()


# ─── досье (раздел 7) ─────────────────────────────────────────────────────────

async def get_user_profile(
    conn: aiosqlite.Connection,
    user_id: int,
    recency_since_ts: int,
) -> dict | None:
    """Собрать все данные для досье администратора (раздел 7 ТЗ).

    Возвращает dict со всеми полями users плюс:
      messages_last_period — количество сообщений с recency_since_ts по сей день.

    Поиск по user_id. Если пользователь не найден в БД — возвращает None.
    recency_since_ts передаётся снаружи (тестируемость).

    Формат вывода статуса в досье определяется хендлером по правилам ТЗ:
      «Доверенный»  — message_count >= TRUST_LIMIT И messages_last_period >= RECENCY_MIN_MESSAGES
      «Спящий»      — message_count >= TRUST_LIMIT И messages_last_period  < RECENCY_MIN_MESSAGES
      «Новичок»     — message_count  < TRUST_LIMIT
    """
    cursor = await conn.execute(
        """
        SELECT
            u.user_id,
            u.username,
            u.joined_at,
            u.invite_link,
            u.message_count,
            u.first_message_at,
            u.last_message_at,
            u.ad_attempts,
            u.last_ad_attempt_at,
            u.added_by,
            COUNT(m.id) AS messages_last_period
        FROM users u
        LEFT JOIN messages m
               ON m.user_id = u.user_id
              AND m.created_at >= ?
        WHERE u.user_id = ?
        GROUP BY u.user_id
        """,
        (recency_since_ts, user_id),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row)


# ─── вступление в чат (раздел 9) ─────────────────────────────────────────────

async def record_join(
    conn: aiosqlite.Connection,
    user_id: int,
    username: str | None,
    joined_at: int,
    invite_link: str | None,
    added_by: int | None,
) -> None:
    """Зафиксировать вступление участника в чат.

    Трогает только joined_at, invite_link, added_by, username.
    НЕ трогает message_count, first_message_at, last_message_at, ad_attempts —
    данные об активности, накопленные до или после вступления, сохраняются.

    Вызывается из handlers/members.py при ChatMemberUpdated (JOIN_TRANSITION).
    """
    await conn.execute(
        """
        INSERT INTO users (user_id, username, joined_at, invite_link, added_by)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username    = COALESCE(excluded.username, users.username),
            joined_at   = excluded.joined_at,
            invite_link = excluded.invite_link,
            added_by    = excluded.added_by
        """,
        (user_id, username, joined_at, invite_link, added_by),
    )
    await conn.commit()


# ─── парсер: список участников (раздел 5) ────────────────────────────────────

async def upsert_member(
    conn: aiosqlite.Connection,
    user_id: int,
    username: str | None,
    joined_at: int | None,
) -> None:
    """Записать участника чата из get_chat_members().

    Безопасен для повторного вызова:
    - joined_at: пишется только если у пользователя его ещё нет (COALESCE).
    - username:  обновляется только если новое значение не None.
    - Данные об активности (message_count и др.) не трогаются.
    """
    await conn.execute(
        """
        INSERT INTO users (user_id, username, joined_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username  = COALESCE(excluded.username, users.username),
            joined_at = COALESCE(users.joined_at, excluded.joined_at)
        """,
        (user_id, username, joined_at),
    )


# ─── парсер истории (раздел 5) ────────────────────────────────────────────────

async def save_parsed_stats(
    conn: aiosqlite.Connection,
    user_id: int,
    username: str | None,
    message_count: int,
    first_message_at: int,
    last_message_at: int,
) -> None:
    """Записать агрегированную статистику из истории чата.

    SET-семантика (не инкремент): перезаписывает message_count,
    first_message_at, last_message_at, username.
    ad_attempts НЕ трогается — данные о нарушениях, собранные ботом
    в рантайме, не должны сбрасываться при повторном запуске парсера.

    Вызывается только из parser.py (разовый сид-скрипт, раздел 5 ТЗ).
    Повторный запуск перезапишет агрегированную статистику, но сохранит
    все данные о нарушениях (ad_attempts).

    Примечание: вызывающий код фиксирует транзакцию пакетно
    (один conn.commit() после всего цикла записей).
    """
    await conn.execute(
        """
        INSERT INTO users (user_id, username, message_count,
                           first_message_at, last_message_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username         = COALESCE(excluded.username, users.username),
            message_count    = excluded.message_count,
            first_message_at = excluded.first_message_at,
            last_message_at  = excluded.last_message_at
        """,
        (user_id, username, message_count, first_message_at, last_message_at),
    )


# ─── история входов/выходов ───────────────────────────────────────────────────

async def record_member_event(
    conn: aiosqlite.Connection,
    user_id: int,
    event_type: str,   # 'join' | 'leave'
    happened_at: int,
) -> None:
    """Записать событие входа или выхода участника."""
    await conn.execute(
        "INSERT INTO member_events (user_id, event_type, happened_at) VALUES (?, ?, ?)",
        (user_id, event_type, happened_at),
    )
    await conn.commit()


async def get_member_events(
    conn: aiosqlite.Connection,
    user_id: int,
    limit: int = 50,
) -> list[dict]:
    """Получить историю входов/выходов пользователя (новые сначала)."""
    cursor = await conn.execute(
        """
        SELECT event_type, happened_at
        FROM member_events
        WHERE user_id = ?
        ORDER BY happened_at DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]

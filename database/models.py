"""
DDL-определения таблиц SQLite.

Три таблицы по разделу 4 ТЗ:
  - users          — профиль участника и суммарная статистика
  - messages       — отдельная строка на каждое сообщение (для расчёта свежести)
  - advertisements — нормализованные тексты объявлений (для анти-дубля)

Время хранится как INTEGER (Unix-timestamp UTC) — единообразно во всех таблицах.
Конвертация: datetime.now(timezone.utc).timestamp() → int, и обратно
             datetime.fromtimestamp(ts, tz=timezone.utc).
"""

# ─── SQL CREATE TABLE ─────────────────────────────────────────────────────────

CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER PRIMARY KEY,
    username        TEXT,
    joined_at       INTEGER,            -- UTC unix-ts; NULL если неизвестно
    invite_link     TEXT,               -- NULL в большинстве случаев (см. раздел 9 ТЗ)
    message_count   INTEGER NOT NULL DEFAULT 0,
    first_message_at INTEGER,           -- UTC unix-ts первого замеченного сообщения
    last_message_at  INTEGER,           -- UTC unix-ts последнего сообщения
    ad_attempts     INTEGER NOT NULL DEFAULT 0,
    added_by        INTEGER             -- user_id того, кто добавил; NULL = сам вступил
);
"""

CREATE_MESSAGES = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(user_id),
    created_at  INTEGER NOT NULL    -- UTC unix-ts
);
"""

# Индекс для быстрого подсчёта сообщений в окне свежести (раздел 6.3 ТЗ)
CREATE_MESSAGES_IDX = """
CREATE INDEX IF NOT EXISTS idx_messages_user_created
    ON messages (user_id, created_at);
"""

CREATE_ADVERTISEMENTS = """
CREATE TABLE IF NOT EXISTS advertisements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(user_id),
    normalized_text TEXT NOT NULL,
    text_hash       TEXT NOT NULL,      -- SHA-256 от normalized_text
    created_at      INTEGER NOT NULL    -- UTC unix-ts
);
"""

# Индекс для быстрого поиска хешей конкретного пользователя (раздел 6.4 ТЗ)
CREATE_ADVERTISEMENTS_IDX = """
CREATE INDEX IF NOT EXISTS idx_advertisements_user
    ON advertisements (user_id);
"""

# Удобный кортеж: передавать в db.py при инициализации
ALL_DDL: tuple[str, ...] = (
    CREATE_USERS,
    CREATE_MESSAGES,
    CREATE_MESSAGES_IDX,
    CREATE_ADVERTISEMENTS,
    CREATE_ADVERTISEMENTS_IDX,
)

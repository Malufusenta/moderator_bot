"""
Подключение к SQLite через aiosqlite и инициализация схемы.

Стратегия соединения: одно долгоживущее соединение (_conn), открытое при старте
бота через init_db() и переданное в каждый запрос через get_db().
Это дешевле, чем открывать/закрывать соединение на каждый хендлер,
и безопасно для SQLite (один writer, WAL даёт параллельное чтение).

PRAGMA, включаемые при инициализации (раздел 8 ТЗ):
  - journal_mode=WAL  — снижает «database is locked» под параллельными запросами.
  - busy_timeout=5000 — ждать 5 сек перед падением с SQLITE_BUSY.
  - foreign_keys=ON   — соблюдать REFERENCES (важно для INSERT в messages/advertisements).
"""

import aiosqlite

import config
from database.models import ALL_DDL

# Модульная переменная: одно соединение на весь процесс
_conn: aiosqlite.Connection | None = None


async def init_db() -> None:
    """Открыть соединение, выставить PRAGMA и создать все таблицы.

    Вызывать один раз при старте бота (в bot.py), до регистрации хендлеров.
    После успешного вызова get_db() становится доступным.
    """
    global _conn

    _conn = await aiosqlite.connect(config.DB_PATH)

    # Позволяет обращаться к колонкам по имени: row["user_id"] вместо row[0]
    _conn.row_factory = aiosqlite.Row

    # Настройка производительности и надёжности (раздел 8 ТЗ)
    await _conn.execute("PRAGMA journal_mode=WAL")
    await _conn.execute("PRAGMA busy_timeout=5000")
    await _conn.execute("PRAGMA foreign_keys=ON")
    await _conn.commit()

    # DDL: CREATE TABLE IF NOT EXISTS для всех трёх таблиц и индексов
    for statement in ALL_DDL:
        await _conn.execute(statement)
    await _conn.commit()


async def close_db() -> None:
    """Корректно закрыть соединение при остановке бота.

    Вызывать в on_shutdown хуке Dispatcher (bot.py).
    """
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


def get_db() -> aiosqlite.Connection:
    """Вернуть активное соединение с БД.

    Не контекст-менеджер — соединение долгоживущее и закрывается в close_db().
    Поднимает RuntimeError, если init_db() не был вызван.

    Пример использования в хендлере:
        conn = get_db()
        user = await queries.get_user(conn, user_id)
    """
    if _conn is None:
        raise RuntimeError(
            "БД не инициализирована. Вызовите await init_db() перед использованием get_db()."
        )
    return _conn

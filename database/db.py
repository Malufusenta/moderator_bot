"""
Подключение к SQLite через aiosqlite и инициализация схемы.

Предоставляет:
  - get_db()  — async context manager, возвращающий aiosqlite.Connection
  - init_db() — создаёт все таблицы (вызывается один раз при старте)

# TODO (раздел 2, 4 ТЗ): реализовать get_db() как async contextmanager
#   с aiosqlite.connect(config.DB_PATH), включить WAL-режим для снижения
#   вероятности «database is locked» при конкурентных записях (раздел 8 ТЗ).
# TODO: реализовать init_db() — выполнить все DDL из database.models.ALL_DDL.
"""

import aiosqlite
from contextlib import asynccontextmanager

import config
from database.models import ALL_DDL


@asynccontextmanager
async def get_db():
    """Async context manager для получения соединения с БД.

    # TODO: реализовать — открыть соединение, включить WAL, вернуть conn.
    """
    raise NotImplementedError


async def init_db() -> None:
    """Создать все таблицы, если они ещё не существуют.

    # TODO: реализовать — открыть соединение, выполнить каждый DDL из ALL_DDL,
    #   зафиксировать изменения.
    """
    raise NotImplementedError

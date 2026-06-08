"""
Тесты для parser.py — только агрегатор и queries.save_parsed_stats.
Pyrogram не импортируется и не нужен: используются duck-typed фейки.

Покрываемые сценарии:
  A. aggregate_messages:
     A1. Два юзера — корректные count / first_ts / last_ts
     A2. Сообщения без from_user (служебные) — пропускаются
     A3. Сообщения от бота — пропускаются
     A4. Сообщения без date — пропускаются
     A5. Username берётся из первого встреченного (newest → oldest)

  B. queries.save_parsed_stats (через tmp_db):
     B1. Первая запись — get_user возвращает те же значения
     B2. Повторная запись перезаписывает count, НО сохраняет ad_attempts
     B3. Несколько юзеров в одном пакете
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from database import queries
from parser import aggregate_messages

# ─── Фейковые объекты Pyrogram ────────────────────────────────────────────────

class _FakeUser:
    def __init__(self, uid: int, username: str | None = None, is_bot: bool = False):
        self.id       = uid
        self.username = username
        self.is_bot   = is_bot


class _FakeMsg:
    """Duck-typed Pyrogram Message.

    date принимается как unix-timestamp (int) и конвертируется в aware datetime
    аналогично тому, как Pyrogram возвращает объекты из history.
    """

    def __init__(
        self,
        *,
        user_id: int | None,
        username: str | None = None,
        is_bot: bool = False,
        date_ts: int | None = None,
    ):
        self.from_user = (
            _FakeUser(user_id, username, is_bot) if user_id is not None else None
        )
        self.date = (
            datetime.fromtimestamp(date_ts, tz=timezone.utc) if date_ts is not None else None
        )


# ─── A. aggregate_messages ────────────────────────────────────────────────────

def test_aggregate_two_users():
    """A1: два пользователя — корректные count, first_ts, last_ts."""
    NOW = 1_700_000_000
    msgs = [
        # Alice — 3 сообщения
        _FakeMsg(user_id=1, username="alice", date_ts=NOW),
        _FakeMsg(user_id=1, username="alice", date_ts=NOW - 100),
        _FakeMsg(user_id=1, username="alice", date_ts=NOW - 200),
        # Bob — 2 сообщения
        _FakeMsg(user_id=2, username="bob",   date_ts=NOW - 50),
        _FakeMsg(user_id=2, username="bob",   date_ts=NOW - 150),
    ]
    result = aggregate_messages(msgs)

    assert set(result.keys()) == {1, 2}

    alice = result[1]
    assert alice["count"]    == 3
    assert alice["last_ts"]  == NOW
    assert alice["first_ts"] == NOW - 200

    bob = result[2]
    assert bob["count"]    == 2
    assert bob["last_ts"]  == NOW - 50
    assert bob["first_ts"] == NOW - 150


def test_aggregate_skips_no_from_user():
    """A2: сообщения без from_user (служебные, каналы, анонимные) пропускаются."""
    NOW = 1_700_000_000
    msgs = [
        _FakeMsg(user_id=None, date_ts=NOW),          # сервисное
        _FakeMsg(user_id=None, date_ts=NOW - 100),    # ещё одно
        _FakeMsg(user_id=10,   username="real", date_ts=NOW - 50),
    ]
    result = aggregate_messages(msgs)

    assert list(result.keys()) == [10]
    assert result[10]["count"] == 1


def test_aggregate_skips_bots():
    """A3: сообщения от ботов пропускаются."""
    NOW = 1_700_000_000
    msgs = [
        _FakeMsg(user_id=99, username="botty", is_bot=True,  date_ts=NOW),
        _FakeMsg(user_id=10, username="human", is_bot=False, date_ts=NOW - 10),
    ]
    result = aggregate_messages(msgs)

    assert 99 not in result
    assert 10 in result
    assert result[10]["count"] == 1


def test_aggregate_skips_no_date():
    """A4: сообщения без даты пропускаются."""
    NOW = 1_700_000_000
    msgs = [
        _FakeMsg(user_id=5, username="x", date_ts=None),   # нет даты
        _FakeMsg(user_id=5, username="x", date_ts=NOW),
    ]
    result = aggregate_messages(msgs)

    assert result[5]["count"] == 1  # только второе попало


def test_aggregate_username_from_newest():
    """A5: username берётся из первого встреченного (newest→oldest) = самый свежий.

    История идёт от новых к старым: первое сообщение в итерабле — самое новое.
    Юзер мог сменить username; нам нужен актуальный.
    """
    NOW = 1_700_000_000
    msgs = [
        _FakeMsg(user_id=7, username="new_name", date_ts=NOW),        # новее
        _FakeMsg(user_id=7, username="old_name", date_ts=NOW - 1000), # старее
    ]
    result = aggregate_messages(msgs)

    assert result[7]["username"] == "new_name"


def test_aggregate_empty_input():
    """Пустой итерабл → пустой словарь."""
    assert aggregate_messages([]) == {}


# ─── B. queries.save_parsed_stats ─────────────────────────────────────────────

async def test_save_parsed_stats_basic(tmp_db):
    """B1: первая запись — get_user возвращает те же значения."""
    NOW = 1_700_000_000
    await queries.save_parsed_stats(
        tmp_db,
        user_id          = 101,
        username         = "testuser",
        message_count    = 42,
        first_message_at = NOW - 5_000,
        last_message_at  = NOW,
    )
    await tmp_db.commit()

    row = await queries.get_user(tmp_db, 101)
    assert row is not None
    assert row["message_count"]    == 42
    assert row["first_message_at"] == NOW - 5_000
    assert row["last_message_at"]  == NOW
    assert row["username"]         == "testuser"
    assert row["ad_attempts"]      == 0   # NOT NULL DEFAULT 0


async def test_save_parsed_stats_preserves_ad_attempts(tmp_db):
    """B2: повторная запись перезаписывает count, НО сохраняет ad_attempts.

    Сценарий: бот уже успел выставить ad_attempts=3, потом парсер
    перезапускается. ad_attempts должны остаться 3.
    """
    NOW = 1_700_000_000

    # Первый сид
    await queries.save_parsed_stats(
        tmp_db, 102, "alice", 30, NOW - 1_000, NOW - 100,
    )
    await tmp_db.commit()

    # Имитируем, что бот выставил ad_attempts
    await tmp_db.execute(
        "UPDATE users SET ad_attempts = 3 WHERE user_id = 102"
    )
    await tmp_db.commit()

    # Повторный запуск парсера с другим count
    await queries.save_parsed_stats(
        tmp_db, 102, "alice_new", 75, NOW - 2_000, NOW,
    )
    await tmp_db.commit()

    row = await queries.get_user(tmp_db, 102)
    assert row["message_count"]    == 75        # перезаписано
    assert row["last_message_at"]  == NOW       # перезаписано
    assert row["first_message_at"] == NOW - 2_000  # перезаписано
    assert row["username"]         == "alice_new"   # перезаписано (новее)
    assert row["ad_attempts"]      == 3         # НЕ тронуто


async def test_save_parsed_stats_coalesce_username(tmp_db):
    """B2b: если новый username None, старый сохраняется (COALESCE)."""
    NOW = 1_700_000_000
    await queries.save_parsed_stats(tmp_db, 103, "known", 10, NOW - 500, NOW - 100)
    await tmp_db.commit()

    # Второй прогон — username недоступен
    await queries.save_parsed_stats(tmp_db, 103, None, 20, NOW - 500, NOW)
    await tmp_db.commit()

    row = await queries.get_user(tmp_db, 103)
    assert row["username"]      == "known"   # сохранился через COALESCE
    assert row["message_count"] == 20        # обновился


async def test_save_parsed_stats_batch(tmp_db):
    """B3: несколько пользователей в одном пакете (как делает main())."""
    NOW = 1_700_000_000
    users = {
        201: ("alpha", 100, NOW - 10_000, NOW - 1_000),
        202: ("beta",   50, NOW -  5_000, NOW -   500),
        203: (None,     15, NOW -  2_000, NOW -   200),
    }
    for uid, (uname, cnt, first, last) in users.items():
        await queries.save_parsed_stats(tmp_db, uid, uname, cnt, first, last)
    await tmp_db.commit()

    for uid, (uname, cnt, first, last) in users.items():
        row = await queries.get_user(tmp_db, uid)
        assert row is not None,            f"user {uid} not found"
        assert row["message_count"] == cnt, f"user {uid} count mismatch"

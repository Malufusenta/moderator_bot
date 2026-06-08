"""
Тесты handlers/admin.py: досье, /check, пересылки, безопасность.

Хендлеры вызываются напрямую — без диспетчера aiogram, без сети.
Все ответы перехватываются через FakeMessage.answer() → FakeBot.sent.

Фикстуры:
  tmp_db       — изолированная SQLite во временном файле
  fake_bot     — FakeBot (записывает sent/deleted/restricted)
  no_scheduler — autouse, заглушает schedule_delete
  admin_msg()  — фабрика FakeMessage для ЛС от администратора
"""

import time

import pytest

import config
from database import queries
from handlers.admin import (
    _determine_status,
    _format_dossier,
    handle_check,
    handle_forward,
)
from tests.conftest import DAY, FakeMessage, FakeUser

# ─── Константы тестового окружения ────────────────────────────────────────────

ADMIN_ID   = 9_999  # будет добавлен в config.ADMIN_IDS через monkeypatch
TARGET_UID = 1_001  # пользователь, чьё досье смотрим


# ─── Вспомогательные фабрики ──────────────────────────────────────────────────

def _admin_msg(
    fake_bot,
    *,
    text: str = "",
    monkeypatch,
) -> FakeMessage:
    """FakeMessage от администратора в приватном чате."""
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_ID])
    msg = FakeMessage(
        user_id=ADMIN_ID,
        text=text,
        chat_id=ADMIN_ID,       # личный чат — chat_id == user_id
        chat_type="private",
    )
    msg.bot = fake_bot
    return msg


async def _seed(conn, user_id, message_count, last_active_days_ago, username=None):
    """Вставить пользователя с нужными счётчиками в обход upsert."""
    now = int(time.time())
    last_ts = now - last_active_days_ago * DAY
    first_ts = now - (last_active_days_ago + 10) * DAY
    await conn.execute(
        "INSERT INTO users (user_id, username, message_count, "
        "first_message_at, last_message_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, username, message_count, first_ts, last_ts),
    )
    await conn.commit()


# ─── Тесты статуса (unit, без БД) ─────────────────────────────────────────────

def test_status_trusted():
    """Доверенный: count >= TRUST_LIMIT и last_active в окне."""
    now = int(time.time())
    profile = {
        "message_count": config.TRUST_LIMIT,
        "last_message_at": now - 5 * DAY,
    }
    assert "Доверенный" in _determine_status(profile, now)


def test_status_sleeping():
    """Спящий: count >= TRUST_LIMIT, но last_active вне окна."""
    now = int(time.time())
    profile = {
        "message_count": config.TRUST_LIMIT,
        "last_message_at": now - (config.RECENCY_DAYS + 5) * DAY,
    }
    assert "Спящий" in _determine_status(profile, now)


def test_status_newbie():
    """Новичок: count < TRUST_LIMIT."""
    now = int(time.time())
    profile = {
        "message_count": config.TRUST_LIMIT - 1,
        "last_message_at": now - DAY,
    }
    assert "Новичок" in _determine_status(profile, now)


# ─── Досье: интеграционные тесты с БД ────────────────────────────────────────

async def test_dossier_trusted_status(tmp_db, fake_bot, monkeypatch):
    """T-DB1: досье доверенного → '⚖️ Статус: … Доверенный'."""
    await _seed(tmp_db, TARGET_UID, message_count=40, last_active_days_ago=5, username="alice")
    msg = _admin_msg(fake_bot, text=f"/check {TARGET_UID}", monkeypatch=monkeypatch)

    await handle_check(msg)

    assert len(fake_bot.sent) == 1
    dossier = fake_bot.sent[0]
    assert "⚖️ Статус:" in dossier
    assert "Доверенный" in dossier
    assert "👤" in dossier and "alice" in dossier


async def test_dossier_sleeping_status(tmp_db, fake_bot, monkeypatch):
    """T-DB2: досье спящего → '⚖️ Статус: … Спящий'."""
    await _seed(tmp_db, TARGET_UID, message_count=200, last_active_days_ago=70)
    msg = _admin_msg(fake_bot, text=f"/check {TARGET_UID}", monkeypatch=monkeypatch)

    await handle_check(msg)

    assert len(fake_bot.sent) == 1
    assert "Спящий" in fake_bot.sent[0]


async def test_dossier_newbie_status(tmp_db, fake_bot, monkeypatch):
    """T-DB3: досье новичка → '⚖️ Статус: … Новичок'."""
    await _seed(tmp_db, TARGET_UID, message_count=5, last_active_days_ago=2)
    msg = _admin_msg(fake_bot, text=f"/check {TARGET_UID}", monkeypatch=monkeypatch)

    await handle_check(msg)

    assert len(fake_bot.sent) == 1
    assert "Новичок" in fake_bot.sent[0]


# ─── /check по числовому ID ───────────────────────────────────────────────────

async def test_check_by_numeric_id(tmp_db, fake_bot, monkeypatch):
    """T-CH1: /check <числовой ID> → возвращает досье."""
    await _seed(tmp_db, TARGET_UID, message_count=50, last_active_days_ago=3)
    msg = _admin_msg(fake_bot, text=f"/check {TARGET_UID}", monkeypatch=monkeypatch)

    await handle_check(msg)

    assert len(fake_bot.sent) == 1
    assert f"ID: {TARGET_UID}" in fake_bot.sent[0]


# ─── /check по @username ──────────────────────────────────────────────────────

async def test_check_username_resolves(tmp_db, fake_bot, monkeypatch):
    """T-CH2: /check @alice → резолвится через find_user_id_by_username → досье."""
    await _seed(tmp_db, TARGET_UID, message_count=40, last_active_days_ago=5, username="alice")
    msg = _admin_msg(fake_bot, text="/check @alice", monkeypatch=monkeypatch)

    await handle_check(msg)

    assert len(fake_bot.sent) == 1
    assert "alice" in fake_bot.sent[0] or f"ID: {TARGET_UID}" in fake_bot.sent[0]


async def test_check_username_without_at(tmp_db, fake_bot, monkeypatch):
    """T-CH3: /check alice (без @) → тоже резолвится."""
    await _seed(tmp_db, TARGET_UID, message_count=40, last_active_days_ago=5, username="alice")
    msg = _admin_msg(fake_bot, text="/check alice", monkeypatch=monkeypatch)

    await handle_check(msg)

    assert len(fake_bot.sent) == 1
    assert "alice" in fake_bot.sent[0] or f"ID: {TARGET_UID}" in fake_bot.sent[0]


async def test_check_unknown_username(tmp_db, fake_bot, monkeypatch):
    """T-CH4: /check @ghost → 'Пользователь не найден в базе.'."""
    msg = _admin_msg(fake_bot, text="/check @ghost", monkeypatch=monkeypatch)

    await handle_check(msg)

    assert len(fake_bot.sent) == 1
    assert "не найден" in fake_bot.sent[0].lower()
    assert "ненадёжен" in fake_bot.sent[0]  # предупреждение обязательно


# ─── Безопасность: не-админ молчит ───────────────────────────────────────────

async def test_non_admin_check_silent(tmp_db, fake_bot, monkeypatch):
    """T-SEC1: /check от не-администратора → ни одного ответа."""
    # ADMIN_IDS не включает пользователя
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_ID])
    regular_id = 1_111

    msg = FakeMessage(
        user_id=regular_id,
        text=f"/check {TARGET_UID}",
        chat_id=regular_id,
        chat_type="private",
    )
    msg.bot = fake_bot

    await handle_check(msg)

    assert fake_bot.sent == [], "Не-админ не должен получать ответ от /check"


async def test_non_admin_forward_silent(tmp_db, fake_bot, monkeypatch):
    """T-SEC2: пересланное сообщение от не-администратора → ни одного ответа."""
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_ID])
    regular_id = 1_111

    msg = FakeMessage(
        user_id=regular_id,
        chat_id=regular_id,
        chat_type="private",
    )
    msg.bot = fake_bot
    # Установить forward_from, чтобы _IsForwarded() вернул True
    msg.forward_from = FakeUser(TARGET_UID, "someuser")
    msg.forward_date = int(time.time())

    await handle_forward(msg)

    assert fake_bot.sent == [], "Не-админ не должен получать ответ при пересылке"


# ─── Пересланные сообщения ────────────────────────────────────────────────────

async def test_forward_hidden_sender(tmp_db, fake_bot, monkeypatch):
    """T-FWD1: forward_origin.type == 'hidden_user' → 'Не удалось определить отправителя'."""

    class _HiddenOrigin:
        type = "hidden_user"
        sender_user_name = "SomePrivateUser"

    msg = _admin_msg(fake_bot, monkeypatch=monkeypatch)
    msg.forward_origin = _HiddenOrigin()

    await handle_forward(msg)

    assert len(fake_bot.sent) == 1
    assert "Не удалось определить отправителя" in fake_bot.sent[0]
    assert "приватная" in fake_bot.sent[0]


async def test_forward_known_user(tmp_db, fake_bot, monkeypatch):
    """T-FWD2: forward_origin.type == 'user' → досье исходного отправителя."""
    await _seed(tmp_db, TARGET_UID, message_count=35, last_active_days_ago=10)

    class _UserOrigin:
        type = "user"
        sender_user = FakeUser(TARGET_UID, "target_user")

    msg = _admin_msg(fake_bot, monkeypatch=monkeypatch)
    msg.forward_origin = _UserOrigin()

    await handle_forward(msg)

    assert len(fake_bot.sent) == 1
    assert f"ID: {TARGET_UID}" in fake_bot.sent[0]


async def test_forward_old_api_forward_from(tmp_db, fake_bot, monkeypatch):
    """T-FWD3: старый API (forward_from без forward_origin) → досье."""
    await _seed(tmp_db, TARGET_UID, message_count=35, last_active_days_ago=10)

    msg = _admin_msg(fake_bot, monkeypatch=monkeypatch)
    msg.forward_from = FakeUser(TARGET_UID, "target_user")
    msg.forward_date = int(time.time())
    # forward_origin остаётся None (старый клиент)

    await handle_forward(msg)

    assert len(fake_bot.sent) == 1
    assert f"ID: {TARGET_UID}" in fake_bot.sent[0]


async def test_forward_unknown_user_in_db(tmp_db, fake_bot, monkeypatch):
    """T-FWD4: пользователь из пересылки отсутствует в БД → 'Нет данных'."""

    class _UserOrigin:
        type = "user"
        sender_user = FakeUser(88_888)

    msg = _admin_msg(fake_bot, monkeypatch=monkeypatch)
    msg.forward_origin = _UserOrigin()

    await handle_forward(msg)

    assert len(fake_bot.sent) == 1
    assert "Нет данных по этому пользователю" in fake_bot.sent[0]

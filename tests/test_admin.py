"""
Тесты handlers/admin.py: досье, /check, пересылки, безопасность.

Хендлеры вызываются напрямую — без диспетчера aiogram, без сети.
Все ответы перехватываются через FakeMessage.answer() → FakeBot.sent.
"""

import time

import pytest

import config
from database import queries
from handlers.admin import (
    _format_added_by,
    _format_dossier,
    _seniority_status,
    handle_check,
    handle_forward,
)
from tests.conftest import DAY, FakeMessage, FakeUser

# ─── Константы тестового окружения ────────────────────────────────────────────

ADMIN_ID   = 9_999
TARGET_UID = 1_001


# ─── Вспомогательные фабрики ──────────────────────────────────────────────────

def _admin_msg(fake_bot, *, text: str = "", monkeypatch) -> FakeMessage:
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_ID])
    msg = FakeMessage(
        user_id=ADMIN_ID, text=text,
        chat_id=ADMIN_ID, chat_type="private",
    )
    msg.bot = fake_bot
    return msg


async def _seed(conn, user_id, message_count, last_active_days_ago,
                username=None, joined_days_ago=None):
    """Вставить пользователя с нужными счётчиками в обход upsert."""
    now = int(time.time())
    last_ts  = now - last_active_days_ago * DAY
    first_ts = now - (last_active_days_ago + 10) * DAY
    joined_ts = (now - joined_days_ago * DAY) if joined_days_ago is not None else None
    await conn.execute(
        "INSERT INTO users (user_id, username, message_count, "
        "first_message_at, last_message_at, joined_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, username, message_count, first_ts, last_ts, joined_ts),
    )
    await conn.commit()


def _base_profile(**overrides) -> dict:
    """Минимальный профиль для unit-тестов (без БД)."""
    base = {
        "user_id":           TARGET_UID,
        "username":          "testuser",
        "joined_at":         None,
        "invite_link":       None,
        "message_count":     0,
        "first_message_at":  None,
        "last_message_at":   None,
        "ad_attempts":       0,
        "last_ad_attempt_at": None,
        "added_by":          None,
    }
    base.update(overrides)
    return base


# ─── Статус по стажу (_seniority_status) — unit-тесты ────────────────────────

def test_seniority_newbie_by_joined_at():
    """joined_at недавно (< NEWCOMER_DAYS) → Новичок."""
    now = int(time.time())
    profile = _base_profile(joined_at=now - 5 * DAY)
    assert "Новичок" in _seniority_status(profile, now)


def test_seniority_veteran_by_joined_at():
    """joined_at давно (>= NEWCOMER_DAYS) → Старичок."""
    now = int(time.time())
    profile = _base_profile(joined_at=now - (config.NEWCOMER_DAYS + 5) * DAY)
    assert "Старичок" in _seniority_status(profile, now)


def test_seniority_uses_first_message_at_as_fallback():
    """joined_at = None, first_message_at давно → Старичок (fallback)."""
    now = int(time.time())
    profile = _base_profile(
        joined_at=None,
        first_message_at=now - (config.NEWCOMER_DAYS + 10) * DAY,
    )
    assert "Старичок" in _seniority_status(profile, now)


def test_seniority_no_data():
    """joined_at и first_message_at оба None → 'Нет данных'."""
    now = int(time.time())
    profile = _base_profile(joined_at=None, first_message_at=None)
    assert _seniority_status(profile, now) == "Нет данных"


# ─── _format_added_by — unit-тесты ───────────────────────────────────────────

def test_added_by_with_username():
    profile = _base_profile(added_by=9001)
    result  = _format_added_by(profile, {"user_id": 9001, "username": "boss"})
    assert result == "добавил @boss (ID: 9001)"


def test_added_by_without_username():
    profile = _base_profile(added_by=9002)
    result  = _format_added_by(profile, {"user_id": 9002, "username": None})
    assert result == "добавил ID: 9002"


def test_added_by_added_by_user_none():
    """added_by задан, но пользователь не найден в БД."""
    profile = _base_profile(added_by=9003)
    result  = _format_added_by(profile, None)
    assert result == "добавил ID: 9003"


def test_added_by_known_invite_link(monkeypatch):
    """invite_link есть и распознан через INVITE_LINKS → имя ссылки."""
    monkeypatch.setattr(config, "INVITE_LINKS", {"https://t.me/+abc": "Лобби"})
    profile = _base_profile(added_by=None, invite_link="https://t.me/+abc")
    result  = _format_added_by(profile, None)
    assert result == "Лобби"


def test_added_by_unknown_invite_link(monkeypatch):
    """invite_link есть, но не в INVITE_LINKS → 'по ссылке'."""
    monkeypatch.setattr(config, "INVITE_LINKS", {})
    profile = _base_profile(added_by=None, invite_link="https://t.me/+unknown")
    result  = _format_added_by(profile, None)
    assert result == "по ссылке"


def test_added_by_no_data():
    """added_by=None, invite_link=None → 'Нет данных'."""
    profile = _base_profile(added_by=None, invite_link=None)
    result  = _format_added_by(profile, None)
    assert result == "Нет данных"


# ─── _format_dossier — структура карточки ────────────────────────────────────

def test_dossier_has_required_fields():
    """Карточка содержит все ожидаемые поля."""
    now = int(time.time())
    profile = _base_profile(
        message_count=50,
        first_message_at=now - 40 * DAY,
        last_message_at=now - 3 * DAY,
    )
    text = _format_dossier(profile, now)
    for field in ("👤", "📅", "🔗", "✉️", "⏱", "📆", "⚖️", "✅", "🛑"):
        assert field in text, f"Поле {field!r} отсутствует в досье"


def test_dossier_no_activity_window_line():
    """В карточке НЕТ строки «В окне свежести» (удалена при рефакторинге)."""
    now = int(time.time())
    text = _format_dossier(_base_profile(), now)
    assert "окне свежести" not in text
    assert "В окне" not in text


def test_dossier_trusted_yes():
    """message_count >= TRUST_LIMIT → '✅ Доверенный: Да'."""
    now = int(time.time())
    profile = _base_profile(message_count=config.TRUST_LIMIT)
    text = _format_dossier(profile, now)
    assert "✅ Доверенный: Да" in text


def test_dossier_trusted_no():
    """message_count < TRUST_LIMIT → '✅ Доверенный: Нет'."""
    now = int(time.time())
    profile = _base_profile(message_count=config.TRUST_LIMIT - 1)
    text = _format_dossier(profile, now)
    assert "✅ Доверенный: Нет" in text


def test_dossier_violations_with_date():
    """ad_attempts > 0 и last_ad_attempt_at задан → дата последнего нарушения."""
    now = int(time.time())
    profile = _base_profile(ad_attempts=3, last_ad_attempt_at=now - DAY)
    text = _format_dossier(profile, now)
    assert "🛑 Нарушений: 3, последнее" in text


def test_dossier_violations_without_date():
    """ad_attempts=0 → только число, без даты."""
    now = int(time.time())
    profile = _base_profile(ad_attempts=0, last_ad_attempt_at=None)
    text = _format_dossier(profile, now)
    assert "🛑 Нарушений: 0" in text
    assert "последнее" not in text


# ─── Досье: интеграционные тесты с БД ────────────────────────────────────────

async def test_dossier_veteran_trusted(tmp_db, fake_bot, monkeypatch):
    """Старый участник (first_msg 80 дней назад) + count=200 → Старичок + Доверенный: Да."""
    await _seed(tmp_db, TARGET_UID, message_count=200, last_active_days_ago=70)
    msg = _admin_msg(fake_bot, text=f"/check {TARGET_UID}", monkeypatch=monkeypatch)

    await handle_check(msg)

    assert len(fake_bot.sent) == 1
    dossier = fake_bot.sent[0]
    assert "Старичок" in dossier
    assert "✅ Доверенный: Да" in dossier


async def test_dossier_veteran_not_trusted(tmp_db, fake_bot, monkeypatch):
    """Старый участник (first_msg 80 дней), но мало сообщений → Старичок + Доверенный: Нет."""
    await _seed(tmp_db, TARGET_UID, message_count=5, last_active_days_ago=70)
    msg = _admin_msg(fake_bot, text=f"/check {TARGET_UID}", monkeypatch=monkeypatch)

    await handle_check(msg)

    assert len(fake_bot.sent) == 1
    dossier = fake_bot.sent[0]
    assert "Старичок" in dossier
    assert "✅ Доверенный: Нет" in dossier


async def test_dossier_newbie_status(tmp_db, fake_bot, monkeypatch):
    """Новый участник (first_msg 5 дней назад) → Новичок + Доверенный: Нет."""
    await _seed(tmp_db, TARGET_UID, message_count=5, last_active_days_ago=2)
    msg = _admin_msg(fake_bot, text=f"/check {TARGET_UID}", monkeypatch=monkeypatch)

    await handle_check(msg)

    assert len(fake_bot.sent) == 1
    dossier = fake_bot.sent[0]
    assert "Новичок" in dossier
    assert "✅ Доверенный: Нет" in dossier


# ─── /check по числовому ID ───────────────────────────────────────────────────

async def test_check_by_numeric_id(tmp_db, fake_bot, monkeypatch):
    await _seed(tmp_db, TARGET_UID, message_count=50, last_active_days_ago=3)
    msg = _admin_msg(fake_bot, text=f"/check {TARGET_UID}", monkeypatch=monkeypatch)
    await handle_check(msg)
    assert len(fake_bot.sent) == 1
    assert f"ID: {TARGET_UID}" in fake_bot.sent[0]


async def test_check_username_resolves(tmp_db, fake_bot, monkeypatch):
    await _seed(tmp_db, TARGET_UID, message_count=40, last_active_days_ago=5, username="alice")
    msg = _admin_msg(fake_bot, text="/check @alice", monkeypatch=monkeypatch)
    await handle_check(msg)
    assert len(fake_bot.sent) == 1
    assert "alice" in fake_bot.sent[0] or f"ID: {TARGET_UID}" in fake_bot.sent[0]


async def test_check_username_without_at(tmp_db, fake_bot, monkeypatch):
    await _seed(tmp_db, TARGET_UID, message_count=40, last_active_days_ago=5, username="alice")
    msg = _admin_msg(fake_bot, text="/check alice", monkeypatch=monkeypatch)
    await handle_check(msg)
    assert len(fake_bot.sent) == 1
    assert "alice" in fake_bot.sent[0] or f"ID: {TARGET_UID}" in fake_bot.sent[0]


async def test_check_unknown_username(tmp_db, fake_bot, monkeypatch):
    msg = _admin_msg(fake_bot, text="/check @ghost", monkeypatch=monkeypatch)
    await handle_check(msg)
    assert len(fake_bot.sent) == 1
    assert "не найден" in fake_bot.sent[0].lower()
    assert "ненадёжен" in fake_bot.sent[0]


# ─── Безопасность: не-админ молчит ───────────────────────────────────────────

async def test_non_admin_check_silent(tmp_db, fake_bot, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_ID])
    msg = FakeMessage(user_id=1_111, text=f"/check {TARGET_UID}",
                      chat_id=1_111, chat_type="private")
    msg.bot = fake_bot
    await handle_check(msg)
    assert fake_bot.sent == []


async def test_non_admin_forward_silent(tmp_db, fake_bot, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_ID])
    msg = FakeMessage(user_id=1_111, chat_id=1_111, chat_type="private")
    msg.bot = fake_bot
    msg.forward_from = FakeUser(TARGET_UID, "someuser")
    msg.forward_date = int(time.time())
    await handle_forward(msg)
    assert fake_bot.sent == []


# ─── Пересланные сообщения ────────────────────────────────────────────────────

async def test_forward_hidden_sender(tmp_db, fake_bot, monkeypatch):
    class _HiddenOrigin:
        type = "hidden_user"
        sender_user_name = "SomePrivateUser"

    msg = _admin_msg(fake_bot, monkeypatch=monkeypatch)
    msg.forward_origin = _HiddenOrigin()
    await handle_forward(msg)
    assert len(fake_bot.sent) == 1
    assert "Не удалось определить отправителя" in fake_bot.sent[0]


async def test_forward_known_user(tmp_db, fake_bot, monkeypatch):
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
    await _seed(tmp_db, TARGET_UID, message_count=35, last_active_days_ago=10)
    msg = _admin_msg(fake_bot, monkeypatch=monkeypatch)
    msg.forward_from = FakeUser(TARGET_UID, "target_user")
    msg.forward_date = int(time.time())
    await handle_forward(msg)
    assert len(fake_bot.sent) == 1
    assert f"ID: {TARGET_UID}" in fake_bot.sent[0]


async def test_forward_unknown_user_in_db(tmp_db, fake_bot, monkeypatch):
    class _UserOrigin:
        type = "user"
        sender_user = FakeUser(88_888)

    msg = _admin_msg(fake_bot, monkeypatch=monkeypatch)
    msg.forward_origin = _UserOrigin()
    await handle_forward(msg)
    assert len(fake_bot.sent) == 1
    assert "Нет данных по этому пользователю" in fake_bot.sent[0]

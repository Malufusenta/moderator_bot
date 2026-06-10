"""
Тесты для record_join (database/queries.py) и отображения added_by в досье.
"""

import time

import pytest

from database import queries
from handlers.admin import _format_added_by, _format_dossier, _determine_status
import config

# ─── Вспомогательное ─────────────────────────────────────────────────────────

def _now() -> int:
    return int(time.time())


def _base_profile(**overrides) -> dict:
    """Минимальный профиль для тестирования _format_dossier."""
    base = {
        "user_id":        1001,
        "username":       "testuser",
        "joined_at":      _now() - 86_400 * 10,
        "invite_link":    None,
        "message_count":  0,
        "first_message_at": None,
        "last_message_at":  None,
        "ad_attempts":    0,
        "added_by":       None,
    }
    base.update(overrides)
    return base


# ─── record_join: новая запись ────────────────────────────────────────────────

async def test_record_join_creates_row(tmp_db):
    """record_join должен создать строку в users с правильными полями."""
    now = _now()
    await queries.record_join(
        tmp_db,
        user_id=2001,
        username="alice",
        joined_at=now,
        invite_link="https://t.me/+abc",
        added_by=9001,
    )

    row = await queries.get_user(tmp_db, 2001)
    assert row is not None
    assert row["username"]    == "alice"
    assert row["joined_at"]   == now
    assert row["invite_link"] == "https://t.me/+abc"
    assert row["added_by"]    == 9001
    # Поля активности не должны быть проставлены
    assert row["message_count"]    == 0
    assert row["first_message_at"] is None
    assert row["last_message_at"]  is None
    assert row["ad_attempts"]      == 0


async def test_record_join_self_join(tmp_db):
    """added_by=None — самостоятельное вступление."""
    now = _now()
    await queries.record_join(
        tmp_db,
        user_id=2002,
        username="bob",
        joined_at=now,
        invite_link=None,
        added_by=None,
    )

    row = await queries.get_user(tmp_db, 2002)
    assert row is not None
    assert row["added_by"]    is None
    assert row["invite_link"] is None


# ─── record_join: конфликт (существующий пользователь) ───────────────────────

async def test_record_join_updates_existing_without_touching_activity(tmp_db):
    """record_join поверх существующего юзера не трогает message_count и т.д."""
    # Симулируем пользователя с историей активности
    await queries.upsert_user(tmp_db, 2003, "charlie", _now())
    await queries.upsert_user(tmp_db, 2003, "charlie", _now())

    row_before = await queries.get_user(tmp_db, 2003)
    assert row_before["message_count"] == 2

    # Теперь он «вступил» (re-join или первичная запись события)
    join_ts = _now()
    await queries.record_join(
        tmp_db,
        user_id=2003,
        username="charlie",
        joined_at=join_ts,
        invite_link="https://t.me/+xyz",
        added_by=9002,
    )

    row_after = await queries.get_user(tmp_db, 2003)
    assert row_after["message_count"]    == 2,       "message_count не должен сброситься"
    assert row_after["joined_at"]        == join_ts,  "joined_at должен обновиться"
    assert row_after["invite_link"]      == "https://t.me/+xyz"
    assert row_after["added_by"]         == 9002
    # ad_attempts тоже не трогаем (по умолчанию 0, проверяем что не изменился)
    assert row_after["ad_attempts"]      == 0


async def test_record_join_preserves_username_when_new_is_none(tmp_db):
    """Если username=None в record_join, старое значение сохраняется (COALESCE)."""
    await queries.upsert_user(tmp_db, 2004, "diana", _now())

    await queries.record_join(
        tmp_db,
        user_id=2004,
        username=None,
        joined_at=_now(),
        invite_link=None,
        added_by=None,
    )

    row = await queries.get_user(tmp_db, 2004)
    assert row["username"] == "diana", "username не должен обнулиться через COALESCE"


# ─── get_user_profile содержит added_by ──────────────────────────────────────

async def test_get_user_profile_includes_added_by(tmp_db):
    """get_user_profile должен возвращать поле added_by."""
    now = _now()
    await queries.record_join(
        tmp_db,
        user_id=2005,
        username="eve",
        joined_at=now,
        invite_link=None,
        added_by=7777,
    )

    profile = await queries.get_user_profile(tmp_db, 2005, now - 86_400)
    assert profile is not None
    assert profile["added_by"] == 7777


# ─── _format_added_by: разные сценарии ───────────────────────────────────────

def test_format_added_by_with_username():
    profile = _base_profile(added_by=9001, joined_at=_now())
    added_by_user = {"user_id": 9001, "username": "admin_user"}
    result = _format_added_by(profile, added_by_user)
    assert result == "@admin_user (ID: 9001)"


def test_format_added_by_without_username():
    """added_by известен, но у добавляющего нет username в БД."""
    profile = _base_profile(added_by=9002, joined_at=_now())
    added_by_user = {"user_id": 9002, "username": None}
    result = _format_added_by(profile, added_by_user)
    assert result == "ID: 9002"


def test_format_added_by_added_by_user_none():
    """added_by есть, но get_user вернул None (юзер не в БД)."""
    profile = _base_profile(added_by=9003, joined_at=_now())
    result = _format_added_by(profile, None)
    assert result == "ID: 9003"


def test_format_added_by_self_join():
    """added_by=None, joined_at есть → вступил сам."""
    profile = _base_profile(added_by=None, joined_at=_now())
    result = _format_added_by(profile, None)
    assert result == "вступил сам"


def test_format_added_by_no_data():
    """added_by=None, joined_at=None → нет данных."""
    profile = _base_profile(added_by=None, joined_at=None)
    result = _format_added_by(profile, None)
    assert result == "Нет данных"


# ─── _format_dossier содержит строку «Кто добавил» ───────────────────────────

def test_dossier_contains_added_by_line():
    now = _now()
    profile = _base_profile(added_by=9001, joined_at=now - 86_400 * 5)
    added_by_user = {"user_id": 9001, "username": "recruiter"}
    text = _format_dossier(profile, now, added_by_user)
    assert "👥 Кто добавил: @recruiter (ID: 9001)" in text


def test_dossier_self_join_line():
    now = _now()
    profile = _base_profile(added_by=None, joined_at=now - 86_400)
    text = _format_dossier(profile, now, None)
    assert "👥 Кто добавил: вступил сам" in text


def test_dossier_no_join_data_line():
    now = _now()
    profile = _base_profile(added_by=None, joined_at=None)
    text = _format_dossier(profile, now, None)
    assert "👥 Кто добавил: Нет данных" in text

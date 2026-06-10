"""
Тесты для record_join (database/queries.py) и отображения added_by в досье.
"""

import time

import pytest

import config
from database import queries
from handlers.admin import _format_added_by, _format_dossier

# ─── Вспомогательное ─────────────────────────────────────────────────────────

def _now() -> int:
    return int(time.time())


def _base_profile(**overrides) -> dict:
    base = {
        "user_id":           1001,
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


# ─── record_join: новая запись ────────────────────────────────────────────────

async def test_record_join_creates_row(tmp_db):
    now = _now()
    await queries.record_join(
        tmp_db, user_id=2001, username="alice",
        joined_at=now, invite_link="https://t.me/+abc", added_by=9001,
    )
    row = await queries.get_user(tmp_db, 2001)
    assert row is not None
    assert row["username"]    == "alice"
    assert row["joined_at"]   == now
    assert row["invite_link"] == "https://t.me/+abc"
    assert row["added_by"]    == 9001
    assert row["message_count"]    == 0
    assert row["first_message_at"] is None
    assert row["last_message_at"]  is None
    assert row["ad_attempts"]      == 0


async def test_record_join_self_join(tmp_db):
    now = _now()
    await queries.record_join(
        tmp_db, user_id=2002, username="bob",
        joined_at=now, invite_link=None, added_by=None,
    )
    row = await queries.get_user(tmp_db, 2002)
    assert row["added_by"]    is None
    assert row["invite_link"] is None


# ─── record_join: конфликт (существующий пользователь) ───────────────────────

async def test_record_join_updates_existing_without_touching_activity(tmp_db):
    await queries.upsert_user(tmp_db, 2003, "charlie", _now())
    await queries.upsert_user(tmp_db, 2003, "charlie", _now())
    assert (await queries.get_user(tmp_db, 2003))["message_count"] == 2

    join_ts = _now()
    await queries.record_join(
        tmp_db, user_id=2003, username="charlie",
        joined_at=join_ts, invite_link="https://t.me/+xyz", added_by=9002,
    )

    row = await queries.get_user(tmp_db, 2003)
    assert row["message_count"] == 2,                     "message_count не должен сброситься"
    assert row["joined_at"]     == join_ts
    assert row["invite_link"]   == "https://t.me/+xyz"
    assert row["added_by"]      == 9002
    assert row["ad_attempts"]   == 0


async def test_record_join_preserves_username_when_new_is_none(tmp_db):
    await queries.upsert_user(tmp_db, 2004, "diana", _now())
    await queries.record_join(
        tmp_db, user_id=2004, username=None, joined_at=_now(),
        invite_link=None, added_by=None,
    )
    assert (await queries.get_user(tmp_db, 2004))["username"] == "diana"


# ─── get_user_profile содержит added_by ──────────────────────────────────────

async def test_get_user_profile_includes_added_by(tmp_db):
    now = _now()
    await queries.record_join(
        tmp_db, user_id=2005, username="eve",
        joined_at=now, invite_link=None, added_by=7777,
    )
    profile = await queries.get_user_profile(tmp_db, 2005, now - 86_400)
    assert profile is not None
    assert profile["added_by"] == 7777


# ─── _format_added_by: разные сценарии ───────────────────────────────────────

def test_format_added_by_with_username():
    profile = _base_profile(added_by=9001)
    result  = _format_added_by(profile, {"user_id": 9001, "username": "admin_user"})
    assert result == "добавил @admin_user (ID: 9001)"


def test_format_added_by_without_username():
    profile = _base_profile(added_by=9002)
    result  = _format_added_by(profile, {"user_id": 9002, "username": None})
    assert result == "добавил ID: 9002"


def test_format_added_by_added_by_user_none():
    """added_by есть, но get_user вернул None."""
    profile = _base_profile(added_by=9003)
    result  = _format_added_by(profile, None)
    assert result == "добавил ID: 9003"


def test_format_added_by_known_invite_link(monkeypatch):
    monkeypatch.setattr(config, "INVITE_LINKS", {"https://t.me/+abc": "Лобби"})
    profile = _base_profile(added_by=None, invite_link="https://t.me/+abc")
    assert _format_added_by(profile, None) == "по ссылке «Лобби»"


def test_format_added_by_unknown_invite_link(monkeypatch):
    monkeypatch.setattr(config, "INVITE_LINKS", {})
    profile = _base_profile(added_by=None, invite_link="https://t.me/+unknown")
    assert _format_added_by(profile, None) == "по ссылке"


def test_format_added_by_no_data():
    profile = _base_profile(added_by=None, invite_link=None)
    assert _format_added_by(profile, None) == "Нет данных"


# ─── _format_dossier содержит строку «Добавил / вступил сам» ─────────────────

def test_dossier_contains_added_by_line():
    now = _now()
    profile = _base_profile(added_by=9001)
    text = _format_dossier(profile, now, {"user_id": 9001, "username": "recruiter"})
    assert "🔗 Добавил / вступил сам: добавил @recruiter (ID: 9001)" in text


def test_dossier_invite_link_line(monkeypatch):
    monkeypatch.setattr(config, "INVITE_LINKS", {"https://t.me/+abc": "Лобби"})
    now = _now()
    profile = _base_profile(added_by=None, invite_link="https://t.me/+abc")
    text = _format_dossier(profile, now)
    assert "🔗 Добавил / вступил сам: по ссылке «Лобби»" in text


def test_dossier_no_join_data_line():
    now = _now()
    profile = _base_profile(added_by=None, invite_link=None)
    text = _format_dossier(profile, now)
    assert "🔗 Добавил / вступил сам: Нет данных" in text

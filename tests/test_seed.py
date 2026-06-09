"""
Тесты seed.py: проверяем, что засеянные состояния корректно читаются
гейтом доверия (moderation/trust.py).

Это интеграционный тест: seed → БД → is_trusted.
Заодно подтверждает, что «спящее» состояние реально блокируется гейтом,
а «доверенное» — проходит.
"""

import time

import pytest

import config
from moderation import trust
from seed import cmd_dormant, cmd_newbie, cmd_trusted

# ─── Вспомогательное ─────────────────────────────────────────────────────────

def _now() -> int:
    return int(time.time())


# ─── Тесты ───────────────────────────────────────────────────────────────────

async def test_seed_trusted_passes_gate(tmp_db):
    """seed trusted → is_trusted == True."""
    uid = 5_001
    await cmd_trusted(uid, "alice")

    result = await trust.is_trusted(uid, _now())
    assert result is True, (
        "После seed trusted гейт должен пропускать пользователя"
    )


async def test_seed_dormant_blocked_by_gate(tmp_db):
    """seed dormant → is_trusted == False (спящий не проходит гейт)."""
    uid = 5_002
    await cmd_dormant(uid, "sleeper")

    result = await trust.is_trusted(uid, _now())
    assert result is False, (
        "После seed dormant гейт должен блокировать пользователя: "
        f"RECENCY_DAYS={config.RECENCY_DAYS}, "
        f"last_active гарантированно вне окна"
    )


async def test_seed_newbie_blocked_by_gate(tmp_db):
    """seed newbie → is_trusted == False (count < TRUST_LIMIT)."""
    uid = 5_003
    await cmd_newbie(uid, "fresh")

    result = await trust.is_trusted(uid, _now())
    assert result is False, (
        "После seed newbie гейт должен блокировать: "
        f"message_count=1 < TRUST_LIMIT={config.TRUST_LIMIT}"
    )


async def test_dormant_has_high_count_but_stale(tmp_db):
    """Дополнительная проверка: у спящего count >= TRUST_LIMIT, но last_active вне окна.

    Подтверждает, что тест test_seed_dormant_blocked_by_gate отклоняет
    именно по свежести, а не по нехватке count.
    """
    from database import queries

    uid = 5_004
    await cmd_dormant(uid, "dormant_detail")

    row = await queries.get_user(tmp_db, uid)
    assert row is not None
    assert row["message_count"] >= config.TRUST_LIMIT, \
        "Спящий должен иметь count >= TRUST_LIMIT"

    cutoff = _now() - config.RECENCY_DAYS * 86_400
    assert row["last_message_at"] < cutoff, \
        "last_message_at спящего должен быть за пределами окна свежести"

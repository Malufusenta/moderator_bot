"""
Тесты сборщика альбомов (handlers/messages.py).

Проверяет поведение on_message / _flush_album при поступлении сообщений
с одинаковым media_group_id. handle_post здесь монкейпатчится — нам важен
только факт, что сборщик правильно агрегировал и передал список.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import handlers.messages as msg_module
from handlers.messages import ALBUM_DEBOUNCE, _flush_album
from tests.conftest import FakeMessage


# ─── T1: три сообщения с одним media_group_id → handle_post получает все три ──

async def test_album_collects_all_parts(tmp_db, fake_bot, reset_albums, monkeypatch):
    """T1: три части альбома обрабатываются как единый пост.

    Симулируем on_message для каждой части (без реального диспетчера aiogram).
    После ожидания дебаунса _flush_album вызывает handle_post со всеми тремя
    сообщениями — проверяем это через монкейпатч.
    """
    mgid = "grp_collect"
    msgs = [
        FakeMessage(user_id=2001, photo=True, caption="Продам мебель",
                    message_id=101, media_group_id=mgid),
        FakeMessage(user_id=2001, photo=True, message_id=102, media_group_id=mgid),
        FakeMessage(user_id=2001, photo=True, message_id=103, media_group_id=mgid),
    ]

    captured: list = []

    async def fake_handle_post(messages, bot):
        captured.append(list(messages))

    monkeypatch.setattr(msg_module, "handle_post", fake_handle_post)

    # Имитируем поступление сообщений через on_message
    for m in msgs:
        m.bot = fake_bot  # aiogram прокидывает бот через middleware
        await msg_module.on_message(m, fake_bot)

    # Ждём немного дольше дебаунса
    await asyncio.sleep(ALBUM_DEBOUNCE + 0.1)

    assert len(captured) == 1, "handle_post должен быть вызван ровно один раз"
    collected_ids = sorted(m.message_id for m in captured[0])
    assert collected_ids == [101, 102, 103], "Все три части должны быть переданы вместе"


# ─── T2: новое сообщение до дебаунса → таймер перезапускается, все части собраны

async def test_album_debounce_resets_on_new_message(tmp_db, fake_bot, reset_albums, monkeypatch):
    """T2: поздно пришедшее сообщение сбрасывает таймер.

    Три сообщения: первые два приходят сразу, третье — после половины
    дебаунс-интервала. Сборщик должен дождаться третьего и только после
    полного ALBUM_DEBOUNCE без новых сообщений вызвать handle_post.

    Без сброса таймера первые два были бы обработаны отдельно.
    """
    mgid = "grp_debounce"
    m1 = FakeMessage(user_id=2002, photo=True, caption="Сдам гараж",
                     message_id=201, media_group_id=mgid)
    m2 = FakeMessage(user_id=2002, photo=True, message_id=202, media_group_id=mgid)
    m3 = FakeMessage(user_id=2002, photo=True, message_id=203, media_group_id=mgid)

    captured: list = []

    async def fake_handle_post(messages, bot):
        captured.append(list(messages))

    monkeypatch.setattr(msg_module, "handle_post", fake_handle_post)

    # m1 и m2 приходят немедленно
    await msg_module.on_message(m1, fake_bot)
    await msg_module.on_message(m2, fake_bot)

    # m3 приходит через половину интервала дебаунса
    await asyncio.sleep(ALBUM_DEBOUNCE / 2)
    await msg_module.on_message(m3, fake_bot)

    # До истечения полного дебаунса после m3 — ничего не обработано
    assert captured == [], "handle_post не должен быть вызван до окончания дебаунса"

    # Ждём завершения дебаунса
    await asyncio.sleep(ALBUM_DEBOUNCE + 0.1)

    assert len(captured) == 1, "handle_post должен быть вызван ровно один раз"
    collected_ids = sorted(m.message_id for m in captured[0])
    assert collected_ids == [201, 202, 203], "Все три части должны быть собраны вместе"

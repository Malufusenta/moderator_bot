"""
Синглтон Pyrogram-клиента для user-сессии.

Используется командой /history для поиска сообщений в чате.
Сессия та же что у parser.py (parser.session).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_client = None


async def start_pyrogram() -> None:
    """Запустить Pyrogram клиент. Вызывается при старте бота."""
    global _client
    try:
        from pyrogram import Client
        import config

        _client = Client(
            name="parser",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
        )
        await _client.start()
        logger.info("Pyrogram клиент запущен (user-сессия: parser.session)")
    except Exception as exc:
        logger.warning("Pyrogram не удалось запустить: %s — /history недоступен", exc)
        _client = None


async def stop_pyrogram() -> None:
    """Остановить Pyrogram клиент. Вызывается при остановке бота."""
    global _client
    if _client is not None:
        try:
            await _client.stop()
            logger.info("Pyrogram клиент остановлен")
        except Exception as exc:
            logger.warning("Pyrogram stop: %s", exc)
        _client = None


def get_pyrogram():
    """Вернуть активный клиент или None если не запущен."""
    return _client

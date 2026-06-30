"""
Отправка лога модераторских действий в отдельный чат/канал.

Если config.LOG_CHAT_ID == 0 (дефолт) — функция ничего не делает.
Сбой отправки логируется на WARNING и НЕ прерывает модерацию.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.enums import ParseMode

import config

logger = logging.getLogger(__name__)


async def log_action(bot: Bot, text: str) -> None:
    """Отправить текст лога в чат config.LOG_CHAT_ID (HTML parse_mode)."""
    if not config.LOG_CHAT_ID:
        return
    try:
        await bot.send_message(config.LOG_CHAT_ID, text, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.warning(
            "admin_log: не удалось отправить лог в чат %d: %s",
            config.LOG_CHAT_ID, exc,
        )

"""
Примитивы модераторских действий: мут участника и отправка предупреждения в чат.

Оба хелпера используются в handlers/messages.py при обнаружении нарушения.
Вынесены отдельно, чтобы не дублировать try/except и логику автоудаления
в двух ветках (недоверенный + дубль).

Подводные камни (раздел 8 ТЗ):
  - restrict_chat_member требует, чтобы бот был администратором с правом ограничений.
  - Если нарушитель сам является администратором чата, вызов вернёт ошибку.
  - until_date ОБЯЗАН быть timezone-aware UTC; наивный datetime приведёт к
    некорректному времени мута (квирк Telegram: мут < 30 сек = навсегда).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ChatPermissions

from utils import scheduler

logger = logging.getLogger(__name__)


async def mute_user(
    bot: Bot,
    chat_id: int,
    user_id: int,
    hours: int,
) -> bool:
    """Замутить участника на hours часов через restrict_chat_member.

    Устанавливает ChatPermissions(can_send_messages=False) с until_date =
    datetime.now(timezone.utc) + timedelta(hours=hours).

    Возвращает True при успехе, False при любой ошибке Telegram API:
      - TelegramForbiddenError: бот не является администратором или не имеет
        права restrict_members.
      - TelegramBadRequest: нарушитель сам является администратором чата —
        ограничить администраторов невозможно (раздел 8 ТЗ).

    В обоих случаях логируем предупреждение и продолжаем работу — бот не должен
    падать из-за того, что не смог замутить конкретного пользователя.
    """
    until_date = datetime.now(timezone.utc) + timedelta(hours=hours)
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until_date,
        )
        logger.info("mute_user: user %d замьючен в чате %d на %d ч.", user_id, chat_id, hours)
        return True
    except TelegramForbiddenError as exc:
        logger.warning(
            "mute_user: нет прав ограничить user %d в чате %d (бот не админ?): %s",
            user_id, chat_id, exc,
        )
    except TelegramBadRequest as exc:
        logger.warning(
            "mute_user: не удалось замутить user %d в чате %d (возможно, администратор): %s",
            user_id, chat_id, exc,
        )
    return False


async def warn(
    bot: Bot,
    chat_id: int,
    text: str,
    delete_after: int,
) -> None:
    """Отправить предупреждение в чат и запланировать его автоудаление.

    Сообщение отправляется в чат (не в ЛС — раздел 8 ТЗ: бот не может написать
    в личку тому, кто не начинал с ним диалог). Удаляется через delete_after секунд
    через scheduler.schedule_delete, не блокируя хендлер.

    Ошибки при отправке (бот без прав, замьючен в чате) логируются и
    поглощаются — мут уже выполнен, отсутствие предупреждения некритично.
    """
    try:
        msg = await bot.send_message(chat_id=chat_id, text=text)
        scheduler.schedule_delete(bot, chat_id, msg.message_id, delete_after)
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.warning("warn: не удалось отправить предупреждение в чат %d: %s", chat_id, exc)

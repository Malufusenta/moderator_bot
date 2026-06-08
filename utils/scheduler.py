"""
Отложенное удаление сообщений через asyncio.create_task (раздел 8 ТЗ).

Используется для автоудаления предупреждений бота из чата спустя
WARNING_DELETE_SECONDS секунд после отправки (разделы 6.3, 6.4).

Примечание (раздел 8 ТЗ):
  При рестарте бота все незавершённые задачи теряются — это допустимо:
  предупреждения просто останутся в чате чуть дольше, чем планировалось.

Хранение ссылок на задачи:
  asyncio может собрать сборщиком мусора Task, на который никто не держит
  ссылку, ещё до завершения корутины. Модульный set _pending гарантирует,
  что задача живёт до своего завершения. done_callback убирает её из set
  сразу после выполнения, не допуская утечки памяти при долгой работе бота.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

logger = logging.getLogger(__name__)

# Держим ссылки на все живые задачи удаления, чтобы GC не собрал их раньше времени
_pending: set[asyncio.Task] = set()


def schedule_delete(bot: Bot, chat_id: int, message_id: int, delay: int) -> None:
    """Запланировать удаление сообщения через delay секунд (неблокирующее).

    Создаёт asyncio.Task и немедленно возвращается — хендлер не ждёт.
    Задача: sleep(delay) → delete_message. Исключения при удалении поглощаются
    (сообщение могло быть удалено вручную, или бот потерял права).

    Аргументы:
        bot        — экземпляр Bot для вызова API.
        chat_id    — ID чата, из которого удалять.
        message_id — ID сообщения для удаления.
        delay      — задержка в секундах (обычно WARNING_DELETE_SECONDS).
    """
    task = asyncio.create_task(
        _delete_after(bot, chat_id, message_id, delay),
        name=f"delete_{chat_id}_{message_id}",
    )
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def _delete_after(bot: Bot, chat_id: int, message_id: int, delay: int) -> None:
    """Внутренняя корутина: ждём delay секунд, затем удаляем сообщение."""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        # Сообщение уже удалено, или бот лишился прав — некритично
        logger.debug("schedule_delete: не удалось удалить %d в %d: %s", message_id, chat_id, exc)
    except Exception as exc:
        logger.warning("schedule_delete: неожиданная ошибка при удалении %d: %s", message_id, exc)

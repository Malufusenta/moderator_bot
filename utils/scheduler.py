"""
Отложенное удаление сообщений через asyncio.create_task.

Используется для автоудаления предупреждений бота из чата спустя
WARNING_DELETE_SECONDS секунд (раздел 6.3, 6.4 и 8 ТЗ).

Примечание из раздела 8 ТЗ:
  При рестарте бота незавершённые задачи теряются — это допустимо.
  Не использовать блокирующий asyncio.sleep внутри хендлеров;
  всегда оборачивать в asyncio.create_task.

# TODO (раздел 8 ТЗ): реализовать schedule_delete(bot, chat_id, message_id, delay).
#   asyncio.create_task(asyncio.sleep(delay) + bot.delete_message(...)).
#   Обработать исключение на случай, если сообщение уже удалено.
"""

from __future__ import annotations

import asyncio

from aiogram import Bot


def schedule_delete(bot: Bot, chat_id: int, message_id: int, delay: int) -> None:
    """Запланировать удаление сообщения через delay секунд (неблокирующее).

    # TODO (раздел 8): создать asyncio.create_task с корутиной,
    #   которая ждёт delay сек, затем вызывает bot.delete_message.
    #   Поглотить TelegramBadRequest, если сообщение уже удалено.
    """
    raise NotImplementedError

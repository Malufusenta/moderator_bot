"""
Точка входа: запуск aiogram-бота и регистрация роутеров.

Порядок старта:
  1. Инициализировать БД (database.db.init_db).
  2. Создать Bot и Dispatcher.
  3. Подключить роутеры: handlers.messages.router, handlers.admin.router.
  4. Запустить polling.

# TODO (раздел 6 ТЗ): подключить роутеры после их реализации.
# TODO (раздел 8 ТЗ): обработать сигналы SIGINT/SIGTERM для graceful shutdown.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import config
from database.db import init_db, close_db
from handlers.messages import router as messages_router
from handlers.admin import router as admin_router
from handlers.members import router as members_router

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    await init_db()

    bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
    dp = Dispatcher()

    dp.include_router(messages_router)
    dp.include_router(admin_router)
    dp.include_router(members_router)

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        # Корректно закрыть соединение с БД при остановке (раздел 8 ТЗ)
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

"""
Разовый сборщик истории чата через Pyrogram (user-сессия).

Запускается один раз перед стартом бота, чтобы заполнить БД
историческими данными (message_count, first/last_message_at).

Логика по разделу 5 ТЗ:
  1. Подключиться user-сессией (API_ID / API_HASH).
  2. Итерировать историю чата CHAT_ID.
  3. На каждое сообщение: upsert_user, increment_message_count, add_message_row.
  4. Делать паузы между запросами (флуд-лимит Telegram).
  5. invite_link ретроспективно не восстанавливается — оставлять NULL.

Подводные камни (раздел 8 ТЗ):
  - Это разовая операция, спешить некуда: паузы обязательны.

# TODO (раздел 5 ТЗ): реализовать main() с Pyrogram Client и iter_messages.
#   Обернуть в asyncio.run(main()), добавить прогресс-лог.
"""

import asyncio


async def main() -> None:
    """Запустить парсинг истории чата и наполнение БД.

    # TODO (раздел 5): подключить Pyrogram Client(session, api_id, api_hash),
    #   итерировать client.get_chat_history(CHAT_ID), для каждого сообщения
    #   вызвать database.queries.*,  делать asyncio.sleep между пачками.
    """
    raise NotImplementedError


if __name__ == "__main__":
    asyncio.run(main())

"""
Обработчик входящих сообщений группового чата.

Ответственность:
  1. Учёт каждого сообщения: upsert_user, increment_message_count, add_message_row.
  2. Если сообщение — объявление (detector.is_advertisement):
       a. Проверить гейт доверия (trust.is_trusted).
       b. Недоверенный → удалить сообщение, мут, предупреждение в чат.
       c. Доверенный → проверить дубли (duplicates.check_duplicate).
          Дубль → удалить, мут, предупреждение; иначе → сохранить.
  3. Обрабатывать альбомы (media_group_id) как единое объявление.
  4. Слушать также edited_message.

Подводные камни (раздел 8 ТЗ):
  - restrict_chat_member требует прав администратора у бота.
  - Нельзя мутить администраторов чата — обрабатывать исключение, не падать.
  - until_date строить от datetime.now(timezone.utc), а не от наивного datetime.now().

# TODO (раздел 6 ТЗ): реализовать router и все обработчики.
#   Зарегистрировать router в bot.py.
"""

from __future__ import annotations

from aiogram import Router

router = Router()

# TODO (раздел 6.1): @router.message() — учёт каждого сообщения.
# TODO (раздел 6.2–6.4): модерация объявлений (детект → доверие → дубли → меры).
# TODO (раздел 6.2): @router.edited_message() — обработка редактирования.
# TODO (раздел 6.2): дедупликация альбомов по media_group_id.

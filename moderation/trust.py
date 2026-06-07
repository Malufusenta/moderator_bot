"""
Гейт доверия: решает, разрешено ли доверенному пользователю публиковать объявления.

Логика по разделу 6.3 ТЗ:
  1. Вайтлист: если user_id в WHITELIST_IDS — пропустить без проверок.
  2. Доверенный = message_count >= TRUST_LIMIT  И
                  сообщений в messages за RECENCY_DAYS дней >= RECENCY_MIN_MESSAGES.
     Оба условия должны выполняться одновременно.
     Свежесть считается БЕЗ учёта текущего сообщения (оно ещё не записано на этом шаге).

Примечание из ТЗ: TRUST_LIMIT и порог свежести — разные числа.
Порог свежести низкий (1 сообщение за 60 дней), иначе публиковать смогут
только пара самых болтливых участников домового чата.

# TODO (раздел 6.3 ТЗ): реализовать is_trusted(user_id, conn) -> bool.
#   Загрузить запись users, посчитать get_recent_message_count из queries,
#   сравнить с TRUST_LIMIT и RECENCY_MIN_MESSAGES.
"""

from __future__ import annotations

import aiosqlite


async def is_trusted(user_id: int, conn: aiosqlite.Connection) -> bool:
    """Вернуть True, если пользователь прошёл гейт доверия.

    # TODO (раздел 6.3): проверить вайтлист, затем message_count и свежесть.
    """
    raise NotImplementedError

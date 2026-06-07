"""
Анти-дубль для доверенных пользователей.

Логика по разделу 6.4 ТЗ:
  1. Нормализовать текст текущего объявления (нижний регистр, без эмодзи/пунктуации).
  2. Быстрая проверка: сравнить text_hash с прошлыми хешами пользователя —
     точный повтор ловится мгновенно, без difflib.
  3. Если точного совпадения нет — сравнить нормализованный текст с прошлыми
     объявлениями через difflib.SequenceMatcher.
  4. Если max similarity > SIMILARITY_THRESHOLD — объявление считается дублем.
  5. Если дубля нет — сохранить normalized_text и text_hash в advertisements.

# TODO (раздел 6.4 ТЗ): реализовать check_duplicate(user_id, text, conn) -> bool.
#   Использовать hashlib.sha256 для text_hash, difflib.SequenceMatcher для fuzzy.
# TODO: реализовать save_if_new(user_id, normalized, hash, conn) -> None.
"""

from __future__ import annotations

import aiosqlite


async def check_duplicate(
    user_id: int,
    normalized_text: str,
    conn: aiosqlite.Connection,
) -> bool:
    """Вернуть True, если normalized_text является дублем прошлого объявления пользователя.

    # TODO (раздел 6.4): загрузить get_user_ads, сравнить хеш, затем difflib.
    """
    raise NotImplementedError


async def save_if_new(
    user_id: int,
    normalized_text: str,
    conn: aiosqlite.Connection,
) -> None:
    """Сохранить объявление в advertisements (вызывается только при отсутствии дубля).

    # TODO (раздел 6.4): посчитать sha256, вызвать queries.save_advertisement.
    """
    raise NotImplementedError

"""
Разовый сборщик истории чата через Pyrogram (раздел 5 ТЗ).

⚠️  ВАЖНО — прочитайте перед запуском:

  1. Запускать ОДИН РАЗ на свежей БД, ДО старта бота.
     Повторный запуск перезапишет message_count / first_message_at /
     last_message_at (ad_attempts и данные о нарушениях сохранятся).

  2. Аккаунт должен быть участником чата config.CHAT_ID —
     иначе история недоступна.

  3. При первом запуске Pyrogram запросит номер телефона и код
     подтверждения; сессия сохраняется в «parser.session» и при
     последующих запусках аутентификация не повторяется.

  4. joined_at и invite_link ретроспективно не восстанавливаются →
     остаются NULL («Нет данных» в досье, раздел 8 ТЗ).

Запуск:
    python parser.py

Что делает скрипт:
  - Итерирует всю историю чата config.CHAT_ID (newest → oldest).
  - Агрегирует message_count, first_message_at, last_message_at
    для каждого живого участника.
  - Записывает результаты одним пакетом через queries.save_parsed_stats.
  - Прогресс печатается каждые 1000 сообщений.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable

import config
from database.db import close_db, get_db, init_db
from database import queries

logger = logging.getLogger(__name__)

# Имя файла сессии Pyrogram (без расширения).
# Создаётся в рабочей директории: parser.session
SESSION_NAME = "parser"

# Шаг прогресс-лога (количество сообщений)
_PROGRESS_STEP = 1_000

# Пауза (сек) на каждый шаг прогресса — щадим лимиты Telegram
_THROTTLE_SLEEP = 0.3


# ─── Чистый агрегатор (тестируется без Pyrogram) ─────────────────────────────

def aggregate_messages(messages: Iterable) -> dict[int, dict]:
    """Агрегировать сообщения по пользователям.

    Принимает любой синхронный итерабл объектов с полями:
      .from_user  (.id, .username, .is_bot) — автор; None для служебных
      .date       — aware datetime UTC (Pyrogram) или None

    Правила фильтрации:
      - from_user is None  → пропустить (сервисное/анонимное/канал)
      - from_user.is_bot   → пропустить
      - date is None       → пропустить

    Сбор данных:
      - username берётся из ПЕРВОГО встреченного сообщения; при обходе
        истории newest→oldest это самый свежий known username.
      - first_ts = min(date), last_ts = max(date) по всем сообщениям юзера.

    Возвращает:
      {user_id: {"username": str|None, "count": int,
                 "first_ts": int, "last_ts": int}}
    """
    stats: dict[int, dict] = {}

    for msg in messages:
        # ── Фильтрация ─────────────────────────────────────────────────────
        if msg.from_user is None:
            continue
        if msg.from_user.is_bot:
            continue
        if msg.date is None:
            continue

        uid: int = msg.from_user.id
        # Pyrogram 2.x: date — aware datetime → unix timestamp
        ts: int = int(msg.date.timestamp())

        if uid not in stats:
            # Первое сообщение от этого юзера = самое свежее (newest→oldest)
            stats[uid] = {
                "username": msg.from_user.username,
                "count":    1,
                "first_ts": ts,
                "last_ts":  ts,
            }
        else:
            entry = stats[uid]
            entry["count"] += 1
            if ts < entry["first_ts"]:
                entry["first_ts"] = ts
            if ts > entry["last_ts"]:
                entry["last_ts"] = ts

    return stats


# ─── Точка входа ─────────────────────────────────────────────────────────────

async def main() -> None:
    """Сбор истории чата и наполнение БД.

    Этапы:
      1. init_db() — создаёт таблицы если нет (idempotent).
      2. Подключиться user-сессией через Pyrogram.
      3. Итерировать всю историю чата с прогресс-логом и throttle-паузами.
         sleep_threshold=60: Pyrogram сам ждёт FloodWait ≤ 60 сек;
         явный except FloodWait нужен только для задержек > 60 сек.
      4. aggregate_messages() — чистая агрегация в памяти.
      5. Записать результаты пакетом в БД; один commit на весь пакет.
      6. close_db() в finally.
    """
    # Pyrogram импортируется здесь — тесты не зависят от него вообще
    from pyrogram import Client
    from pyrogram.errors import FloodWait

    await init_db()
    conn = get_db()

    print(
        f"[parser] Старт. Чат: {config.CHAT_ID}\n"
        f"[parser] Файл сессии: {SESSION_NAME}.session\n"
        "[parser] При первом запуске Pyrogram запросит номер телефона.\n"
    )

    try:
        async with Client(
            name=SESSION_NAME,
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            sleep_threshold=60,
        ) as app:
            batch: list = []
            total = 0

            print("[parser] Подключено. Начинаем сбор истории...")

            try:
                async for msg in app.get_chat_history(config.CHAT_ID):
                    batch.append(msg)
                    total += 1
                    if total % _PROGRESS_STEP == 0:
                        print(f"[parser] Собрано: {total} сообщений...")
                        await asyncio.sleep(_THROTTLE_SLEEP)

            except FloodWait as e:
                # FloodWait выше sleep_threshold: сохраняем то, что успели
                logger.warning("[parser] FloodWait %d сек — сохраняем частичный результат.", e.value)
                print(
                    f"[parser] ⚠️  FloodWait {e.value} сек. "
                    f"Сохраняем {total} сообщений и завершаем."
                )

            print(f"[parser] Сбор завершён: {total} сообщений. Агрегируем...")
            stats = aggregate_messages(batch)
            del batch  # освободить память до записи в БД

            print(f"[parser] Записываем {len(stats)} пользователей в БД...")
            for uid, data in stats.items():
                await queries.save_parsed_stats(
                    conn,
                    user_id          = uid,
                    username         = data["username"],
                    message_count    = data["count"],
                    first_message_at = data["first_ts"],
                    last_message_at  = data["last_ts"],
                )
            await conn.commit()

            print(
                "[parser] ✅ Готово!\n"
                f"[parser]    Пользователей: {len(stats)}\n"
                f"[parser]    Сообщений учтено: {total}\n"
            )

    finally:
        await close_db()
        print("[parser] БД закрыта.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(main())

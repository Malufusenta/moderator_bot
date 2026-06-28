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

async def _collect_members(app, conn) -> int:
    """Шаг 1: загрузить всех текущих участников чата.

    Использует get_chat_members() — возвращает людей независимо от того,
    писали они что-нибудь или нет. joined_date может быть None для старых
    участников — в этом случае пишем NULL, не перетирая существующее значение.

    Возвращает количество обработанных участников.
    """
    from pyrogram.errors import FloodWait

    count = 0
    print("[parser] Шаг 1: загрузка списка участников...")

    try:
        async for member in app.get_chat_members(config.CHAT_ID):
            user = member.user
            if user is None or user.is_bot:
                continue

            joined_at: int | None = None
            if member.joined_date is not None:
                joined_at = int(member.joined_date.timestamp())

            await queries.upsert_member(
                conn,
                user_id   = user.id,
                username  = user.username,
                joined_at = joined_at,
            )
            count += 1

            if count % 100 == 0:
                print(f"[parser]   участников: {count}...")
                await conn.commit()

    except FloodWait as e:
        logger.warning("[parser] FloodWait %d сек при загрузке участников.", e.value)
        print(f"[parser] ⚠️  FloodWait {e.value} сек — частичный результат.")

    await conn.commit()
    print(f"[parser] Участников записано: {count}")
    return count


async def _collect_history(app, conn) -> int:
    """Шаг 2: пройти историю сообщений, обновить счётчики активности.

    Только этот шаг обновляет message_count / first_message_at / last_message_at.
    Участники без сообщений (загруженные в шаге 1) не затрагиваются.
    Возвращает количество сообщений.
    """
    from pyrogram.errors import FloodWait

    batch: list = []
    total = 0

    print("[parser] Шаг 2: сбор истории сообщений...")

    try:
        async for msg in app.get_chat_history(config.CHAT_ID):
            batch.append(msg)
            total += 1
            if total % _PROGRESS_STEP == 0:
                print(f"[parser]   сообщений: {total}...")
                await asyncio.sleep(_THROTTLE_SLEEP)

    except FloodWait as e:
        logger.warning("[parser] FloodWait %d сек — сохраняем частичный результат.", e.value)
        print(
            f"[parser] ⚠️  FloodWait {e.value} сек. "
            f"Сохраняем {total} сообщений и завершаем."
        )

    print(f"[parser] Сбор завершён: {total} сообщений. Агрегируем...")
    stats = aggregate_messages(batch)
    del batch

    print(f"[parser] Записываем статистику для {len(stats)} пользователей...")
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
    return total


async def main() -> None:
    """Сбор данных чата и наполнение БД.

    Два шага:
      1. get_chat_members() — все участники, включая молчунов.
         joined_at пишется если известен; не перетирает существующее.
      2. get_chat_history() — история сообщений → счётчики активности.

    После выполнения в БД есть все участники чата, даже те кто ни разу
    не писал — /check по ним вернёт досье с нулевой активностью.
    """
    from pyrogram import Client

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
            members_count = await _collect_members(app, conn)
            messages_count = await _collect_history(app, conn)

            print(
                "\n[parser] ✅ Готово!\n"
                f"[parser]    Участников в чате: {members_count}\n"
                f"[parser]    Сообщений учтено: {messages_count}\n"
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

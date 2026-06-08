"""
Гейт доверия: две независимые проверки перед публикацией объявления (раздел 6.3 ТЗ).

Шаг 1 — вайтлист (is_whitelisted):
  Полный байпас: user_id в WHITELIST_IDS → пропустить ВСЕ проверки,
  включая анти-дубль. Handler вызывает эту функцию ПЕРВОЙ; при True
  дальше ничего не проверяется.

Шаг 2 — «заработанное» доверие (is_trusted):
  Проверяется только если пользователь НЕ в вайтлисте.
  Доверенный = оба условия одновременно:
    а) message_count >= TRUST_LIMIT    (суммарная активность за всё время)
    б) last_message_at >= cutoff       (свежесть: хотя бы одно сообщение в окне)
  Если хотя бы одно не выполнено → недоверенный.

Почему last_message_at, а не COUNT по таблице messages:
  Парсер (parser.py) заполняет last_message_at по всей истории, но не вставляет
  строки в messages (это дорого для миллионов строк и избыточно для истории).
  При RECENCY_MIN_MESSAGES=1 «хотя бы 1 сообщение в окне» ≡ last_message_at >= cutoff.
  Функция get_recent_message_count в queries.py остаётся нетронутой на будущее —
  если понадобится строгий счёт min > 1 уже на живых данных бота.

КОНТРАКТ для handler'а:
  is_trusted вызывается ДО записи текущего сообщения-объявления в БД.
  Если вызвать после upsert_user, last_message_at уже будет = now и свежесть
  всегда пройдёт — что неверно для «спящего» нарушителя.
  Порядок в handler'е: is_trusted → (если нужно) upsert_user + add_message_row.
"""

from __future__ import annotations

import config
from database import queries
from database.db import get_db


def is_whitelisted(user_id: int) -> bool:
    """Проверить, находится ли пользователь в вайтлисте (раздел 6.3, шаг 1).

    Вайтлист — полный байпас: при True handler сразу пропускает сообщение,
    не вызывая is_trusted и не проверяя дубли.
    Синхронная, без обращения к БД — читает только конфиг.
    """
    return user_id in config.WHITELIST_IDS


async def is_trusted(user_id: int, now_ts: int) -> bool:
    """Проверить «заработанное» доверие (раздел 6.3, шаг 2).

    Вайтлист здесь НЕ проверяется — handler обязан вызвать is_whitelisted() раньше.

    Алгоритм:
      1. Загрузить профиль из БД. Нет записи → False (бот не видел этого юзера).
      2. message_count < TRUST_LIMIT → False (новичок, нет суммарной активности).
      3. Вычислить cutoff = now_ts - RECENCY_DAYS * 86400.
         last_message_at is None или < cutoff → False (спящий: был активен, но давно).
      4. Иначе → True (доверенный: и суммарно, и недавно).

    Аргумент now_ts принимается параметром (а не вычисляется внутри) —
    это позволяет handler'у передать один и тот же момент времени и в гейт,
    и в upsert_user, не допуская расхождения часов между двумя вызовами.
    """
    conn = get_db()
    profile = await queries.get_user(conn, user_id)

    if profile is None:
        return False  # пользователь вообще не встречался боту

    if profile["message_count"] < config.TRUST_LIMIT:
        return False  # новичок: мало сообщений за всё время

    cutoff = now_ts - config.RECENCY_DAYS * 86400
    last_active = profile["last_message_at"]

    if last_active is None or last_active < cutoff:
        return False  # спящий: суммарно много, но давно не писал

    return True  # доверенный: и количество, и свежесть в норме

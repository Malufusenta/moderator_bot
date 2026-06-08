"""
Анти-дубль для доверенных пользователей (раздел 6.4 ТЗ).

Два этапа сравнения — от быстрого к медленному:
  1. Точный хеш (SHA-256 нормализованного текста) — O(n) сравнений строк.
     Ловит дословные повторы мгновенно, без диффлиба.
  2. Нечёткое сравнение (difflib.SequenceMatcher.ratio) — O(n·m) посимвольно.
     Ловит объявления с мелкими правками: добавили/убрали «!», изменили цену.

Оба этапа работают с нормализованным текстом (normalizer.normalize), который
убирает пунктуацию, эмодзи и лишние пробелы — чтобы «Продам!!!» и «продам»
давали одинаковый хеш.

Контракт с handler'ом:
  handler вызывает check_duplicate(user_id, raw_text, now_ts) ПЕРЕД тем как
  разрешить публикацию. Если False — вызывает save_if_new(user_id, raw_text, now_ts)
  для записи в историю. Если True — удаляет сообщение и мутит.
"""

from __future__ import annotations

import difflib
import hashlib

import config
from database import queries
from database.db import get_db
from moderation.normalizer import normalize


def _prepare(text: str) -> tuple[str, str]:
    """Нормализовать текст и вычислить SHA-256 хеш.

    Общий внутренний хелпер для check_duplicate и save_if_new:
    нормализация выполняется ровно один раз при каждом обращении,
    и нормализованная строка + хеш передаются в обе ветки без дублирования.

    Возвращает:
        (normalized, hex_digest)  — оба значения сохраняются в advertisements.
    """
    normalized = normalize(text)
    text_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return normalized, text_hash


async def check_duplicate(user_id: int, text: str, now_ts: int) -> bool:
    """Вернуть True, если text является дублем одного из прошлых объявлений юзера.

    Алгоритм (раздел 6.4):
      1. Нормализовать + хешировать входной текст.
      2. Guard: если нормализованный текст < 3 символов — False (нечего сравнивать).
      3. Загрузить прошлые объявления пользователя из БД.
         Нет прошлых объявлений → False (первое объявление всегда новое).
      4. Быстрый путь: совпал хеш с любым прошлым → True (точный повтор).
      5. Медленный путь: difflib.SequenceMatcher.ratio() для каждого прошлого.
         Берём максимум. Если max > config.SIMILARITY_THRESHOLD → True.
      6. Иначе → False.

    Принимает now_ts для единообразия с save_if_new (handler передаёт
    одну метку времени в оба вызова), здесь параметр не используется.

    TODO: при большом архиве объявлений одного юзера (десятки+) можно
      ограничить сравнение последними N записями через ORDER BY created_at DESC LIMIT N.
      Для домового чата на 700 человек неактуально — объявлений будет единицы.
    """
    normalized, text_hash = _prepare(text)

    # Guard: слишком короткий текст после нормализации — не сравниваем
    if len(normalized) < 3:
        return False

    conn = get_db()
    past_ads = await queries.get_user_ads(conn, user_id)

    if not past_ads:
        return False  # у пользователя ещё нет истории объявлений

    # ── Быстрый путь: точное совпадение по SHA-256 ────────────────────────────
    for ad in past_ads:
        if ad["text_hash"] == text_hash:
            return True  # дословный повтор

    # ── Медленный путь: нечёткое сравнение через difflib ─────────────────────
    # ratio() ∈ [0.0, 1.0]; 0.85 — умеренно строгий порог: ловит правки цены,
    # добавленные восклицательные знаки, перестановку слов с сохранением смысла.
    max_ratio = max(
        difflib.SequenceMatcher(
            None,
            normalized,
            ad["normalized_text"],
            autojunk=False,   # отключить авто-эвристику, корректнее для коротких строк
        ).ratio()
        for ad in past_ads
    )

    return max_ratio > config.SIMILARITY_THRESHOLD


async def save_if_new(user_id: int, text: str, now_ts: int) -> None:
    """Сохранить объявление в архив (вызывать ТОЛЬКО при check_duplicate → False).

    Нормализует текст, вычисляет хеш и вставляет запись в таблицу advertisements.
    Повторную проверку на дубль не делает — это ответственность handler'а.
    """
    normalized, text_hash = _prepare(text)
    conn = get_db()
    await queries.save_advertisement(conn, user_id, normalized, text_hash, now_ts)

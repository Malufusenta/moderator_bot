"""
Определение объявления: медиа + стоп-слово в тексте/подписи (раздел 6.2 ТЗ).

Два прохода проверки стоп-слов:

  Проход 1 — основной, через deobfuscate() + регулярки с \b.
    Ловит: "Продам", "ПРОДАМ", "продаëтся", "рr0dам" (после деобфускации).
    Не ловит: "п р о д а м" (пробелы разрушают \b-границу слова).

  Проход 2 — anti-collapse, через collapse() + вхождение подстроки.
    Убирает ВСЕ не-буквы из текста и из стоп-слов, ищет вхождение.
    Ловит: "п р о д а м" → "продам", "про-дам" → "продам".
    Компромисс: может дать ложные срабатывания на составные слова, но для
    домового чата это допустимо; TODO: добавить минимальную длину слова
    или whitelist исключений при необходимости.

КРИТИЧНО (раздел 6.2):
  У медиа-сообщений текст лежит в message.caption, а НЕ в message.text.
  Объединять: (message.text or "") + " " + (message.caption or "").
  Без этого детект не сработает ни разу — барахолка это почти всегда
  фото/видео с подписью.

Регулярка стоп-слов компилируется ОДИН РАЗ при импорте модуля.
"""

from __future__ import annotations

import re

import config
from moderation.normalizer import deobfuscate, collapse


# ─── Компиляция паттерна стоп-слов ────────────────────────────────────────────

def _build_stop_pattern(words: list[str]) -> re.Pattern[str]:
    """Собрать один compiled-паттерн из всех стоп-слов.

    Каждое слово оборачивается в \\b...\\b для границ.
    Многословные фразы ("к продаже") — пробел заменяется \\s+,
    чтобы работало и с переносом строки, и с двойным пробелом.
    re.escape гарантирует безопасность произвольных строк из конфига.
    """
    if not words:
        # Паттерн, который никогда не совпадает — бот работает без стоп-слов
        return re.compile(r"(?!)", re.IGNORECASE | re.UNICODE)

    parts = []
    for word in words:
        escaped = re.escape(word)
        # Пробелы в фразе → \s+ (совместимо с любым whitespace между словами)
        escaped = escaped.replace(r"\ ", r"\s+").replace(" ", r"\s+")
        parts.append(r"\b" + escaped + r"\b")

    pattern = "|".join(parts)
    return re.compile(pattern, re.IGNORECASE | re.UNICODE)


# Compile once at import time — не создавать на каждое сообщение
_STOP_PATTERN: re.Pattern[str] = _build_stop_pattern(config.STOP_WORDS)

# Collapsed версии стоп-слов — для прохода 2 (anti-collapse)
# Только слова длиной >= 4 буквы после схлопывания, чтобы избежать
# коротких совпадений ("ам", "ру" и т.п.) — TODO: тюнить при необходимости
_COLLAPSED_STOP_WORDS: list[str] = [
    collapsed
    for w in config.STOP_WORDS
    if len(collapsed := re.sub(r"[^а-яёa-z]", "", w.lower())) >= 4
]


# ─── Публичные функции ────────────────────────────────────────────────────────

def get_combined_text(message) -> str:
    """Объединить text и caption сообщения в одну строку.

    У медиа-сообщений (фото, видео) текст лежит в caption, не в text.
    Эта функция — единственное место извлечения текста: используется и
    в is_advertisement(), и в handlers/messages.py для анти-дубля,
    чтобы не дублировать логику.
    """
    return (message.text or "") + " " + (message.caption or "")


def _has_media(message) -> bool:
    """True, если сообщение содержит фото, видео или анимацию (GIF)."""
    return bool(
        message.photo
        or message.video
        or message.animation  # GIF-объявления тоже встречаются
    )


def _contains_stop_word(text: str) -> bool:
    """Проверить наличие стоп-слова двумя проходами.

    Проход 1: deobfuscate + regex \b — основной.
    Проход 2: collapse + substring — ловит расклеенные слова.
    """
    # Проход 1: деобфускация гомоглифов + поиск по \b-границам
    clean = deobfuscate(text)
    if _STOP_PATTERN.search(clean):
        return True

    # Проход 2: схлопнуть все пробелы/пунктуацию, искать подстроку
    # Ловит: "п р о д а м" → "продам", "про-дам" → "продам"
    # TODO: при необходимости добавить минимальный порог длины совпадения
    #   или список исключений (false-positive: "снятьё", "продамся" и т.п.)
    collapsed_text = collapse(clean)
    for stop in _COLLAPSED_STOP_WORDS:
        if stop in collapsed_text:
            return True

    return False


def is_advertisement(message) -> bool:
    """Вернуть True, если сообщение является объявлением (раздел 6.2 ТЗ).

    Объявление = ОБА условия одновременно:
      1. В сообщении есть медиа (фото / видео / анимация).
      2. Текст (text или caption) содержит стоп-слово.

    Порядок проверок: сначала медиа (дешевле) — short-circuit,
    если медиа нет, текст не анализируем.
    """
    if not _has_media(message):
        return False

    combined = get_combined_text(message)
    if not combined.strip():
        return False

    return _contains_stop_word(combined)

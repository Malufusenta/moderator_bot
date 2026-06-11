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


def _build_collapsed(words: list[str]) -> list[str]:
    return [
        collapsed
        for w in words
        if len(collapsed := re.sub(r"[^а-яёa-z]", "", w.lower())) >= 4
    ]


# Compile once at import time — не создавать на каждое сообщение
_STOP_PATTERN_MEDIA: re.Pattern[str] = _build_stop_pattern(config.STOP_WORDS_MEDIA)
_STOP_PATTERN_TEXT:  re.Pattern[str] = _build_stop_pattern(config.STOP_WORDS_TEXT)
_COLLAPSED_MEDIA: list[str] = _build_collapsed(config.STOP_WORDS_MEDIA)
_COLLAPSED_TEXT:  list[str] = _build_collapsed(config.STOP_WORDS_TEXT)

# Backward-compat: объединённый паттерн (используется в contains_stopword)
_STOP_PATTERN: re.Pattern[str] = _build_stop_pattern(config.STOP_WORDS)
_COLLAPSED_STOP_WORDS: list[str] = _build_collapsed(config.STOP_WORDS)


# ─── Публичные функции ────────────────────────────────────────────────────────

def get_combined_text(message) -> str:
    """Объединить text и caption сообщения в одну строку.

    У медиа-сообщений (фото, видео) текст лежит в caption, не в text.
    Эта функция — единственное место извлечения текста: используется и
    в is_advertisement(), и в handlers/messages.py для анти-дубля,
    чтобы не дублировать логику.
    """
    return (message.text or "") + " " + (message.caption or "")


def has_media(message) -> bool:
    """True, если сообщение содержит фото, видео или анимацию (GIF).

    Публичная функция — используется в handlers/messages.py для проверки
    наличия медиа по всему альбому: any(has_media(m) for m in messages).
    """
    return bool(
        message.photo
        or message.video
        or message.animation  # GIF-объявления тоже встречаются
    )


def _check(text: str, pattern: re.Pattern[str], collapsed_words: list[str]) -> bool:
    clean = deobfuscate(text)
    if pattern.search(clean):
        return True
    collapsed_text = collapse(clean)
    return any(stop in collapsed_text for stop in collapsed_words)


def contains_stopword_media(text: str) -> bool:
    """Стоп-слово из группы «требует медиа» (продам, аренда…)."""
    return _check(text, _STOP_PATTERN_MEDIA, _COLLAPSED_MEDIA)


def contains_stopword_text(text: str) -> bool:
    """Стоп-слово из группы «без медиа» (сдам, пересдам…)."""
    return _check(text, _STOP_PATTERN_TEXT, _COLLAPSED_TEXT)


def contains_stopword(text: str) -> bool:
    """Любое стоп-слово (объединение обоих групп)."""
    return _check(text, _STOP_PATTERN, _COLLAPSED_STOP_WORDS)


def is_advertisement(message) -> bool:
    """Вернуть True, если сообщение является объявлением (раздел 6.2 ТЗ).

    Объявление = ОБА условия одновременно:
      1. В сообщении есть медиа (фото / видео / анимация).
      2. Текст (text или caption) содержит стоп-слово.

    Тонкий обёртыватель над has_media + contains_stopword.
    Для одиночных сообщений достаточен; альбомы обрабатываются
    напрямую через has_media/contains_stopword в handlers/messages.py.
    """
    return has_media(message) and contains_stopword(get_combined_text(message))

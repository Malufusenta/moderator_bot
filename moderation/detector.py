"""
Определение того, является ли входящее сообщение объявлением.

Объявление = медиа (фото или видео) + стоп-слово в тексте/подписи.

Ключевые моменты из раздела 6.2 ТЗ:
  - Текст проверять из объединения message.text и message.caption:
      combined = (msg.text or "") + " " + (msg.caption or "")
    Без этого детект не сработает, т.к. барахолка — это фото с подписью.
  - Стоп-слова искать регулярками re с \b (границы слов), регистронезависимо.
  - Фразы с пробелами (например «к продаже») тоже должны находиться.
  - Альбомы (media_group_id): обрабатывать группу как единое целое (раздел 6.2).
  - Слушать edited_message наравне с обычными сообщениями (раздел 6.2).

# TODO (раздел 6.2 ТЗ): реализовать is_advertisement(message) -> bool.
#   Проверить наличие медиа, нормализовать combined-текст через normalizer,
#   пройтись по STOP_WORDS с re.search(r'\b{word}\b', text, re.IGNORECASE).
"""

from __future__ import annotations

from aiogram.types import Message


def is_advertisement(message: Message) -> bool:
    """Вернуть True, если сообщение является объявлением (медиа + стоп-слово).

    # TODO (раздел 6.2): проверить message.photo / message.video,
    #   объединить text + caption, нормализовать, искать стоп-слова.
    """
    raise NotImplementedError

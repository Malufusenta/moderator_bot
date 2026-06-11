"""
Оркестратор модерации входящих сообщений группового чата (раздел 6 ТЗ).

Обрабатывает: message, edited_message — только из config.CHAT_ID.
Логика: учёт активности → детект объявления → вайтлист → гейт доверия
        → анти-дубль → меры (удаление + мут + предупреждение).

Сборщик альбомов (media_group_id):
  Telegram разбивает альбом на N отдельных сообщений, приходящих
  почти одновременно (< 0.5 сек). Обрабатываем группу как единое целое:
  накапливаем сообщения в _albums[mgid] и ждём ALBUM_DEBOUNCE секунд после
  последнего поступившего, чтобы собрать все части перед оценкой.
  Одно объявление-альбом → одно удаление, один мут, одно предупреждение.

Подводные камни (раздел 8 ТЗ):
  - until_date для мута — timezone-aware UTC (делается в actions.mute_user).
  - Нельзя мутить администраторов — обработано в actions.mute_user.
  - Бот не может написать в ЛС — предупреждения в чат с автоудалением.
  - Гейт доверия вызывается ДО записи сообщения в БД (контракт trust.py).
"""

from __future__ import annotations

import asyncio
import html
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message, User

import config
from database import queries
from database.db import get_db
from handlers import actions
from moderation import detector, duplicates, trust
from utils.admin_log import log_action

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

router = Router()

# ── Константы ─────────────────────────────────────────────────────────────────

# Задержка перед обработкой альбома: ждём, пока Telegram пришлёт все части
ALBUM_DEBOUNCE: float = 1.0  # секунды

# ── Состояние сборщика альбомов (модульный уровень) ───────────────────────────
# Ключ — media_group_id (str), значение — накопленные сообщения / активный бот
_albums: dict[str, list[Message]] = {}
_album_bots: dict[str, Bot] = {}
_album_tasks: dict[str, asyncio.Task] = {}
_pending_album_tasks: set[asyncio.Task] = set()  # держим ref против GC


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _mention(user: User) -> str:
    """Безопасное упоминание: @username или экранированное полное имя."""
    if user.username:
        return f"@{user.username}"
    return html.escape(user.full_name)


def _fmt_utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def _snippet(text: str, limit: int = 150) -> str:
    return text[:limit] + ("…" if len(text) > limit else "")


async def _delete_messages(bot: Bot, chat_id: int, messages: list[Message]) -> None:
    """Удалить все части объявления; поглощает ошибки (уже удалено, нет прав)."""
    for m in messages:
        try:
            await bot.delete_message(chat_id, m.message_id)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.debug("delete %d: %s", m.message_id, exc)


# ── Ядро модерации ────────────────────────────────────────────────────────────

async def handle_post(messages: list[Message], bot: Bot) -> None:
    """Основная логика модерации для одного поста (одиночное или альбом).

    Принимает список сообщений, которые уже отсортированы по message_id
    (для альбомов) или содержат единственный элемент (одиночное сообщение).

    Порядок шагов строго фиксирован — нарушение порядка ломает контракты:
      гейт доверия ОБЯЗАН идти до upsert_user, иначе свежесть всегда True.
    """
    msg0 = messages[0]
    user = msg0.from_user
    if user is None:
        return  # сервисное сообщение без автора

    user_id: int = user.id
    username: str | None = user.username
    chat_id: int = msg0.chat.id
    now_ts: int = int(time.time())
    conn = get_db()

    # ── Определяем, является ли пост объявлением ──────────────────────────────
    # Для альбома: медиа хотя бы в одном сообщении + стоп-слово в объединённом
    # тексте всех сообщений (caption может быть только у первого в альбоме).
    media_present = any(
        m.photo or m.video or m.animation for m in messages
    )
    combined_text = " ".join(detector.get_combined_text(m) for m in messages)
    is_ad = media_present and detector.contains_stopword(combined_text)

    # ── Шаг 1: не объявление — учитываем активность и выходим ────────────────
    if not is_ad:
        await queries.upsert_user(conn, user_id, username, now_ts)
        await queries.add_message_row(conn, user_id, now_ts)
        return

    # ── Шаг 2: вайтлист — полный байпас всех проверок ────────────────────────
    if trust.is_whitelisted(user_id):
        await queries.upsert_user(conn, user_id, username, now_ts)
        await queries.add_message_row(conn, user_id, now_ts)
        return

    # ── Шаг 3: гейт доверия (ДО записи в БД — контракт) ─────────────────────
    trusted = await trust.is_trusted(user_id, now_ts)

    mention = _mention(user)
    hours = config.MUTE_HOURS

    # ── Шаг 4: недоверенный пользователь ─────────────────────────────────────
    if not trusted:
        warn_text = (
            f"{mention}, публикация объявлений доступна только активным участникам. "
            f"Вы переведены в режим чтения на {hours} часа."
        )
        await queries.increment_ad_attempts(conn, user_id, username, now_ts)
        actor = f"@{username}" if username else user.full_name
        actor_display = f"{actor} (ID: {user_id})"
        snip     = _snippet(combined_text)
        time_str = _fmt_utc(now_ts)
        if config.DRY_RUN:
            logger.info(
                "[DRY_RUN] user_id=%d причина=недоверенный действие=delete+mute+warn текст=%r",
                user_id, warn_text,
            )
            await log_action(bot,
                f"🧪 [DRY-RUN] 🔇 БЫ замьютил (недоверенный)\n"
                f"👤 {actor_display}\n"
                f"📝 объявление от недоверенного\n"
                f"✉️ {snip}\n"
                f"🕐 {time_str}"
            )
        else:
            await _delete_messages(bot, chat_id, messages)
            muted = await actions.mute_user(bot, chat_id, user_id, hours)
            await actions.warn(bot, chat_id, warn_text, config.WARNING_DELETE_SECONDS)
            if muted:
                await log_action(bot,
                    f"🔇 Мут (недоверенный)\n"
                    f"👤 {actor_display}\n"
                    f"📝 объявление от недоверенного\n"
                    f"✉️ {snip}\n"
                    f"🕐 {time_str}"
                )
            else:
                await log_action(bot,
                    f"⚠️ Не удалось замьютить\n"
                    f"👤 {actor_display}\n"
                    f"📝 нет прав или цель — админ\n"
                    f"🕐 {time_str}"
                )
        # Не записываем: удалённое/заблокированное объявление не считается активностью
        return

    # ── Шаг 5: доверенный — проверка на дубль ────────────────────────────────
    dup = await duplicates.check_duplicate(user_id, combined_text, now_ts)

    if dup:
        warn_text = (
            f"{mention}, повторная публикация того же объявления запрещена. "
            f"Вы переведены в режим чтения на {hours} часа."
        )
        await queries.increment_ad_attempts(conn, user_id, username, now_ts)
        actor = f"@{username}" if username else user.full_name
        actor_display = f"{actor} (ID: {user_id})"
        snip     = _snippet(combined_text)
        time_str = _fmt_utc(now_ts)
        if config.DRY_RUN:
            logger.info(
                "[DRY_RUN] user_id=%d причина=дубль действие=delete+mute+warn текст=%r",
                user_id, warn_text,
            )
            await log_action(bot,
                f"🧪 [DRY-RUN] 🔁 БЫ замьютил (дубль)\n"
                f"👤 {actor_display}\n"
                f"📝 повторная публикация\n"
                f"✉️ {snip}\n"
                f"🕐 {time_str}"
            )
        else:
            await _delete_messages(bot, chat_id, messages)
            muted = await actions.mute_user(bot, chat_id, user_id, hours)
            await actions.warn(bot, chat_id, warn_text, config.WARNING_DELETE_SECONDS)
            if muted:
                await log_action(bot,
                    f"🔁 Мут (дубль)\n"
                    f"👤 {actor_display}\n"
                    f"📝 повторная публикация\n"
                    f"✉️ {snip}\n"
                    f"🕐 {time_str}"
                )
            else:
                await log_action(bot,
                    f"⚠️ Не удалось замьютить\n"
                    f"👤 {actor_display}\n"
                    f"📝 нет прав или цель — админ\n"
                    f"🕐 {time_str}"
                )
        # Не записываем и не save_if_new: нарушение, не пост
        return

    # ── Шаг 5b: новое оригинальное объявление — сохранить и засчитать ────────
    await duplicates.save_if_new(user_id, combined_text, now_ts)
    await queries.upsert_user(conn, user_id, username, now_ts)
    await queries.add_message_row(conn, user_id, now_ts)


# ── Сборщик альбомов ─────────────────────────────────────────────────────────

async def _flush_album(mgid: str) -> None:
    """Корутина дебаунса: ждём ALBUM_DEBOUNCE, затем обрабатываем альбом."""
    await asyncio.sleep(ALBUM_DEBOUNCE)

    messages = _albums.pop(mgid, [])
    bot = _album_bots.pop(mgid, None)
    _album_tasks.pop(mgid, None)

    if not messages or bot is None:
        return

    messages.sort(key=lambda m: m.message_id)
    await handle_post(messages, bot)


# ── Хендлеры ─────────────────────────────────────────────────────────────────

@router.message(F.chat.id == config.CHAT_ID)
@router.edited_message(F.chat.id == config.CHAT_ID)
async def on_message(message: Message, bot: Bot) -> None:
    """Точка входа: одиночное сообщение или часть альбома из целевого чата.

    Фильтр F.chat.id == config.CHAT_ID — принимаем только сообщения
    из нашего чата; ЛС (admin-панель) обрабатывается в handlers/admin.py.

    Зарегистрирован и для message, и для edited_message — ловим попытку
    обойти модерацию через редактирование (раздел 6.2 ТЗ).
    Примечание: edited_message увеличит message_count повторно, если текст
    не-объявление, — небольшая погрешность, некритична для домового чата.
    """
    # Пропускаем сервисные и анонимные обновления
    if message.from_user is None or message.from_user.is_bot:
        return

    mgid = message.media_group_id

    # Одиночное сообщение — обрабатываем сразу
    if mgid is None:
        await handle_post([message], bot)
        return

    # Альбом: накапливаем, дебаунсим
    _albums.setdefault(mgid, []).append(message)
    _album_bots[mgid] = bot  # одинаковый бот для всех сообщений альбома

    # Отменяем предыдущую flush-задачу (ещё не все части пришли)
    if mgid in _album_tasks:
        _album_tasks[mgid].cancel()
        _pending_album_tasks.discard(_album_tasks[mgid])

    task = asyncio.create_task(_flush_album(mgid), name=f"album_{mgid}")
    _album_tasks[mgid] = task
    _pending_album_tasks.add(task)
    task.add_done_callback(_pending_album_tasks.discard)

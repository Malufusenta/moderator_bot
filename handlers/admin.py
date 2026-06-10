"""
Админ-панель: досье пользователя в личных сообщениях бота (раздел 7 ТЗ).

Безопасность:
  - router.message.filter(F.chat.type == "private") — бот реагирует на ЛС.
  - Каждый хендлер проверяет from_user.id in config.ADMIN_IDS и возвращает
    управление без ответа при несоответствии.
  - НЕТ catch-all: сообщение в ЛС от не-админа не получает никакого ответа.

Два триггера:
  1. /check <user_id>           — досье по числовому ID
     /check <@username|username> — досье по username (с предупреждением
                                   о ненадёжности поиска, раздел 8 ТЗ)
  2. Пересланное сообщение      — досье по user_id из forward_origin
                                   (или forward_from для старых клиентов);
                                   приватная пересылка (hidden_user) → сообщение
                                   о невозможности определить отправителя.

Формат досье (раздел 7 ТЗ):
  👤 Пользователь: @username (ID: …)
  📅 В чате с: …
  🔗 Пришёл по ссылке: …
  ✉️ Сообщений всего: …
  🟢 Активность за 60 дней: …
  ⏱ Первое сообщение: …
  ⏱ Последняя активность: …
  🛑 Попыток нарушений: …
  ⚖️ Статус: Доверенный / Спящий / Новичок

Статус определяется по last_message_at (те же правила, что в is_trusted),
а не по счётчику messages_last_period (раздел 6.3 / 7 ТЗ).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import Message

import config
from database import queries
from database.db import get_db

logger = logging.getLogger(__name__)

router = Router()
# Все хендлеры этого роутера работают ТОЛЬКО в приватных чатах.
router.message.filter(F.chat.type == "private")


# ─── Фильтр «сообщение является пересылкой» ──────────────────────────────────

class _IsForwarded(BaseFilter):
    """Возвращает True, если сообщение является пересылкой (оба API)."""

    async def __call__(self, message: Message) -> bool:
        return (
            getattr(message, "forward_origin", None) is not None
            or getattr(message, "forward_from",   None) is not None
            or getattr(message, "forward_date",   None) is not None
        )


# ─── Вспомогательные функции ──────────────────────────────────────────────────

def _is_admin(user_id: int) -> bool:
    """Проверить, входит ли user_id в список ADMIN_IDS."""
    return user_id in config.ADMIN_IDS


_TZ_VN = timezone(timedelta(hours=7))  # UTC+7 Вьетнам

def _fmt_ts(ts: int | None) -> str:
    """Преобразовать unix-timestamp в читаемую строку (UTC+7) или 'Нет данных'."""
    if ts is None:
        return "Нет данных"
    dt = datetime.fromtimestamp(ts, tz=_TZ_VN)
    return dt.strftime("%d.%m.%Y %H:%M")


def _determine_status(profile: dict, now_ts: int) -> str:
    """Определить статус пользователя по тем же правилам, что is_trusted.

    Правила (раздел 6.3 ТЗ):
      Новичок:   message_count < TRUST_LIMIT
      Доверенный: message_count >= TRUST_LIMIT И last_message_at >= cutoff
      Спящий:    message_count >= TRUST_LIMIT И last_message_at < cutoff (или None)
    """
    cutoff = now_ts - config.RECENCY_DAYS * 86_400
    mc   = profile["message_count"]
    last = profile.get("last_message_at")

    if mc < config.TRUST_LIMIT:
        return "🔴 Новичок"
    if last is not None and last >= cutoff:
        return "🟢 Доверенный"
    return "🟡 Спящий"


def _format_added_by(profile: dict, added_by_user: dict | None) -> str:
    """Сформировать строку «Кто добавил» для досье."""
    added_by_id = profile.get("added_by")
    if added_by_id is not None:
        if added_by_user and added_by_user.get("username"):
            return f"@{added_by_user['username']} (ID: {added_by_id})"
        return f"ID: {added_by_id}"
    if profile.get("joined_at") is not None:
        return "вступил сам"
    return "Нет данных"


def _format_dossier(profile: dict, now_ts: int, added_by_user: dict | None = None) -> str:
    """Собрать текст досье из профиля пользователя."""
    username = profile.get("username")
    uid      = profile["user_id"]
    user_line   = f"@{username} (ID: {uid})" if username else f"ID: {uid}"
    invite_link = profile.get("invite_link") or "Нет данных"
    status      = _determine_status(profile, now_ts)
    added_by_str = _format_added_by(profile, added_by_user)

    last = profile.get("last_message_at")
    cutoff = now_ts - config.RECENCY_DAYS * 86_400
    if last is not None:
        days_ago      = (now_ts - last) // 86_400
        in_window_str = "Да" if last >= cutoff else "Нет"
        days_ago_str  = str(days_ago)
    else:
        days_ago_str  = "Нет данных"
        in_window_str = "Нет данных"

    return (
        f"👤 Пользователь: {user_line}\n"
        f"📅 В чате с: {_fmt_ts(profile.get('joined_at'))}\n"
        f"👥 Кто добавил: {added_by_str}\n"
        f"🔗 Пришёл по ссылке: {invite_link}\n"
        f"✉️ Сообщений всего: {profile['message_count']}\n"
        f"⏱ Первое сообщение: {_fmt_ts(profile.get('first_message_at'))}\n"
        f"⏱ Последняя активность: {_fmt_ts(last)}\n"
        f"📆 Дней с последней активности: {days_ago_str}\n"
        f"🔍 В окне свежести ({config.RECENCY_DAYS} дн.): {in_window_str}\n"
        f"🛑 Попыток нарушений: {profile['ad_attempts']}\n"
        f"⚖️ Статус: {status}"
    )


async def _send_dossier(message: Message, user_id: int) -> None:
    """Запросить профиль и отправить досье. Если пользователь не найден — сообщить об этом."""
    now_ts = int(time.time())
    cutoff = now_ts - config.RECENCY_DAYS * 86_400
    conn   = get_db()

    profile = await queries.get_user_profile(conn, user_id, cutoff)
    if profile is None:
        await message.answer("Нет данных по этому пользователю.")
        return

    added_by_user: dict | None = None
    if profile.get("added_by") is not None:
        added_by_user = await queries.get_user(conn, profile["added_by"])

    await message.answer(_format_dossier(profile, now_ts, added_by_user))


# ─── Хендлеры ─────────────────────────────────────────────────────────────────

@router.message(Command("check"))
async def handle_check(message: Message) -> None:
    """/check <user_id|@username> — досье по ID или username.

    Поиск по username ненадёжен (может смениться) — рядом с результатом
    выводится предупреждение (раздел 8 ТЗ).
    """
    if not message.from_user or not _is_admin(message.from_user.id):
        return  # не-админ — молча игнорируем

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "Использование:\n"
            "  /check 123456789   — поиск по числовому ID\n"
            "  /check @username   — поиск по username (ненадёжно)"
        )
        return

    arg = parts[1].strip().lstrip("@")

    if arg.isdigit():
        # Числовой ID — надёжный путь
        await _send_dossier(message, int(arg))
        return

    # Username — поиск в БД
    conn    = get_db()
    user_id = await queries.find_user_id_by_username(conn, arg)
    if user_id is None:
        await message.answer(
            "Пользователь не найден в базе.\n"
            "⚠️ Поиск по username ненадёжен: username мог измениться. "
            "Используйте /check <user_id> для надёжного поиска."
        )
        return

    await _send_dossier(message, user_id)


@router.message(_IsForwarded())
async def handle_forward(message: Message) -> None:
    """Пересланное сообщение — показать досье исходного отправителя.

    Поддерживает оба API:
      - forward_origin (Telegram Bot API ≥ 7.0 / aiogram 3.7+):
          MessageOriginUser      → sender_user.id
          MessageOriginHiddenUser → «приватная пересылка»
          прочие                  → «канал/чат»
      - forward_from (старый API): прямой User или None при приватности.
    """
    if not message.from_user or not _is_admin(message.from_user.id):
        return  # не-админ — молча игнорируем

    user_id: int | None = None

    # ── Новый API: forward_origin ─────────────────────────────────────────────
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        origin_type = getattr(origin, "type", None)

        if origin_type == "hidden_user":
            await message.answer(
                "Не удалось определить отправителя (приватная пересылка)."
            )
            return

        elif origin_type == "user":
            sender = getattr(origin, "sender_user", None)
            if sender is not None:
                user_id = sender.id
            else:
                # Теоретически невозможно, но обрабатываем аккуратно
                await message.answer(
                    "Не удалось определить отправителя (приватная пересылка)."
                )
                return

        else:
            # MessageOriginChat / MessageOriginChannel — нет личного user_id
            await message.answer(
                "Не удалось определить отправителя "
                "(сообщение переслано из канала или чата)."
            )
            return

    # ── Старый API: forward_from ──────────────────────────────────────────────
    if user_id is None:
        fwd_from = getattr(message, "forward_from", None)
        if fwd_from is not None:
            user_id = fwd_from.id

    # ── Отправитель всё ещё неизвестен ────────────────────────────────────────
    if user_id is None:
        await message.answer(
            "Не удалось определить отправителя (приватная пересылка)."
        )
        return

    await _send_dossier(message, user_id)

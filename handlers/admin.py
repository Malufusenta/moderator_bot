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

Формат досье:
  👤 Пользователь: @username (ID: …)
  📅 В чате с: …
  🔗 Добавил / вступил сам: …
  ✉️ Сообщений всего: …
  ⏱ Первое сообщение: …
  ⏱ Последняя активность: …
  📆 Дней с последней активности: …
  ⚖️ Статус: 🔴 Новичок | 🟢 Старичок   (по стажу: joined_at или first_message_at)
  ✅ Доверенный: Да | Нет                (message_count >= TRUST_LIMIT)
  🛑 Нарушений: N[, последнее <дата>]
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
    return user_id in config.ADMIN_IDS


_TZ_VN = timezone(timedelta(hours=7))  # UTC+7 Вьетнам


def _fmt_ts(ts: int | None) -> str:
    """Преобразовать unix-timestamp в читаемую строку (UTC+7) или 'Нет данных'."""
    if ts is None:
        return "Нет данных"
    dt = datetime.fromtimestamp(ts, tz=_TZ_VN)
    return dt.strftime("%d.%m.%Y %H:%M")


def _seniority_status(profile: dict, now_ts: int) -> str:
    """Статус по стажу: Новичок (<NEWCOMER_DAYS дней) или Старичок.

    База — joined_at; если не известен — first_message_at.
    Оба None → 'Нет данных'.
    Display-only, не влияет на гейт модерации.
    """
    base_ts = profile.get("joined_at") or profile.get("first_message_at")
    if base_ts is None:
        return "Нет данных"
    days = (now_ts - base_ts) // 86_400
    return "🔴 Новичок" if days < config.NEWCOMER_DAYS else "🟢 Старичок"


def _is_trusted_display(profile: dict) -> str:
    """'Да' если message_count >= TRUST_LIMIT, иначе 'Нет'."""
    return "Да" if profile["message_count"] >= config.TRUST_LIMIT else "Нет"


def _format_added_by(profile: dict, added_by_user: dict | None) -> str:
    """Сформировать строку источника появления участника.

    added_by не None  → 'добавил @ник (ID: X)' / 'добавил ID: X'
    invite_link известна → 'по ссылке «Имя»' (из config.INVITE_LINKS) / 'по ссылке'
    оба None          → 'Нет данных'
    """
    added_by_id = profile.get("added_by")
    if added_by_id is not None:
        if added_by_user and added_by_user.get("username"):
            return f"добавил @{added_by_user['username']} (ID: {added_by_id})"
        return f"добавил ID: {added_by_id}"

    invite_link = profile.get("invite_link")
    if invite_link:
        name = config.INVITE_LINKS.get(invite_link)
        if name:
            return f"по ссылке «{name}»"
        return "по ссылке"

    return "Нет данных"


def _format_dossier(profile: dict, now_ts: int, added_by_user: dict | None = None) -> str:
    """Собрать текст досье из профиля пользователя."""
    username  = profile.get("username")
    uid       = profile["user_id"]
    user_line = f"@{username} (ID: {uid})" if username else f"ID: {uid}"

    join_source  = _format_added_by(profile, added_by_user)
    seniority    = _seniority_status(profile, now_ts)
    trusted_str  = _is_trusted_display(profile)

    last = profile.get("last_message_at")
    if last is not None:
        days_ago_str = str((now_ts - last) // 86_400)
    else:
        days_ago_str = "Нет данных"

    violations = profile["ad_attempts"]
    last_viol  = profile.get("last_ad_attempt_at")
    if violations > 0 and last_viol is not None:
        violations_str = f"{violations}, последнее {_fmt_ts(last_viol)}"
    else:
        violations_str = str(violations)

    return (
        f"👤 Пользователь: {user_line}\n"
        f"📅 В чате с: {_fmt_ts(profile.get('joined_at'))}\n"
        f"🔗 Добавил / вступил сам: {join_source}\n"
        f"✉️ Сообщений всего: {profile['message_count']}\n"
        f"⏱ Первое сообщение: {_fmt_ts(profile.get('first_message_at'))}\n"
        f"⏱ Последняя активность: {_fmt_ts(last)}\n"
        f"📆 Дней с последней активности: {days_ago_str}\n"
        f"⚖️ Статус: {seniority}\n"
        f"✅ Доверенный: {trusted_str}\n"
        f"🛑 Нарушений: {violations_str}"
    )


async def _send_dossier(message: Message, user_id: int) -> None:
    """Запросить профиль и отправить досье."""
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
    """/check <user_id|@username> — досье по ID или username."""
    if not message.from_user or not _is_admin(message.from_user.id):
        return

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
        await _send_dossier(message, int(arg))
        return

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
    """Пересланное сообщение — показать досье исходного отправителя."""
    if not message.from_user or not _is_admin(message.from_user.id):
        return

    user_id: int | None = None

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
                await message.answer(
                    "Не удалось определить отправителя (приватная пересылка)."
                )
                return

        else:
            await message.answer(
                "Не удалось определить отправителя "
                "(сообщение переслано из канала или чата)."
            )
            return

    if user_id is None:
        fwd_from = getattr(message, "forward_from", None)
        if fwd_from is not None:
            user_id = fwd_from.id

    if user_id is None:
        await message.answer(
            "Не удалось определить отправителя (приватная пересылка)."
        )
        return

    await _send_dossier(message, user_id)

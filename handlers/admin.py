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
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

import config
from database import queries
from database.db import get_db
from services.pyrogram_client import get_pyrogram

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


def _resolve_invite_link_name(invite_link: str) -> str | None:
    """Найти имя ссылки в config.INVITE_LINKS.

    Сначала точное совпадение, затем префикс-поиск — Telegram Bot API
    возвращает ссылки от других администраторов в усечённом виде:
    «https://t.me/+nWEB7ZFp…» вместо полного «https://t.me/+nWEB7ZFp7m0yZjAy».
    """
    name = config.INVITE_LINKS.get(invite_link)
    if name:
        return name
    # Отрезаем хвост (…, … или ...) и ищем, какая настроенная ссылка
    # начинается с оставшегося префикса.
    prefix = invite_link.rstrip(".…")
    if len(prefix) < 15:  # слишком короткий префикс — не матчим
        return None
    for key, val in config.INVITE_LINKS.items():
        if key.startswith(prefix):
            return val
    return None


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
        name = _resolve_invite_link_name(invite_link)
        if name:
            return name
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

    msg_count = profile.get("message_count") or 0
    buttons = []
    if msg_count > 0 and get_pyrogram() is not None:
        buttons.append(InlineKeyboardButton(
            text=f"✉️ Сообщения ({msg_count})",
            callback_data=f"hist_open:{user_id}",
        ))
    buttons.append(InlineKeyboardButton(
        text="📅 Входы / выходы",
        callback_data=f"events:{user_id}",
    ))
    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])

    await message.answer(_format_dossier(profile, now_ts, added_by_user), reply_markup=keyboard)


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


# ─── /history ────────────────────────────────────────────────────────────────

_HISTORY_PAGE = 10   # сообщений на страницу

# Ссылка на сообщение в супергруппе: убираем префикс -100 из chat_id
_LINK_CHAT_ID = str(config.CHAT_ID).replace("-100", "")


def _msg_link(message_id: int) -> str:
    return f"https://t.me/c/{_LINK_CHAT_ID}/{message_id}"


def _history_keyboard(user_id: int, offset: int, has_more: bool) -> InlineKeyboardMarkup | None:
    """Кнопки пагинации. None если страница единственная."""
    buttons = []
    if offset > 0:
        buttons.append(InlineKeyboardButton(
            text="← Назад",
            callback_data=f"hist:{user_id}:{max(0, offset - _HISTORY_PAGE)}",
        ))
    if has_more:
        buttons.append(InlineKeyboardButton(
            text="Ещё →",
            callback_data=f"hist:{user_id}:{offset + _HISTORY_PAGE}",
        ))
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def _format_history_page(msgs: list, offset: int, user_id: int) -> str:
    """Форматировать страницу истории."""
    lines = [f"📝 Сообщения пользователя <code>{user_id}</code> "
             f"(#{offset + 1}–{offset + len(msgs)}):\n"]
    for msg in msgs:
        date_str = _fmt_ts(int(msg.date.timestamp()))
        text = msg.text or msg.caption or ""
        link = _msg_link(msg.id)
        if text:
            snippet = text[:150] + ("…" if len(text) > 150 else "")
            lines.append(f'🕐 <a href="{link}">{date_str}</a>\n{snippet}\n')
        else:
            lines.append(f'🕐 <a href="{link}">{date_str}</a> — <i>[медиа]</i>\n')
    return "\n".join(lines)


async def _fetch_history(user_id: int, offset: int) -> tuple[list, bool]:
    """Получить страницу + флаг есть ли ещё."""
    pyro = get_pyrogram()
    if pyro is None:
        return [], False
    msgs = []
    # Берём на 1 больше чтобы понять есть ли следующая страница
    async for msg in pyro.search_messages(
        config.CHAT_ID, from_user=user_id,
        limit=_HISTORY_PAGE + 1, offset=offset,
    ):
        msgs.append(msg)
    has_more = len(msgs) > _HISTORY_PAGE
    return msgs[:_HISTORY_PAGE], has_more


async def _resolve_user_id(arg: str) -> int | None:
    """Преобразовать аргумент команды в user_id."""
    if arg.isdigit():
        return int(arg)
    conn = get_db()
    return await queries.find_user_id_by_username(conn, arg)


@router.message(Command("history"))
async def handle_history(message: Message) -> None:
    """/history <user_id|@username> — сообщения пользователя в чате с пагинацией."""
    if not message.from_user or not _is_admin(message.from_user.id):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "Использование:\n"
            "  /history 123456789\n"
            "  /history @username"
        )
        return

    if get_pyrogram() is None:
        await message.answer("⚠️ Pyrogram-сессия не запущена — /history недоступен.")
        return

    user_id = await _resolve_user_id(parts[1].strip().lstrip("@"))
    if user_id is None:
        await message.answer(
            "Пользователь не найден в базе.\n"
            "⚠️ Поиск по username ненадёжен — используйте /history <user_id>."
        )
        return

    wait_msg = await message.answer("🔍 Ищу сообщения...")
    try:
        msgs, has_more = await _fetch_history(user_id, offset=0)
    except Exception as exc:
        logger.warning("history: ошибка поиска: %s", exc)
        await wait_msg.delete()
        await message.answer(f"⚠️ Ошибка при поиске: {exc}")
        return

    await wait_msg.delete()

    if not msgs:
        await message.answer("Сообщений не найдено.")
        return

    await message.answer(
        _format_history_page(msgs, offset=0, user_id=user_id),
        reply_markup=_history_keyboard(user_id, offset=0, has_more=has_more),
    )


@router.callback_query(F.data.startswith("hist_open:"))
async def handle_history_open(callback: CallbackQuery) -> None:
    """Кнопка «Сообщения (N)» в досье — открывает историю новым сообщением."""
    if not callback.from_user or not _is_admin(callback.from_user.id):
        await callback.answer()
        return

    user_id = int(callback.data.split(":")[1])
    await callback.answer("🔍 Загружаю...")

    try:
        msgs, has_more = await _fetch_history(user_id, offset=0)
    except Exception as exc:
        logger.warning("hist_open callback: ошибка: %s", exc)
        await callback.answer(f"Ошибка: {exc}", show_alert=True)
        return

    if not msgs:
        await callback.answer("Сообщений не найдено.", show_alert=True)
        return

    await callback.message.answer(
        _format_history_page(msgs, offset=0, user_id=user_id),
        reply_markup=_history_keyboard(user_id, offset=0, has_more=has_more),
    )


@router.callback_query(F.data.startswith("hist:"))
async def handle_history_page(callback: CallbackQuery) -> None:
    """Пагинация истории через инлайн-кнопки."""
    if not callback.from_user or not _is_admin(callback.from_user.id):
        await callback.answer()
        return

    _, uid_str, offset_str = callback.data.split(":")
    user_id = int(uid_str)
    offset  = int(offset_str)

    await callback.answer("🔍 Загружаю...")
    try:
        msgs, has_more = await _fetch_history(user_id, offset=offset)
    except Exception as exc:
        logger.warning("history callback: ошибка: %s", exc)
        await callback.answer(f"Ошибка: {exc}", show_alert=True)
        return

    if not msgs:
        await callback.answer("Больше сообщений нет.", show_alert=True)
        return

    await callback.message.edit_text(
        _format_history_page(msgs, offset=offset, user_id=user_id),
        reply_markup=_history_keyboard(user_id, offset=offset, has_more=has_more),
    )


@router.callback_query(F.data.startswith("events:"))
async def handle_member_events(callback: CallbackQuery) -> None:
    """Кнопка «Входы / выходы» в досье — показывает историю событий."""
    if not callback.from_user or not _is_admin(callback.from_user.id):
        await callback.answer()
        return

    user_id = int(callback.data.split(":")[1])
    conn    = get_db()
    events  = await queries.get_member_events(conn, user_id)

    await callback.answer()

    if not events:
        await callback.message.answer(
            f"📅 История входов/выходов <code>{user_id}</code>:\n\nСобытий пока нет (бот начал следить недавно)."
        )
        return

    lines = [f"📅 История входов/выходов <code>{user_id}</code>:\n"]
    for ev in events:
        icon = "👋" if ev["event_type"] == "join" else "🚪"
        label = "Вступил" if ev["event_type"] == "join" else "Покинул"
        lines.append(f"{icon} {label} — {_fmt_ts(ev['happened_at'])}")

    await callback.message.answer("\n".join(lines))

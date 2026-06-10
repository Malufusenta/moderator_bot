"""
Отслеживание вступлений в чат в реальном времени (раздел 9 ТЗ).

Фиксирует:
  - кто вступил (user_id, username, joined_at)
  - через какую ссылку (invite_link)
  - кто добавил (added_by): None если вступил сам по ссылке/поиску

Принцип определения added_by:
  ChatMemberUpdated.from_user — инициатор действия.
  Если from_user.id != new_chat_member.user.id → кто-то добавил вручную.
  Если совпадают → пользователь сам нажал «Вступить».
"""

from __future__ import annotations

import logging
from datetime import timezone

from aiogram import F, Router
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter, JOIN_TRANSITION
from aiogram.types import ChatMemberUpdated

import config
from database import queries
from database.db import get_db

logger = logging.getLogger(__name__)

router = Router()


@router.chat_member(
    ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION),
    F.chat.id == config.CHAT_ID,
)
async def on_member_join(event: ChatMemberUpdated) -> None:
    """Зафиксировать нового участника чата."""
    joined_user = event.new_chat_member.user
    actor       = event.from_user

    user_id  = joined_user.id
    username = joined_user.username

    joined_at = int(event.date.replace(tzinfo=timezone.utc).timestamp())

    invite_link: str | None = None
    if event.invite_link is not None:
        invite_link = event.invite_link.invite_link

    # actor == joined_user → сам вступил по ссылке или через поиск
    added_by: int | None = None
    if actor is not None and actor.id != user_id:
        added_by = actor.id

    conn = get_db()
    await queries.record_join(
        conn,
        user_id=user_id,
        username=username,
        joined_at=joined_at,
        invite_link=invite_link,
        added_by=added_by,
    )

    if added_by is not None:
        logger.info(
            "Новый участник user_id=%d (@%s) добавлен пользователем %d",
            user_id, username, added_by,
        )
    else:
        logger.info(
            "Новый участник user_id=%d (@%s) вступил самостоятельно (ссылка: %s)",
            user_id, username, invite_link,
        )

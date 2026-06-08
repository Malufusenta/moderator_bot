# Установить переменные окружения ДО любых импортов, которые загружают config.
# conftest.py обрабатывается раньше тестовых модулей, поэтому os.environ здесь
# будут видны при первом импорте config в тест-файлах.
import os
os.environ.setdefault("BOT_TOKEN",  "0:test_stub_token")
os.environ.setdefault("API_ID",     "12345")
os.environ.setdefault("API_HASH",   "testhashstub")
os.environ.setdefault("CHAT_ID",    "-100123456789")
os.environ.setdefault("ADMIN_IDS",  "")
os.environ.setdefault("WHITELIST_IDS", "")

from unittest.mock import MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest

import config
import database.db as db_module
import utils.scheduler as scheduler_module

# ─── Константы, удобные в тестах ──────────────────────────────────────────────

TEST_CHAT_ID: int = int(os.environ["CHAT_ID"])
DAY: int = 86_400  # секунд в сутках


# ─── Фейковые объекты ─────────────────────────────────────────────────────────

class FakeUser:
    def __init__(self, uid: int, username: str | None = None, is_bot: bool = False):
        self.id = uid
        self.username = username
        self.full_name = f"User{uid}"
        self.is_bot = is_bot


class FakeChat:
    def __init__(self, chat_id: int = TEST_CHAT_ID):
        self.id = chat_id


class FakeMessage:
    """Минимальный фейк aiogram.types.Message.

    Поля соответствуют тому, что реально читает handle_post / on_message:
      from_user (.id, .username, .is_bot, .full_name)
      chat       (.id)
      text, caption
      photo      — непустой список (truthy) или None
      video      — truthy-объект или None
      animation  — truthy-объект или None
      message_id
      media_group_id
    """

    def __init__(
        self,
        *,
        user_id: int,
        username: str | None = None,
        text: str | None = None,
        caption: str | None = None,
        photo: bool = False,
        video: bool = False,
        animation: bool = False,
        message_id: int = 1,
        media_group_id: str | None = None,
        chat_id: int = TEST_CHAT_ID,
    ):
        self.from_user = FakeUser(user_id, username)
        self.chat = FakeChat(chat_id)
        self.text = text
        self.caption = caption
        # aiogram возвращает list[PhotoSize]; для теста нужен truthy объект
        self.photo = [MagicMock()] if photo else None
        self.video  = MagicMock() if video  else None
        self.animation = MagicMock() if animation else None
        self.message_id = message_id
        self.media_group_id = media_group_id
        self.bot = None  # handle_post получает bot явным аргументом, не через message


class _SentMsg:
    """Фейк ответа bot.send_message: только message_id нужен scheduler'у."""
    message_id = 9_999


class FakeBot:
    """Записывает все обращения к Telegram API в списки; не шлёт реальных запросов.

    mute_raises=True: restrict_chat_member бросит TelegramBadRequest,
    имитируя ситуацию «нарушитель — администратор чата» (раздел 8 ТЗ).
    """

    def __init__(self, *, mute_raises: bool = False):
        self.deleted:    list[int] = []   # message_id каждого вызова delete_message
        self.restricted: list[int] = []   # user_id каждого успешного restrict
        self.sent:       list[str] = []   # текст каждого send_message
        self.mute_raises = mute_raises

    # ── API методы ────────────────────────────────────────────────────────────

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        # handle_post: await bot.delete_message(chat_id, m.message_id)  (позиционно)
        self.deleted.append(message_id)

    async def restrict_chat_member(
        self, chat_id=None, user_id=None, permissions=None, until_date=None, **kw
    ) -> None:
        # actions.mute_user: await bot.restrict_chat_member(chat_id=…, user_id=…, …)
        if self.mute_raises:
            raise TelegramBadRequest(
                MagicMock(), "Bad Request: can't restrict chat administrator"
            )
        self.restricted.append(user_id)

    async def send_message(self, chat_id=None, text=None, **kw) -> _SentMsg:
        # actions.warn: await bot.send_message(chat_id=…, text=…)
        self.sent.append(text)
        return _SentMsg()


# ─── pytest-фикстуры ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def no_scheduler(monkeypatch):
    """schedule_delete → no-op.

    Без этой замены тест запустит asyncio.sleep(WARNING_DELETE_SECONDS=15) и
    будет висеть 15 секунд. Логику автоудаления предупреждений тестируем
    отдельно, если нужно; здесь она не в фокусе.
    """
    monkeypatch.setattr(scheduler_module, "schedule_delete", lambda *a, **kw: None)


@pytest.fixture
async def tmp_db(monkeypatch, tmp_path):
    """Изолированная SQLite БД во временном файле.

    Монкейпатчит config.DB_PATH, вызывает init_db() и возвращает соединение.
    После теста вызывает close_db() — сбрасывает модульный _conn в None,
    чтобы следующий тест получил чистое соединение к своей БД.
    """
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_file))
    await db_module.init_db()
    yield db_module.get_db()
    await db_module.close_db()


@pytest.fixture
def fake_bot() -> FakeBot:
    return FakeBot()


@pytest.fixture
def fake_bot_mute_fails() -> FakeBot:
    return FakeBot(mute_raises=True)


@pytest.fixture
def reset_albums(monkeypatch):
    """Сбросить модульное состояние сборщика альбомов между тестами.

    monkeypatch.setattr восстановит оригинальные значения после теста.
    """
    import handlers.messages as m
    monkeypatch.setattr(m, "_albums",              {})
    monkeypatch.setattr(m, "_album_bots",          {})
    monkeypatch.setattr(m, "_album_tasks",         {})
    monkeypatch.setattr(m, "_pending_album_tasks", set())

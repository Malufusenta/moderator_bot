# Установить переменные окружения ДО любых импортов, которые загружают config.
# conftest.py обрабатывается раньше тестовых модулей, поэтому os.environ здесь
# будут видны при первом импорте config в тест-файлах.
import os
# ── Telegram ──────────────────────────────────────────────────────────────────
os.environ.setdefault("BOT_TOKEN",  "0:test_stub_token")
os.environ.setdefault("API_ID",     "12345")
os.environ.setdefault("API_HASH",   "testhashstub")
os.environ.setdefault("CHAT_ID",    "-100123456789")
os.environ.setdefault("ADMIN_IDS",  "")
os.environ.setdefault("WHITELIST_IDS", "")
# ── Пороги (изолируем тесты от .env — значения должны быть предсказуемы) ──────
os.environ.setdefault("TRUST_LIMIT",          "30")
os.environ.setdefault("NEWCOMER_DAYS",        "30")
os.environ.setdefault("RECENCY_DAYS",         "60")
os.environ.setdefault("RECENCY_MIN_MESSAGES", "1")
os.environ.setdefault("SIMILARITY_THRESHOLD", "0.85")
os.environ.setdefault("MUTE_HOURS",           "24")
os.environ.setdefault("WARNING_DELETE_SECONDS", "15")
os.environ.setdefault("DRY_RUN",              "false")
os.environ.setdefault("LOG_CHAT_ID",          "0")

from unittest.mock import MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest

import config
import database.db as db_module
import utils.scheduler as scheduler_module

# ─── Константы, удобные в тестах ──────────────────────────────────────────────

TEST_CHAT_ID: int = int(os.environ["CHAT_ID"])
# ID тестового лог-чата; используется в fake_bot_with_log
TEST_LOG_CHAT_ID: int = -9_999_999
DAY: int = 86_400  # секунд в сутках


# ─── Фейковые объекты ─────────────────────────────────────────────────────────

class FakeUser:
    def __init__(self, uid: int, username: str | None = None, is_bot: bool = False):
        self.id = uid
        self.username = username
        self.full_name = f"User{uid}"
        self.is_bot = is_bot


class FakeChat:
    def __init__(self, chat_id: int = TEST_CHAT_ID, chat_type: str = "supergroup"):
        self.id   = chat_id
        self.type = chat_type


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
        chat_type: str = "supergroup",
    ):
        self.from_user = FakeUser(user_id, username)
        self.chat = FakeChat(chat_id, chat_type)
        self.text = text
        self.caption = caption
        # aiogram возвращает list[PhotoSize]; для теста нужен truthy объект
        self.photo = [MagicMock()] if photo else None
        self.video  = MagicMock() if video  else None
        self.animation = MagicMock() if animation else None
        self.message_id = message_id
        self.media_group_id = media_group_id
        # forward-поля (None по умолчанию; задаются в admin-тестах)
        self.forward_origin = None
        self.forward_from   = None
        self.forward_date   = None
        # bot: None для handle_post (принимает bot явным аргументом);
        # FakeBot — для admin-хендлеров, которые используют message.answer()
        self.bot: "FakeBot | None" = None

    async def answer(self, text: str | None = None, **kw) -> "_SentMsg":
        """Имитирует message.answer() — пишет в self.bot.sent если бот задан."""
        if self.bot is not None:
            self.bot.sent.append(text)
        return _SentMsg()


class _SentMsg:
    """Фейк ответа bot.send_message: только message_id нужен scheduler'у."""
    message_id = 9_999


class FakeBot:
    """Записывает все обращения к Telegram API в списки; не шлёт реальных запросов.

    mute_raises=True: restrict_chat_member бросит TelegramBadRequest,
    имитируя ситуацию «нарушитель — администратор чата» (раздел 8 ТЗ).

    log_chat_id: если задан, send_message на этот chat_id идёт в self.log_sent
    вместо self.sent — позволяет тестировать admin-лог отдельно от warn.
    """

    def __init__(self, *, mute_raises: bool = False, log_chat_id: int = 0):
        self.deleted:    list[int] = []   # message_id каждого вызова delete_message
        self.restricted: list[int] = []   # user_id каждого успешного restrict
        self.sent:       list[str] = []   # текст warn/ответов (не лог-чат)
        self.log_sent:   list[str] = []   # текст сообщений в лог-чат
        self.mute_raises = mute_raises
        self._log_chat_id = log_chat_id

    # ── API методы ────────────────────────────────────────────────────────────

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        self.deleted.append(message_id)

    async def restrict_chat_member(
        self, chat_id=None, user_id=None, permissions=None, until_date=None, **kw
    ) -> None:
        if self.mute_raises:
            raise TelegramBadRequest(
                MagicMock(), "Bad Request: can't restrict chat administrator"
            )
        self.restricted.append(user_id)

    async def send_message(self, chat_id=None, text=None, **kw) -> _SentMsg:
        if self._log_chat_id and chat_id == self._log_chat_id:
            self.log_sent.append(text)
        else:
            self.sent.append(text)
        return _SentMsg()


# ─── pytest-фикстуры ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def no_scheduler(monkeypatch):
    """schedule_delete → no-op."""
    monkeypatch.setattr(scheduler_module, "schedule_delete", lambda *a, **kw: None)


@pytest.fixture
async def tmp_db(monkeypatch, tmp_path):
    """Изолированная SQLite БД во временном файле."""
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
def fake_bot_with_log(monkeypatch) -> FakeBot:
    """FakeBot с включённым лог-чатом (LOG_CHAT_ID = TEST_LOG_CHAT_ID)."""
    monkeypatch.setattr(config, "LOG_CHAT_ID", TEST_LOG_CHAT_ID)
    return FakeBot(log_chat_id=TEST_LOG_CHAT_ID)


@pytest.fixture
def fake_bot_mute_fails_with_log(monkeypatch) -> FakeBot:
    """FakeBot: мут падает + лог-чат включён."""
    monkeypatch.setattr(config, "LOG_CHAT_ID", TEST_LOG_CHAT_ID)
    return FakeBot(mute_raises=True, log_chat_id=TEST_LOG_CHAT_ID)


@pytest.fixture
def reset_albums(monkeypatch):
    """Сбросить модульное состояние сборщика альбомов между тестами."""
    import handlers.messages as m
    monkeypatch.setattr(m, "_albums",              {})
    monkeypatch.setattr(m, "_album_bots",          {})
    monkeypatch.setattr(m, "_album_tasks",         {})
    monkeypatch.setattr(m, "_pending_album_tasks", set())

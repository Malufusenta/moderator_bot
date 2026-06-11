"""
Тесты логики handle_post (раздел 6 ТЗ).

Каждый тест вызывает handle_post([...], bot) напрямую, минуя диспетчер aiogram
и сборщик альбомов. Это позволяет проверять бизнес-логику без реального бота.

Зависимости:
  - tmp_db:     временная SQLite БД (из conftest); handle_post читает get_db()
  - fake_bot:   FakeBot, пишущий вызовы в списки
  - no_scheduler (autouse): schedule_delete → no-op
"""

import time

import pytest

import config
from database import queries
from handlers.messages import handle_post
from moderation.duplicates import save_if_new
from tests.conftest import DAY, FakeMessage, TEST_LOG_CHAT_ID

# ─── Вспомогательная функция засева БД ────────────────────────────────────────

async def seed_user(
    conn,
    user_id: int,
    message_count: int,
    last_active_days_ago: int = 5,
    username: str = "user",
) -> None:
    """Вставить пользователя с готовыми счётчиками минуя upsert (чтобы не инкрементировать)."""
    now = int(time.time())
    await conn.execute(
        "INSERT INTO users (user_id, username, message_count, last_message_at) "
        "VALUES (?, ?, ?, ?)",
        (user_id, username, message_count, now - last_active_days_ago * DAY),
    )
    await conn.commit()


# ─── Тесты ────────────────────────────────────────────────────────────────────

async def test_newbie_ad_deleted_and_muted(tmp_db, fake_bot):
    """T1: новичок (нет в БД) постит фото + стоп-слово.

    Ожидается: 1 delete, 1 mute, 1 warn, ad_attempts=1, message_count=0,
               ни одной записи в advertisements.
    """
    msg = FakeMessage(
        user_id=1001, username="ghost",
        photo=True, caption="Продам велосипед срочно",
        message_id=10,
    )
    await handle_post([msg], fake_bot)

    assert fake_bot.deleted    == [10],  "Сообщение должно быть удалено"
    assert len(fake_bot.restricted) == 1, "Мут должен сработать"
    assert len(fake_bot.sent)       == 1, "Предупреждение должно быть отправлено"
    assert "активным участникам" in fake_bot.sent[0]

    row = await queries.get_user(tmp_db, 1001)
    assert row is not None,            "Запись пользователя должна быть создана"
    assert row["ad_attempts"]    == 1, "ad_attempts должен быть 1"
    assert row["message_count"]  == 0, "message_count НЕ должен расти (удалённое ≠ активность)"
    assert row["last_ad_attempt_at"] is not None, "last_ad_attempt_at должен быть проставлен"

    ads = await queries.get_user_ads(tmp_db, 1001)
    assert ads == [],                  "advertisements НЕ должны создаваться"


async def test_newbie_album_all_parts_deleted(tmp_db, fake_bot):
    """T2: новичок постит альбом из 3 фото, стоп-слово в caption первого.

    handle_post вызывается сразу со всеми 3 сообщениями (сборщик альбомов
    тестируется отдельно). Ожидается: 3 delete, ровно 1 mute, 1 warn.
    """
    mgid = "album_1"
    msgs = [
        FakeMessage(user_id=1002, photo=True, caption="Продам диван", message_id=1, media_group_id=mgid),
        FakeMessage(user_id=1002, photo=True, message_id=2, media_group_id=mgid),
        FakeMessage(user_id=1002, photo=True, message_id=3, media_group_id=mgid),
    ]
    await handle_post(msgs, fake_bot)

    assert sorted(fake_bot.deleted) == [1, 2, 3], "Все 3 части альбома должны быть удалены"
    assert len(fake_bot.restricted) == 1,          "Мут должен быть ровно один"
    assert len(fake_bot.sent)       == 1,           "Предупреждение должно быть одно"

    row = await queries.get_user(tmp_db, 1002)
    assert row["ad_attempts"]   == 1
    assert row["message_count"] == 0

    ads = await queries.get_user_ads(tmp_db, 1002)
    assert ads == []


async def test_trusted_new_ad_saved(tmp_db, fake_bot):
    """T3: доверенный (count=40, активен 5 дней назад) постит новое объявление.

    Ожидается: 0 delete, 0 mute, 1 запись в advertisements,
               message_count=41, 1 строка в messages.
    """
    await seed_user(tmp_db, 1003, message_count=40, last_active_days_ago=5)

    msg = FakeMessage(user_id=1003, photo=True, caption="Сдам квартиру", message_id=20)
    await handle_post([msg], fake_bot)

    assert fake_bot.deleted     == [], "Сообщение доверенного НЕ должно удаляться"
    assert fake_bot.restricted  == [], "Мут не нужен"
    assert fake_bot.sent        == [], "Предупреждение не нужно"

    row = await queries.get_user(tmp_db, 1003)
    assert row["message_count"] == 41, "message_count должен инкрементнуться"

    cur = await tmp_db.execute("SELECT COUNT(*) FROM messages WHERE user_id=1003")
    assert (await cur.fetchone())[0] == 1, "Строка в messages должна появиться"

    ads = await queries.get_user_ads(tmp_db, 1003)
    assert len(ads) == 1, "Объявление должно быть сохранено в advertisements"


async def test_trusted_duplicate_deleted(tmp_db, fake_bot):
    """T4: доверенный повторно постит то же объявление → дубль.

    Ожидается: 1 delete, 1 mute, warn с «повторная публикация»,
               ad_attempts=1, message_count НЕ изменился (ещё 40).
    """
    await seed_user(tmp_db, 1003, message_count=40, last_active_days_ago=5)
    # Засеять первое объявление в advertisements
    await save_if_new(1003, "Сдам квартиру", int(time.time()))

    msg = FakeMessage(user_id=1003, photo=True, caption="Сдам квартиру", message_id=21)
    await handle_post([msg], fake_bot)

    assert fake_bot.deleted    == [21], "Дубль должен быть удалён"
    assert len(fake_bot.restricted) == 1, "Мут за дубль"
    assert len(fake_bot.sent)  == 1
    assert "повторная публикация" in fake_bot.sent[0]

    row = await queries.get_user(tmp_db, 1003)
    assert row["ad_attempts"]   == 1
    assert row["message_count"] == 40, "message_count НЕ должен расти при дубле"

    ads = await queries.get_user_ads(tmp_db, 1003)
    assert len(ads) == 1, "Вторая запись в advertisements НЕ должна создаваться"


async def test_trusted_different_ad_saved(tmp_db, fake_bot):
    """T5: доверенный постит другое объявление после первого.

    Ожидается: 0 delete, в advertisements 2 записи, message_count инкрементнут.
    """
    await seed_user(tmp_db, 1003, message_count=40, last_active_days_ago=5)
    await save_if_new(1003, "Сдам квартиру", int(time.time()))

    msg = FakeMessage(user_id=1003, photo=True, caption="Продам диван", message_id=22)
    await handle_post([msg], fake_bot)

    assert fake_bot.deleted    == [], "Новое объявление НЕ удаляется"
    assert fake_bot.restricted == []

    ads = await queries.get_user_ads(tmp_db, 1003)
    assert len(ads) == 2, "Должно быть 2 объявления в advertisements"

    row = await queries.get_user(tmp_db, 1003)
    assert row["message_count"] == 41


async def test_plain_text_no_media_recorded(tmp_db, fake_bot):
    """T6: обычное текстовое сообщение без медиа.

    Ожидается: 0 delete, 0 mute, message_count +1, строка в messages.
    """
    await seed_user(tmp_db, 1004, message_count=10, last_active_days_ago=2)

    msg = FakeMessage(user_id=1004, text="Добрый вечер, соседи!", message_id=30)
    await handle_post([msg], fake_bot)

    assert fake_bot.deleted    == []
    assert fake_bot.restricted == []
    assert fake_bot.sent       == []

    row = await queries.get_user(tmp_db, 1004)
    assert row["message_count"] == 11

    cur = await tmp_db.execute("SELECT COUNT(*) FROM messages WHERE user_id=1004")
    assert (await cur.fetchone())[0] == 1


async def test_sleeping_user_treated_as_untrusted(tmp_db, fake_bot):
    """T7: «спящий» (count=200, но last_active=70 дней назад).

    Стражевой тест контракта «is_trusted вызывается ДО upsert_user»:
    если бы upsert шёл первым, last_message_at стал бы = now и спящий
    прошёл бы проверку свежести как доверенный.

    Ожидается: трактуется как НЕДОВЕРЕННЫЙ → 1 delete, mute, warn;
               message_count остался 200 (не уpsert-нут).
    """
    await seed_user(tmp_db, 1005, message_count=200, last_active_days_ago=70)

    msg = FakeMessage(user_id=1005, photo=True, caption="Продам велосипед", message_id=40)
    await handle_post([msg], fake_bot)

    assert fake_bot.deleted    == [40], "Спящий должен получить удаление"
    assert len(fake_bot.restricted) == 1, "Спящий должен быть замьючен"
    assert len(fake_bot.sent)  == 1

    row = await queries.get_user(tmp_db, 1005)
    assert row["message_count"] == 200, "message_count спящего НЕ должен измениться"
    assert row["ad_attempts"]   == 1


async def test_whitelisted_user_bypasses_all_checks(tmp_db, fake_bot, monkeypatch):
    """T8: пользователь в WHITELIST_IDS → байпас всех проверок.

    Ожидается: 0 delete, 0 mute, message_count +1, НИ ОДНОЙ записи
               в advertisements (вайтлист обходит и анти-дубль).
    """
    monkeypatch.setattr(config, "WHITELIST_IDS", [5555])

    msg = FakeMessage(user_id=5555, photo=True, caption="Продам дачу", message_id=50)
    await handle_post([msg], fake_bot)

    assert fake_bot.deleted    == [], "Вайтлист-пользователь НЕ удаляется"
    assert fake_bot.restricted == [], "Мут не нужен"

    row = await queries.get_user(tmp_db, 5555)
    assert row is not None
    assert row["message_count"] == 1, "Активность должна записываться"

    ads = await queries.get_user_ads(tmp_db, 5555)
    assert ads == [], "save_if_new НЕ вызывается для вайтлиста"


async def test_mute_failure_does_not_stop_warn(tmp_db, fake_bot_mute_fails):
    """T9: restrict_chat_member бросает TelegramBadRequest (нарушитель — админ).

    Ожидается: delete всё равно произошёл; mute вернул False (restricted пуст);
               warn всё равно отправлен; ad_attempts=1.
    """
    msg = FakeMessage(
        user_id=1006, photo=True, caption="Продам велосипед", message_id=60
    )
    await handle_post([msg], fake_bot_mute_fails)

    assert fake_bot_mute_fails.deleted == [60], "Удаление должно быть выполнено"
    assert fake_bot_mute_fails.restricted == [], "restrict_chat_member упал → список пуст"
    assert len(fake_bot_mute_fails.sent) == 1,  "Warn должен быть отправлен несмотря на мут"

    row = await queries.get_user(tmp_db, 1006)
    assert row["ad_attempts"]   == 1
    assert row["message_count"] == 0


async def test_dry_run_no_actions_but_db_written(tmp_db, fake_bot, monkeypatch, caplog):
    """T10: DRY_RUN=True — карательные действия пропускаются, БД пишется как обычно.

    Новичок (нет в БД) постит фото + стоп-слово.
    Ожидается:
      - fake_bot.deleted == [] и fake_bot.restricted == [] (delete/mute не вызваны)
      - fake_bot.sent == []                               (warn не вызван)
      - ad_attempts == 1                                  (инкремент выполнен)
      - message_count == 0                                (удалённое не активность)
      - В логе присутствует запись [DRY_RUN] с user_id и причиной
    """
    import logging
    import config as cfg

    monkeypatch.setattr(cfg, "DRY_RUN", True)

    msg = FakeMessage(
        user_id=1007, username="dryuser",
        photo=True, caption="Продам велосипед",
        message_id=70,
    )

    with caplog.at_level(logging.INFO, logger="handlers.messages"):
        await handle_post([msg], fake_bot)

    # Карательных действий нет
    assert fake_bot.deleted    == [], "DRY_RUN: удалений быть не должно"
    assert fake_bot.restricted == [], "DRY_RUN: мутов быть не должно"
    assert fake_bot.sent       == [], "DRY_RUN: предупреждений быть не должно"

    # Счётчики написаны как в боевом режиме
    row = await queries.get_user(tmp_db, 1007)
    assert row is not None
    assert row["ad_attempts"]   == 1, "ad_attempts должен инкрементиться даже в DRY_RUN"
    assert row["message_count"] == 0, "message_count не растёт (объявление не активность)"

    # Лог содержит [DRY_RUN] и user_id
    dry_records = [r for r in caplog.records if "[DRY_RUN]" in r.message]
    assert dry_records, "В логе должна быть хотя бы одна запись [DRY_RUN]"
    assert "1007" in dry_records[0].message, "user_id должен быть в лог-записи"
    assert "недоверенный" in dry_records[0].message, "причина должна быть в лог-записи"


# ─── Тесты admin-лога ─────────────────────────────────────────────────────────

async def test_log_untrusted_sends_to_log_chat(tmp_db, fake_bot_with_log):
    """T-LOG1: новичок постит объявление → лог уходит в LOG_CHAT_ID."""
    msg = FakeMessage(
        user_id=2001, username="spammer",
        photo=True, caption="Продам велосипед", message_id=100,
    )
    await handle_post([msg], fake_bot_with_log)

    assert len(fake_bot_with_log.log_sent) == 1, "Должно быть одно лог-сообщение"
    log_text = fake_bot_with_log.log_sent[0]
    assert "2001" in log_text,          "user_id должен быть в логе"
    assert "недоверенный" in log_text,  "причина должна быть в логе"
    assert "🔇" in log_text,            "Эмодзи мута должен быть в логе"
    # warn-сообщение уходит в чат, не в лог
    assert len(fake_bot_with_log.sent) == 1


async def test_log_duplicate_sends_to_log_chat(tmp_db, fake_bot_with_log):
    """T-LOG2: доверенный постит дубль → лог с пометкой «дубль»."""
    await seed_user(tmp_db, 2002, message_count=40, last_active_days_ago=5)
    await save_if_new(2002, "Продам диван", int(time.time()))

    msg = FakeMessage(
        user_id=2002, username="trusted_dup",
        photo=True, caption="Продам диван", message_id=101,
    )
    await handle_post([msg], fake_bot_with_log)

    assert len(fake_bot_with_log.log_sent) == 1
    log_text = fake_bot_with_log.log_sent[0]
    assert "2002" in log_text
    assert "дубль" in log_text
    assert "🔁" in log_text


async def test_log_disabled_when_log_chat_id_zero(tmp_db, fake_bot):
    """T-LOG3: LOG_CHAT_ID=0 (дефолт) → log_action ничего не шлёт."""
    # fake_bot создан без log_chat_id — LOG_CHAT_ID остаётся 0 из conftest setdefault
    msg = FakeMessage(
        user_id=2003,
        photo=True, caption="Продам велосипед", message_id=102,
    )
    await handle_post([msg], fake_bot)

    assert fake_bot.log_sent == [], "При LOG_CHAT_ID=0 лог-сообщений быть не должно"
    assert len(fake_bot.sent) == 1, "warn в чат должен уйти как обычно"


async def test_log_dry_run_has_prefix_no_actions(tmp_db, fake_bot_with_log, monkeypatch):
    """T-LOG4: DRY_RUN + LOG_CHAT_ID задан → лог с [DRY-RUN], delete/mute/warn не вызывались."""
    monkeypatch.setattr(config, "DRY_RUN", True)

    msg = FakeMessage(
        user_id=2004, username="dryspammer",
        photo=True, caption="Продам велосипед", message_id=103,
    )
    await handle_post([msg], fake_bot_with_log)

    # Карательных действий нет
    assert fake_bot_with_log.deleted    == []
    assert fake_bot_with_log.restricted == []
    assert fake_bot_with_log.sent       == [], "warn не должен уйти в DRY_RUN"

    # Лог с пометкой
    assert len(fake_bot_with_log.log_sent) == 1
    log_text = fake_bot_with_log.log_sent[0]
    assert "[DRY-RUN]" in log_text
    assert "2004" in log_text
    assert "недоверенный" in log_text


async def test_log_mute_failure_logged(tmp_db, fake_bot_mute_fails_with_log):
    """T-LOG5: мут упал (нарушитель — админ) → в лог уходит «⚠️ Не удалось замьютить»."""
    msg = FakeMessage(
        user_id=2005, username="adminspammer",
        photo=True, caption="Продам велосипед", message_id=104,
    )
    await handle_post([msg], fake_bot_mute_fails_with_log)

    assert fake_bot_mute_fails_with_log.deleted == [104]
    assert len(fake_bot_mute_fails_with_log.log_sent) == 1
    log_text = fake_bot_mute_fails_with_log.log_sent[0]
    assert "⚠️" in log_text
    assert "замьютить" in log_text
    assert "2005" in log_text


# ─── Разделение стоп-слов: с медиа / без медиа ────────────────────────────────

async def test_media_word_without_media_is_ignored(tmp_db, fake_bot):
    """Слово «продам» (группа MEDIA) без медиа → не объявление, считается активностью."""
    msg = FakeMessage(
        user_id=3001, username="user3001",
        text="Продам диван",
    )
    await handle_post([msg], fake_bot)
    assert fake_bot.deleted == []
    assert fake_bot.restricted == []
    assert fake_bot.sent == []


async def test_media_word_with_media_is_ad(tmp_db, fake_bot):
    """Слово «продам» (группа MEDIA) + медиа → объявление → удалено + мут/warn."""
    msg = FakeMessage(
        user_id=3002, username="user3002",
        photo=True, caption="Продам диван", message_id=200,
    )
    await handle_post([msg], fake_bot)
    assert fake_bot.deleted == [200]
    assert fake_bot.restricted != [] or fake_bot.sent != []


async def test_text_word_without_media_is_ad(tmp_db, fake_bot):
    """Слово «сдаётся» (группа TEXT) без медиа → объявление."""
    msg = FakeMessage(
        user_id=3003, username="user3003",
        text="Сдаётся комната в районе Митино",
    )
    await handle_post([msg], fake_bot)
    # недоверенный → мут или warn
    assert fake_bot.restricted != [] or fake_bot.sent != []


async def test_peresd_word_without_media_is_ad(tmp_db, fake_bot):
    """Слово «пересдаю» (группа TEXT) без медиа → объявление."""
    msg = FakeMessage(
        user_id=3004, username="user3004",
        text="Пересдаю квартиру, срочно",
    )
    await handle_post([msg], fake_bot)
    assert fake_bot.restricted != [] or fake_bot.sent != []

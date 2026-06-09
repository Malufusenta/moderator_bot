"""
Dev-утилита для подготовки состояний БД при ручном тестировании.

⚠️  Только для разработки — не импортируется в production-код.

Подкоманды
----------
  python seed.py trusted  USER_ID [USERNAME]
      Доверенный участник: count выше порога, последнее сообщение — сейчас.

  python seed.py dormant  USER_ID [USERNAME]
      «Спящий»: высокий count, но последняя активность — за пределами окна свежести.

  python seed.py newbie   USER_ID [USERNAME]
      Новичок: count=1, последнее сообщение — сейчас.

  python seed.py custom   USER_ID --count N --days-ago D [--username U] [--ad-attempts A]
      Произвольные параметры. --ad-attempts требует отдельного UPDATE.

  python seed.py show
      Вывести таблицу users в читаемом виде + число объявлений.

  python seed.py reset [--yes]
      Очистить все данные (структуру таблиц не трогать).
      Без --yes — запрашивает подтверждение.

Все пороги берутся из config (TRUST_LIMIT, RECENCY_DAYS) — не хардкодятся.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone

import config
from database.db import close_db, get_db, init_db
from database import queries


# ─── Вспомогательные функции ──────────────────────────────────────────────────

def _fmt_ts(ts: int | None) -> str:
    """Unix-timestamp → 'YYYY-MM-DD HH:MM UTC' или '—'."""
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _now() -> int:
    return int(time.time())


# ─── Команды ──────────────────────────────────────────────────────────────────

async def cmd_trusted(user_id: int, username: str | None) -> None:
    """Засеять доверенного участника."""
    now = _now()
    conn = get_db()
    await queries.save_parsed_stats(
        conn,
        user_id          = user_id,
        username         = username,
        message_count    = config.TRUST_LIMIT + 10,
        first_message_at = now - 30 * 86_400,
        last_message_at  = now,
    )
    await conn.commit()
    print(
        f"✅ trusted  user_id={user_id}  username={username!r}\n"
        f"   message_count={config.TRUST_LIMIT + 10}  "
        f"last_active={_fmt_ts(now)}"
    )


async def cmd_dormant(user_id: int, username: str | None) -> None:
    """Засеять «спящего» участника (высокий count, протухшая свежесть)."""
    now = _now()
    last_ts  = now - (config.RECENCY_DAYS + 10) * 86_400   # гарантированно вне окна
    first_ts = now - 200 * 86_400
    conn = get_db()
    await queries.save_parsed_stats(
        conn,
        user_id          = user_id,
        username         = username,
        message_count    = config.TRUST_LIMIT + 170,
        first_message_at = first_ts,
        last_message_at  = last_ts,
    )
    await conn.commit()
    print(
        f"✅ dormant  user_id={user_id}  username={username!r}\n"
        f"   message_count={config.TRUST_LIMIT + 170}  "
        f"last_active={_fmt_ts(last_ts)}  "
        f"(окно истекло {config.RECENCY_DAYS + 10} дней назад)"
    )


async def cmd_newbie(user_id: int, username: str | None) -> None:
    """Засеять новичка."""
    now = _now()
    conn = get_db()
    await queries.save_parsed_stats(
        conn,
        user_id          = user_id,
        username         = username,
        message_count    = 1,
        first_message_at = now,
        last_message_at  = now,
    )
    await conn.commit()
    print(
        f"✅ newbie   user_id={user_id}  username={username!r}\n"
        f"   message_count=1"
    )


async def cmd_custom(
    user_id:    int,
    count:      int,
    days_ago:   int,
    username:   str | None,
    ad_attempts: int | None,
) -> None:
    """Засеять произвольные параметры."""
    now     = _now()
    last_ts = now - days_ago * 86_400
    conn    = get_db()
    await queries.save_parsed_stats(
        conn,
        user_id          = user_id,
        username         = username,
        message_count    = count,
        first_message_at = last_ts,
        last_message_at  = last_ts,
    )
    if ad_attempts is not None:
        # save_parsed_stats не трогает ad_attempts — отдельный UPDATE
        await conn.execute(
            "UPDATE users SET ad_attempts = ? WHERE user_id = ?",
            (ad_attempts, user_id),
        )
    await conn.commit()
    print(
        f"✅ custom   user_id={user_id}  username={username!r}\n"
        f"   message_count={count}  last_active={_fmt_ts(last_ts)}"
        + (f"  ad_attempts={ad_attempts}" if ad_attempts is not None else "")
    )


async def cmd_show() -> None:
    """Вывести содержимое таблицы users."""
    conn = get_db()
    cur = await conn.execute(
        """
        SELECT
            u.user_id,
            u.username,
            u.message_count,
            u.first_message_at,
            u.last_message_at,
            u.ad_attempts,
            COUNT(a.id) AS ad_count
        FROM users u
        LEFT JOIN advertisements a ON a.user_id = u.user_id
        GROUP BY u.user_id
        ORDER BY u.last_message_at DESC NULLS LAST
        """
    )
    rows = await cur.fetchall()

    if not rows:
        print("(таблица users пуста)")
        return

    # Ширины колонок
    col_w = [10, 16, 7, 20, 20, 5, 4]
    headers = ["user_id", "username", "count", "first_msg", "last_msg", "ad_att", "ads"]
    sep = "  ".join("-" * w for w in col_w)

    def _row_line(r) -> str:
        return "  ".join([
            str(r["user_id"]).ljust(col_w[0]),
            (r["username"] or "—").ljust(col_w[1]),
            str(r["message_count"]).rjust(col_w[2]),
            _fmt_ts(r["first_message_at"]).ljust(col_w[3]),
            _fmt_ts(r["last_message_at"]).ljust(col_w[4]),
            str(r["ad_attempts"]).rjust(col_w[5]),
            str(r["ad_count"]).rjust(col_w[6]),
        ])

    print("  ".join(h.ljust(col_w[i]) for i, h in enumerate(headers)))
    print(sep)
    for row in rows:
        print(_row_line(row))
    print(f"\n  Итого: {len(rows)} пользователей")

    # Контекст относительно порогов
    now    = _now()
    cutoff = now - config.RECENCY_DAYS * 86_400
    trusted = sum(
        1 for r in rows
        if r["message_count"] >= config.TRUST_LIMIT
        and r["last_message_at"] is not None
        and r["last_message_at"] >= cutoff
    )
    dormant = sum(
        1 for r in rows
        if r["message_count"] >= config.TRUST_LIMIT
        and (r["last_message_at"] is None or r["last_message_at"] < cutoff)
    )
    newbies = sum(1 for r in rows if r["message_count"] < config.TRUST_LIMIT)
    print(
        f"  TRUST_LIMIT={config.TRUST_LIMIT}  RECENCY_DAYS={config.RECENCY_DAYS}\n"
        f"  Доверенных: {trusted}  Спящих: {dormant}  Новичков: {newbies}"
    )


async def cmd_reset(yes: bool) -> None:
    """Очистить все данные (структуру не трогать)."""
    if not yes:
        answer = input("Удалить все данные из users / messages / advertisements? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Отменено.")
            return

    conn = get_db()
    # Отключаем FK временно, чтобы удалять в произвольном порядке
    await conn.execute("PRAGMA foreign_keys = OFF")
    await conn.execute("DELETE FROM advertisements")
    await conn.execute("DELETE FROM messages")
    await conn.execute("DELETE FROM users")
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.commit()
    print("✅ Таблицы очищены (структура сохранена).")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Seed-утилита для тестовой БД бота модерации.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", metavar="КОМАНДА")

    # trusted
    s = sub.add_parser("trusted", help="Засеять доверенного участника")
    s.add_argument("user_id",  type=int)
    s.add_argument("username", nargs="?", default=None)

    # dormant
    s = sub.add_parser("dormant", help="Засеять «спящего» участника")
    s.add_argument("user_id",  type=int)
    s.add_argument("username", nargs="?", default=None)

    # newbie
    s = sub.add_parser("newbie", help="Засеять новичка")
    s.add_argument("user_id",  type=int)
    s.add_argument("username", nargs="?", default=None)

    # custom
    s = sub.add_parser("custom", help="Произвольные параметры")
    s.add_argument("user_id",      type=int)
    s.add_argument("--count",      type=int, required=True, metavar="N")
    s.add_argument("--days-ago",   type=int, required=True, metavar="D",
                   dest="days_ago")
    s.add_argument("--username",   default=None)
    s.add_argument("--ad-attempts", type=int, default=None, dest="ad_attempts",
                   metavar="A")

    # show
    sub.add_parser("show", help="Показать содержимое таблицы users")

    # reset
    s = sub.add_parser("reset", help="Очистить все данные")
    s.add_argument("--yes", action="store_true",
                   help="Не спрашивать подтверждение")

    return p


async def _run(args: argparse.Namespace) -> None:
    await init_db()
    try:
        if args.cmd == "trusted":
            await cmd_trusted(args.user_id, args.username)
        elif args.cmd == "dormant":
            await cmd_dormant(args.user_id, args.username)
        elif args.cmd == "newbie":
            await cmd_newbie(args.user_id, args.username)
        elif args.cmd == "custom":
            await cmd_custom(
                args.user_id, args.count, args.days_ago,
                args.username, args.ad_attempts,
            )
        elif args.cmd == "show":
            await cmd_show()
        elif args.cmd == "reset":
            await cmd_reset(args.yes)
        else:
            _build_parser().print_help()
    finally:
        await close_db()


def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()
    if args.cmd is None:
        parser.print_help()
        sys.exit(0)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()

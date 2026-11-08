"""Перенос данных из SQLite в PostgreSQL.

Использование:
    python scripts/migrate_sqlite_to_postgres.py \
        --sqlite data/bot.db \
        --postgres postgresql://modbot:password@localhost:5432/modbot

Скрипт рассчитан на пустую целевую базу: схема создаётся автоматически,
затем таблицы наполняются с сохранением id и сбрасываются последовательности.
Перед запуском остановите бота.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.database import Database  # noqa: E402

# Порядок важен из-за внешних ключей.
TABLES: tuple[str, ...] = (
    "guild_settings",
    "bot_jobs",
    "selfrole_panels",
    "selfrole_options",
    "selfrole_exclusive",
    "command_access",
    "autoroles",
    "banned_words",
    "warnings",
    "members",
    "level_rewards",
    "ticket_panels",
    "tickets",
    "temp_actions",
    "appeals",
    "giveaways",
    "giveaway_entries",
    "stats_channels",
    "shop_items",
    "shop_purchases",
    "captcha_pending",
)

ID_TABLES: tuple[str, ...] = (
    "bot_jobs",
    "selfrole_panels",
    "warnings",
    "ticket_panels",
    "tickets",
    "temp_actions",
    "appeals",
    "giveaways",
    "stats_channels",
    "shop_items",
    "shop_purchases",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Перенос SQLite -> PostgreSQL")
    parser.add_argument("--sqlite", default="data/bot.db", help="Путь к файлу SQLite")
    parser.add_argument("--postgres", required=True, help="DSN PostgreSQL")
    return parser.parse_args()


def sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(row[1]) for row in rows]


async def migrate(sqlite_path: Path, postgres_dsn: str) -> int:
    if not sqlite_path.is_file():
        print(f"[!] Файл SQLite не найден: {sqlite_path}")
        return 1

    source = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row

    target = Database(postgres_dsn)
    await target.connect()

    try:
        for table in TABLES:
            row = await target.fetchone(f"SELECT COUNT(*) AS c FROM {table}")
            if row is not None and int(row["c"]) > 0:
                print(f"[!] Таблица {table} в PostgreSQL уже содержит данные.")
                print("    Перенос возможен только в пустую базу.")
                return 1

        total = 0
        for table in TABLES:
            columns = sqlite_columns(source, table)
            if not columns:
                print(f"[-] {table}: нет в SQLite, пропускаю")
                continue

            column_sql = ", ".join(columns)
            placeholders = ", ".join("?" for _ in columns)
            rows = source.execute(f"SELECT {column_sql} FROM {table}").fetchall()
            if not rows:
                print(f"[ ] {table}: пусто")
                continue

            await target.executemany(
                f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})",
                [tuple(row) for row in rows],
            )
            total += len(rows)
            print(f"[+] {table}: {len(rows)}")

        for table in ID_TABLES:
            await target.execute(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {table}), 1),
                    (SELECT COUNT(*) FROM {table}) > 0
                )
                """
            )

        print(f"\n[✓] Перенесено строк: {total}")
        return 0
    finally:
        source.close()
        await target.close()


def main() -> int:
    args = parse_args()
    return asyncio.run(migrate(Path(args.sqlite), args.postgres))


if __name__ == "__main__":
    raise SystemExit(main())

"""Обновляет dashboard/commands.json списком команд бота.

Запускать после добавления/переименования команд:
    python scripts/dump_dashboard_commands.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot import EXTENSIONS, ModerationBot  # noqa: E402
from config import Config  # noqa: E402


async def dump() -> int:
    bot = ModerationBot(
        Config(
            token="catalog",
            guild_id=None,
            db_path=Path(":memory:"),
            database_url=None,
            log_level="INFO",
            log_file=None,
        )
    )
    await bot.db.connect()
    try:
        for extension in EXTENSIONS:
            await bot.load_extension(extension)
        rows = [
            {
                "name": command.qualified_name,
                "description": command.description,
                "group": command.root_parent.name if command.root_parent else command.name,
            }
            for command in bot.tree.walk_commands()
        ]
        rows.sort(key=lambda item: (item["group"], item["name"]))
        target = ROOT / "dashboard" / "commands.json"
        target.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Записано команд: {len(rows)} -> {target}")
        return 0
    finally:
        for extension in reversed(EXTENSIONS):
            try:
                await bot.unload_extension(extension)
            except Exception:  # noqa: BLE001
                pass
        await bot.db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(dump()))

"""Проверка, что все коги загружаются и дерево команд корректно.

Запуск без токена и без подключения к Discord:
    python scripts/check_commands.py

Скрипт создаёт бота с временной SQLite в памяти, загружает все расширения,
строит дерево application commands и проверяет лимиты Discord.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import discord  # noqa: E402
from discord import app_commands  # noqa: E402

from bot import EXTENSIONS, ModerationBot  # noqa: E402
from config import Config  # noqa: E402

NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
PARAM_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def check_command(command: app_commands.Command, issues: list[str]) -> None:
    name = command.qualified_name
    if not NAME_RE.match(command.name):
        issues.append(f"{name}: недопустимое имя команды")
    if not command.description or len(command.description) > 100:
        issues.append(f"{name}: описание пустое или длиннее 100 символов")
    if len(command.parameters) > 25:
        issues.append(f"{name}: больше 25 параметров")

    optional_seen = False
    for parameter in command.parameters:
        if not PARAM_RE.match(parameter.name):
            issues.append(f"{name}: недопустимое имя параметра {parameter.name!r}")
        if not parameter.description or len(parameter.description) > 100:
            issues.append(f"{name}: описание параметра {parameter.name} пустое/длинное")
        if parameter.choices and len(parameter.choices) > 25:
            issues.append(f"{name}: больше 25 choices у {parameter.name}")
        if parameter.required and optional_seen:
            issues.append(f"{name}: обязательный параметр {parameter.name} после необязательного")
        if not parameter.required:
            optional_seen = True


async def main() -> int:
    config = Config(
        token="test",
        guild_id=None,
        db_path=Path(":memory:"),
        database_url=None,
        log_level="INFO",
        log_file=None,
    )
    bot = ModerationBot(config)
    await bot.db.connect()
    issues: list[str] = []

    try:
        for extension in EXTENSIONS:
            await bot.load_extension(extension)

        top_level = bot.tree.get_commands()
        if len(top_level) > 100:
            issues.append(f"Слишком много top-level команд: {len(top_level)} > 100")
        for command in top_level:
            if not isinstance(command, app_commands.Group):
                issues.append(f"/{command.name}: top-level команда вне группы")

        seen: set[str] = set()
        for command in top_level:
            if command.name in seen:
                issues.append(f"Дубликат top-level команды: {command.name}")
            seen.add(command.name)

        all_commands = list(bot.tree.walk_commands())
        checked = 0
        for item in all_commands:
            if isinstance(item, app_commands.Group):
                if len(item.commands) > 25:
                    issues.append(f"{item.qualified_name}: больше 25 подкоманд")
                if not item.commands:
                    issues.append(f"{item.qualified_name}: пустая группа")
                continue
            if isinstance(item, app_commands.Command):
                check_command(item, issues)
                checked += 1

        print(f"Расширений загружено: {len(EXTENSIONS)}")
        print(f"Top-level команд/групп: {len(top_level)}")
        print(f"Всего узлов дерева: {len(all_commands)} (команд: {checked})")
        print(f"discord.py: {discord.__version__}")

        if issues:
            print("\nНайдены проблемы:")
            for issue in issues:
                print(" -", issue)
            return 1
        print("\nOK: все команды зарегистрированы и проходят лимиты Discord.")
        return 0
    finally:
        for extension in reversed(EXTENSIONS):
            try:
                await bot.unload_extension(extension)
            except Exception:
                pass
        await bot.db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

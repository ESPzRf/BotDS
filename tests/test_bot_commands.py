"""Загрузка всех когов и проверка дерева slash-команд.

Тест пропускается, если discord.py не установлен.
"""
from __future__ import annotations

import unittest
from pathlib import Path

try:
    import discord
    from discord import app_commands

    HAS_DISCORD = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_DISCORD = False

if HAS_DISCORD:
    from bot import EXTENSIONS, ModerationBot
    from config import Config


@unittest.skipUnless(HAS_DISCORD, "discord.py не установлен")
class CommandTreeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        config = Config(
            token="test-token",
            guild_id=None,
            db_path=Path(":memory:"),
            database_url=None,
            log_level="INFO",
            log_file=None,
        )
        self.bot = ModerationBot(config)
        await self.bot.db.connect()
        for extension in EXTENSIONS:
            await self.bot.load_extension(extension)

    async def asyncTearDown(self) -> None:
        for extension in reversed(EXTENSIONS):
            try:
                await self.bot.unload_extension(extension)
            except Exception:  # noqa: BLE001
                pass
        await self.bot.db.close()

    async def test_tree_limits_and_names(self) -> None:
        top_level = self.bot.tree.get_commands()
        self.assertLessEqual(len(top_level), 100)
        self.assertEqual(len({command.name for command in top_level}), len(top_level))

        for item in self.bot.tree.walk_commands():
            if isinstance(item, app_commands.Group):
                self.assertTrue(item.commands, f"{item.qualified_name}: пустая группа")
                self.assertLessEqual(len(item.commands), 25)
                continue

            self.assertIsInstance(item, app_commands.Command)
            self.assertRegex(item.name, r"^[a-z0-9_-]{1,32}$")
            self.assertTrue(item.description)
            self.assertLessEqual(len(item.description), 100)
            self.assertLessEqual(len(item.parameters), 25)
            self.assertTrue(callable(item.callback))

            optional_seen = False
            for parameter in item.parameters:
                self.assertRegex(parameter.name, r"^[a-z0-9_-]{1,32}$")
                self.assertTrue(parameter.description, f"{item.qualified_name}.{parameter.name}")
                if not parameter.required:
                    optional_seen = True
                elif optional_seen:
                    self.fail(
                        f"{item.qualified_name}: обязательный {parameter.name} после необязательного"
                    )

    async def test_every_command_has_binding(self) -> None:
        for item in self.bot.tree.walk_commands():
            if isinstance(item, app_commands.Command):
                self.assertIsNotNone(item.binding, f"{item.qualified_name}: нет cog")

    async def test_presets_reference_existing_commands(self) -> None:
        from core.command_access import COMMAND_PRESETS

        available = {
            item.qualified_name
            for item in self.bot.tree.walk_commands()
        }
        for name, commands in COMMAND_PRESETS.items():
            self.assertEqual(len(commands), len(set(commands)), f"{name}: дубликаты")
            for command in commands:
                self.assertIn(command, available, f"{name}: нет команды {command}")

    async def test_help_pages_fit_discord_limits(self) -> None:
        from cogs.utility import build_help_pages

        pages = build_help_pages(self.bot)
        self.assertTrue(pages)
        for number, page in enumerate(pages, start=1):
            self.assertLessEqual(len(page.fields), 25)
            total = len(page.title or "") + len(page.description or "")
            total += sum(len(field.name) + len(field.value) for field in page.fields)
            self.assertLessEqual(total, 5800, f"страница {number} слишком большая")
            self.assertLessEqual(len(page.description or ""), 4096)
            for field in page.fields:
                self.assertLessEqual(len(field.name), 256)
                self.assertLessEqual(len(field.value), 1024)


if __name__ == "__main__":
    unittest.main()

"""Служебные команды: help, ping, botinfo."""

from __future__ import annotations

import platform
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.embeds import COLOR_MUTED, COLOR_PRIMARY, COLOR_SUCCESS, base_embed
from core.timeparse import format_duration

SECTION_ORDER = [
    "🛡️ Модерация",
    "🎫 Тикеты и апелляции",
    "🚨 Безопасность",
    "🤖 Автоматизация",
    "📈 Уровни и магазин",
    "🎉 Розыгрыши",
    "⚙️ Настройки",
    "ℹ️ Прочее",
]

ROOT_SECTION = {
    "warn": 0,
    "warnings": 0,
    "unwarn": 0,
    "clearwarnings": 0,
    "warnconfig": 0,
    "tempactions": 0,
    "ticket": 1,
    "appeal": 1,
    "appeals": 1,
    "antiraid": 2,
    "antispam": 2,
    "captcha": 2,
    "automod": 3,
    "autorole": 3,
    "selfrole": 3,
    "stats": 3,
    "rank": 4,
    "leaderboard": 4,
    "daily": 4,
    "levelconfig": 4,
    "shop": 4,
    "buy": 4,
    "balance": 4,
    "giveaway": 5,
    "logs": 6,
    "settings": 6,
    "access": 6,
    "help": 7,
    "ping": 7,
    "botinfo": 7,
}


def pack_lines(lines: list[str], limit: int = 1000) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


class Utility(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="Список команд бота")
    async def help_command(self, interaction: discord.Interaction) -> None:
        sections: dict[int, list[str]] = {index: [] for index in range(len(SECTION_ORDER))}

        for command in self.bot.tree.walk_commands():
            if not isinstance(command, app_commands.Command):
                continue
            root = command.root_parent.name if command.root_parent else command.name
            index = ROOT_SECTION.get(root, len(SECTION_ORDER) - 1)
            sections[index].append(f"`/{command.qualified_name}` — {command.description}")

        embed = base_embed(
            title="🤖 Команды модерации",
            description=(
                "Начните с настройки логов: `/logs channel`, затем включите нужные модули.\n"
                "Все команды — слэш-команды с подсказками."
            ),
            color=COLOR_PRIMARY,
        )

        for index, title in enumerate(SECTION_ORDER):
            lines = sections[index]
            if not lines:
                continue
            for part_index, part in enumerate(pack_lines(lines)):
                name = title if part_index == 0 else f"{title} (продолжение)"
                embed.add_field(name=name, value=part, inline=False)

        total = len(list(self.bot.tree.walk_commands()))
        embed.set_footer(text=f"Всего команд: {total} • Ошибка? Проверьте права бота")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="ping", description="Проверить задержку бота и базы данных")
    async def ping(self, interaction: discord.Interaction) -> None:
        started = time.perf_counter()
        try:
            await self.bot.db.fetchone("SELECT 1 AS ok")
            db_ms = (time.perf_counter() - started) * 1000
            db_text = f"{db_ms:.1f} мс"
        except Exception:  # noqa: BLE001
            db_text = "недоступна"

        gateway_ms = round(self.bot.latency * 1000)
        embed = base_embed(
            title="🏓 Понг",
            description=f"Gateway: **{gateway_ms} мс**\nБаза данных: **{db_text}**",
            color=COLOR_SUCCESS,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="botinfo", description="Информация о боте")
    async def botinfo(self, interaction: discord.Interaction) -> None:
        assert self.bot.user is not None
        uptime = (datetime.now(timezone.utc) - self.bot.started_at).total_seconds()
        guilds = len(self.bot.guilds)
        members = sum(guild.member_count or 0 for guild in self.bot.guilds)
        commands_count = len(list(self.bot.tree.walk_commands()))

        embed = base_embed(title="ℹ️ Информация о боте", color=COLOR_MUTED)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="Бот", value=f"{self.bot.user} (`{self.bot.user.id}`)", inline=False)
        embed.add_field(name="Серверов", value=str(guilds), inline=True)
        embed.add_field(name="Участников", value=f"{members:,}".replace(",", " "), inline=True)
        embed.add_field(name="Команд", value=str(commands_count), inline=True)
        embed.add_field(name="Аптайм", value=format_duration(int(uptime)), inline=True)
        embed.add_field(name="discord.py", value=discord.__version__, inline=True)
        embed.add_field(name="Python", value=platform.python_version(), inline=True)
        embed.set_footer(text="Модерация, уровни и логи — всё в одном боте")

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Utility(bot))

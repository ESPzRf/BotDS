"""Статистика сервера в названиях голосовых каналов."""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.embeds import COLOR_MUTED, base_embed, success_embed
from core.errors import UserError
from core.repositories import StatsChannel
from core.utils import truncate

log = logging.getLogger(__name__)

KINDS = {
    "members": "Участники",
    "humans": "Люди",
    "bots": "Боты",
    "boosts": "Бусты",
}

KIND_CHOICES = [
    app_commands.Choice(name="Всего участников", value="members"),
    app_commands.Choice(name="Люди", value="humans"),
    app_commands.Choice(name="Боты", value="bots"),
    app_commands.Choice(name="Бусты", value="boosts"),
]

DEFAULT_TEMPLATES = {
    "members": "👥 Участники: {count}",
    "humans": "👤 Люди: {count}",
    "bots": "🤖 Боты: {count}",
    "boosts": "🚀 Бусты: {count}",
}


class Stats(commands.Cog):
    stats = app_commands.Group(
        name="stats",
        description="Статистика в голосовых каналах",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._human_bot_cache: dict[int, tuple[int, int]] = {}

    async def cog_load(self) -> None:
        if self.bot.is_ready() and not self._update_loop.is_running():
            self._update_loop.start()

    def cog_unload(self) -> None:
        self._update_loop.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self._update_loop.is_running():
            self._update_loop.start()
        try:
            await self._update_all()
        except Exception:  # noqa: BLE001
            log.exception("Не удалось обновить статистику при старте")

    @tasks.loop(seconds=600)
    async def _update_loop(self) -> None:
        await self.bot.wait_until_ready()
        try:
            await self._update_all()
        except Exception:  # noqa: BLE001 - цикл не должен умирать
            log.exception("Не удалось обновить статистику")

    async def _update_all(self) -> None:
        if not await self._enabled_anywhere():
            return
        self._human_bot_cache.clear()
        channels = await self.bot.repos.stats.all()
        for item in channels:
            await self._update_channel(item)

    async def refresh_guild(self, guild_id: int) -> int:
        """Обновляет каналы статистики одного сервера по запросу из дашборда."""

        if not await self.bot.settings.get_bool(guild_id, "stats_enabled", True):
            return 0
        self._human_bot_cache.pop(guild_id, None)
        channels = await self.bot.repos.stats.list(guild_id)
        updated = 0
        for item in channels:
            await self._update_channel(item)
            updated += 1
        return updated

    async def _enabled_anywhere(self) -> bool:
        for guild in self.bot.guilds:
            if await self.bot.settings.get_bool(guild.id, "stats_enabled", True):
                return True
        return False

    def _count(self, guild: discord.Guild, kind: str) -> int:
        if kind == "members":
            return guild.member_count or 0
        if kind in {"humans", "bots"}:
            cached = self._human_bot_cache.get(guild.id)
            if cached is None:
                humans = sum(1 for member in guild.members if not member.bot)
                cached = (humans, len(guild.members) - humans)
                self._human_bot_cache[guild.id] = cached
            return cached[0] if kind == "humans" else cached[1]
        if kind == "boosts":
            return guild.premium_subscription_count or 0
        return 0

    def _name(self, item: StatsChannel, count: int) -> str:
        try:
            text = item.template.format(count=count)
        except (KeyError, IndexError, ValueError):
            text = f"{item.template} {count}"
        return truncate(text, 100, suffix="")

    async def _update_channel(self, item: StatsChannel) -> None:
        guild = self.bot.get_guild(item.guild_id)
        if guild is None:
            return
        if not await self.bot.settings.get_bool(guild.id, "stats_enabled", True):
            return

        channel = guild.get_channel(item.channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            return

        name = self._name(item, self._count(guild, item.kind))
        if channel.name == name:
            return
        try:
            await channel.edit(name=name, reason="Обновление статистики")
        except (discord.Forbidden, discord.HTTPException):
            log.debug("Не удалось обновить канал статистики %s", item.channel_id, exc_info=True)

    # --------------------------------------------------------------- commands

    @stats.command(name="add", description="Добавить канал статистики")
    @app_commands.describe(
        kind="Что показывать",
        channel="Голосовой канал",
        template="Шаблон с {count}, например «👥 Участники: {count}»",
    )
    @app_commands.choices(kind=KIND_CHOICES)
    async def stats_add(
        self,
        interaction: discord.Interaction,
        kind: str,
        channel: discord.VoiceChannel,
        template: Optional[str] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        if kind not in KINDS:
            raise UserError("Неизвестный тип статистики.")

        permissions = channel.permissions_for(guild.me)
        if not permissions.manage_channels:
            raise UserError(f"У бота нет права «Управлять каналом» для {channel.mention}.")

        tmpl = (template or DEFAULT_TEMPLATES[kind]).strip()[:100]
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.repos.stats.upsert(guild.id, channel.id, kind, tmpl)
        item = next(
            (entry for entry in await self.bot.repos.stats.list(guild.id) if entry.channel_id == channel.id),
            None,
        )
        if item is not None:
            await self._update_channel(item)

        await interaction.followup.send(
            embed=success_embed(
                f"{channel.mention} будет показывать **{KINDS[kind]}**.\nШаблон: `{tmpl}`"
            ),
            ephemeral=True,
        )

    @stats.command(name="remove", description="Убрать канал из статистики")
    @app_commands.describe(channel="Канал статистики")
    async def stats_remove(self, interaction: discord.Interaction, channel: discord.VoiceChannel) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        removed = await self.bot.repos.stats.remove(guild.id, channel.id)
        if not removed:
            raise UserError(f"{channel.mention} не найден в статистике.")
        await interaction.response.send_message(
            embed=success_embed(f"{channel.mention} убран из статистики."),
            ephemeral=True,
        )

    @stats.command(name="list", description="Список каналов статистики")
    async def stats_list(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        items = await self.bot.repos.stats.list(guild.id)
        embed = base_embed(title="📊 Статистика", color=COLOR_MUTED)
        if not items:
            embed.description = "Каналов статистики нет. Добавьте: `/stats add`."
        else:
            for item in items[:20]:
                embed.add_field(
                    name=f"<#{item.channel_id}>",
                    value=f"Тип: **{KINDS.get(item.kind, item.kind)}**\nШаблон: `{item.template}`",
                    inline=False,
                )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @stats.command(name="enable", description="Включить обновление статистики")
    async def stats_enable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.settings.set(interaction.guild.id, "stats_enabled", True)
        await interaction.response.send_message(
            embed=success_embed("Статистика включена."),
            ephemeral=True,
        )

    @stats.command(name="disable", description="Выключить обновление статистики")
    async def stats_disable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.settings.set(interaction.guild.id, "stats_enabled", False)
        await interaction.response.send_message(
            embed=success_embed("Статистика выключена."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Stats(bot))

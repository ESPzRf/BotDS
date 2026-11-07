"""Общие настройки сервера: обзор и сброс."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.discord_utils import ConfirmView
from core.embeds import COLOR_MUTED, COLOR_WARNING, base_embed, success_embed

log = logging.getLogger(__name__)


class Settings(commands.Cog):
    settings_group = app_commands.Group(
        name="settings",
        description="Общие настройки бота на сервере",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @settings_group.command(name="language", description="Язык ответов бота")
    @app_commands.choices(
        language=[
            app_commands.Choice(name="Русский", value="ru"),
            app_commands.Choice(name="English", value="en"),
        ]
    )
    async def settings_language(
        self,
        interaction: discord.Interaction,
        language: str,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return
        if language not in self.bot.i18n.languages:
            await interaction.response.send_message(
                embed=base_embed(
                    title=self.bot.i18n.t("ru", "bot.language.invalid"),
                    color=COLOR_WARNING,
                ),
                ephemeral=True,
            )
            return

        await self.bot.settings.set(guild.id, "locale", language)
        text = self.bot.i18n.t(
            language,
            "bot.language.saved",
            language=self.bot.i18n.language_name(language),
        )
        await interaction.response.send_message(embed=success_embed(text), ephemeral=True)

    @settings_group.command(name="view", description="Показать все настройки бота на сервере")
    async def settings_view(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return

        settings = self.bot.settings
        guild_id = guild.id

        log_channel = await settings.get(guild_id, "log_channel")
        modlog_channel = await settings.get(guild_id, "modlog_channel")
        autoroles = await self.bot.repos.autoroles.list(guild_id)
        words = await self.bot.repos.words.list(guild_id)
        panels = await self.bot.repos.selfroles.panels(guild_id)
        rewards = await self.bot.repos.level_rewards.list(guild_id)
        ticket_panels = await self.bot.repos.tickets.panels(guild_id)
        open_tickets = await self.bot.repos.tickets.open_tickets(guild_id)
        active_giveaways = await self.bot.repos.giveaways.active(guild_id)
        shop_items = await self.bot.repos.shop.list(guild_id)
        stats_channels = await self.bot.repos.stats.list(guild_id)

        automod_enabled = await settings.get_bool(guild_id, "automod_enabled", False)
        automod_action = str(await settings.get(guild_id, "automod_action", "delete"))
        levels_enabled = await settings.get_bool(guild_id, "levels_enabled", True)
        xp_min = await settings.get_int(guild_id, "levels_xp_min", 15)
        xp_max = await settings.get_int(guild_id, "levels_xp_max", 25)
        warn_rules = await settings.get(guild_id, "warn_escalations", [])
        dm_enabled = await settings.get_bool(guild_id, "warnings_dm", True)
        locale = str(await settings.get(guild_id, "locale", "ru"))

        def channel_text(channel_id: int | None) -> str:
            if not channel_id:
                return "не задан"
            return f"<#{int(channel_id)}>"

        embed = base_embed(title=f"⚙️ Настройки — {guild.name}", color=COLOR_MUTED)
        embed.add_field(
            name="Логи",
            value=f"Общий: {channel_text(log_channel)}\nМодерация: {channel_text(modlog_channel)}",
            inline=True,
        )
        embed.add_field(name="Автовыдача ролей", value=f"Ролей: **{len(autoroles)}**", inline=True)
        embed.add_field(
            name="Автомодерация",
            value=("✅ включена" if automod_enabled else "❌ выключена")
            + f"\nДействие: `{automod_action}`\nСлов: **{len(words)}**",
            inline=True,
        )
        embed.add_field(
            name="Предупреждения",
            value=f"Порогов: **{len(warn_rules) if isinstance(warn_rules, list) else 0}**\n"
            f"DM: {'✅' if dm_enabled else '❌'}",
            inline=True,
        )
        embed.add_field(
            name="Уровни",
            value=("✅ включены" if levels_enabled else "❌ выключены")
            + f"\nXP за сообщение: {xp_min}–{xp_max}"
            + f"\nНаград: **{len(rewards)}**",
            inline=True,
        )
        embed.add_field(name="Панели ролей", value=f"Панелей: **{len(panels)}**", inline=True)
        embed.add_field(
            name="Тикеты",
            value=f"Панелей: **{len(ticket_panels)}**\nОткрытых: **{len(open_tickets)}**",
            inline=True,
        )
        embed.add_field(
            name="Развлечения",
            value=f"Розыгрышей: **{len(active_giveaways)}**\nТоваров: **{len(shop_items)}**",
            inline=True,
        )
        embed.add_field(name="Статистика", value=f"Каналов: **{len(stats_channels)}**", inline=True)
        embed.add_field(
            name="Язык",
            value=self.bot.i18n.language_name(locale),
            inline=True,
        )

        embed.set_footer(text="Сбросить настройки: /settings reset")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @settings_group.command(name="reset", description="Сбросить настройки сервера к значениям по умолчанию")
    async def settings_reset(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return

        view = ConfirmView(interaction.user.id, confirm_label="Сбросить настройки")
        await interaction.response.send_message(
            embed=base_embed(
                title="Сбросить настройки?",
                description=(
                    "Будут сброшены каналы логов, настройки автомодерации, пороги предупреждений "
                    "и параметры уровней.\n\n"
                    "**Не удаляются:** сами предупреждения, статистика участников, "
                    "запрещённые слова, автoроли и панели ролей."
                ),
                color=COLOR_WARNING,
            ),
            view=view,
            ephemeral=True,
        )
        await view.wait()

        if view.value is not True:
            await interaction.edit_original_response(
                embed=base_embed(title="Сброс отменён", color=COLOR_MUTED),
                view=None,
            )
            return

        await self.bot.settings.delete_all(guild.id)
        await interaction.edit_original_response(
            embed=success_embed("Настройки сервера сброшены к значениям по умолчанию."),
            view=None,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Settings(bot))

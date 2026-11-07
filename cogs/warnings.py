"""Предупреждения и автоматическая эскалация наказаний."""

from __future__ import annotations

import logging
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.embeds import (
    COLOR_MUTED,
    COLOR_WARNING,
    base_embed,
    success_embed,
    warning_embed,
)
from core.errors import UserError
from core.moderation import ensure_can_moderate
from core.settings import DEFAULT_WARN_ESCALATIONS
from core.timeparse import format_duration, parse_duration
from core.utils import plural_ru, truncate

log = logging.getLogger(__name__)

ESCALATION_CHOICES = [
    app_commands.Choice(name="Тайм-аут", value="timeout"),
    app_commands.Choice(name="Кик", value="kick"),
    app_commands.Choice(name="Бан", value="ban"),
]

ACTION_NAMES = {"timeout": "тайм-аут", "kick": "кик", "ban": "бан"}


def describe_rule(rule: dict[str, Any]) -> str:
    action = str(rule.get("action", ""))
    name = ACTION_NAMES.get(action, action)
    if action == "timeout":
        try:
            seconds = int(rule.get("duration") or 3600)
        except (TypeError, ValueError):
            seconds = 3600
        return f"{name} на {format_duration(seconds)}"
    return name


class Warnings(commands.Cog):
    mod = app_commands.Group(
        name="mod",
        description="Предупреждения и действия модераторов",
        default_permissions=discord.Permissions(moderate_members=True),
    )
    warnconfig = app_commands.Group(
        name="warnconfig",
        description="Настройка предупреждений и автонаказаний",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # --------------------------------------------------------------- commands

    @mod.command(name="warn", description="Выдать предупреждение участнику")
    @app_commands.describe(member="Нарушитель", reason="Причина предупреждения")
    @app_commands.default_permissions(moderate_members=True)
    async def warn(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        await ensure_can_moderate(interaction, member)
        await interaction.response.defer(ephemeral=True, thinking=True)

        result = await self.bot.warning_service.add(
            guild,
            member,
            interaction.user,
            reason,
            source="manual",
        )

        word = plural_ru(result.count, "предупреждение", "предупреждения", "предупреждений")
        embed = warning_embed(
            f"{member.mention} получил **{result.count}** {word}.",
            title="⚠️ Предупреждение выдано",
        )
        embed.add_field(name="Причина", value=truncate(reason, 1024), inline=False)
        embed.add_field(name="ID", value=f"`{result.warning.id}`", inline=True)
        if result.rule is not None:
            embed.add_field(name="Порог", value=str(result.rule.get("count", "—")), inline=True)
        if result.action_note:
            embed.add_field(name="Автодействие", value=truncate(result.action_note, 1024), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @mod.command(name="warnings", description="Показать активные предупреждения участника")
    @app_commands.describe(member="Участник")
    @app_commands.default_permissions(moderate_members=True)
    async def warnings_list(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        warnings = await self.bot.repos.warnings.active_for(guild.id, member.id)
        if not warnings:
            await interaction.response.send_message(
                embed=base_embed(
                    title="Предупреждения",
                    description=f"У {member.mention} нет активных предупреждений.",
                    color=COLOR_MUTED,
                ),
                ephemeral=True,
            )
            return

        word = plural_ru(len(warnings), "предупреждение", "предупреждения", "предупреждений")
        embed = base_embed(
            title=f"Предупреждения — {member}",
            description=f"Активных: **{len(warnings)}** {word}",
            color=COLOR_WARNING,
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        for warning in warnings[:10]:
            moderator = f"<@{warning.moderator_id}>" if warning.moderator_id else "Автомодерация"
            source = " · автомод" if warning.source == "automod" else ""
            embed.add_field(
                name=f"#{warning.id} · {discord.utils.format_dt(warning.created_at, 'd')}",
                value=truncate(
                    f"Модератор: {moderator}{source}\nПричина: {warning.reason}",
                    1024,
                ),
                inline=False,
            )

        if len(warnings) > 10:
            embed.set_footer(text=f"Показаны первые 10 из {len(warnings)}")

        rules = await self.bot.settings.get(guild.id, "warn_escalations", [])
        if isinstance(rules, list) and rules:
            thresholds = ", ".join(
                f"{rule.get('count')} → {describe_rule(rule)}"
                for rule in rules
                if isinstance(rule, dict)
            )
            if thresholds:
                embed.add_field(name="Пороги автонаказаний", value=truncate(thresholds, 1024), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @mod.command(name="unwarn", description="Снять предупреждение по ID")
    @app_commands.describe(warning_id="ID предупреждения (из /mod warnings)", reason="Причина снятия")
    @app_commands.default_permissions(moderate_members=True)
    async def unwarn(
        self,
        interaction: discord.Interaction,
        warning_id: int,
        reason: Optional[str] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        warning = await self.bot.repos.warnings.get(warning_id)
        if warning is None or warning.guild_id != guild.id:
            raise UserError(f"Предупреждение #{warning_id} не найдено на этом сервере.")
        if not warning.active:
            raise UserError(f"Предупреждение #{warning_id} уже снято.")

        await self.bot.repos.warnings.deactivate(warning_id, guild.id)
        count = await self.bot.repos.warnings.count_active(guild.id, warning.user_id)

        embed = success_embed(
            f"Предупреждение **#{warning_id}** снято с <@{warning.user_id}>.\n"
            f"Осталось активных: **{count}**."
        )
        if reason:
            embed.add_field(name="Причина", value=truncate(reason, 1024), inline=False)
        embed.add_field(name="Исходная причина", value=truncate(warning.reason, 1024), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @mod.command(name="clearwarnings", description="Снять все предупреждения участника")
    @app_commands.describe(member="Участник, чьи предупреждения нужно снять")
    @app_commands.default_permissions(moderate_members=True)
    async def clearwarnings(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        await ensure_can_moderate(interaction, member)
        count = await self.bot.repos.warnings.clear(guild.id, member.id)
        if count == 0:
            raise UserError(f"У {member.mention} нет активных предупреждений.")

        await interaction.response.send_message(
            embed=success_embed(
                f"Снято предупреждений с {member.mention}: **{count}**."
            ),
            ephemeral=True,
        )

    # ------------------------------------------------------------- warnconfig

    @warnconfig.command(name="show", description="Показать настройки предупреждений")
    async def warnconfig_show(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        settings = self.bot.settings

        rules = await settings.get(guild.id, "warn_escalations", [])
        dm_enabled = await settings.get_bool(guild.id, "warnings_dm", True)
        channel_id = await settings.get(guild.id, "modlog_channel")
        channel_text = f"<#{int(channel_id)}>" if channel_id else "общий канал логов"

        embed = base_embed(title="⚙️ Предупреждения", color=COLOR_MUTED)
        embed.add_field(name="Личные сообщения", value="✅ включены" if dm_enabled else "❌ выключены", inline=True)
        embed.add_field(name="Канал логов", value=channel_text, inline=True)

        if not isinstance(rules, list) or not rules:
            embed.add_field(name="Пороги", value="Автонаказания не настроены.", inline=False)
        else:
            valid_rules = [rule for rule in rules if isinstance(rule, dict)]
            if not valid_rules:
                embed.add_field(name="Пороги", value="Автонаказания не настроены.", inline=False)
            else:
                lines = [
                    f"**{rule.get('count', '?')}** предупреждений → {describe_rule(rule)}"
                    for rule in sorted(valid_rules, key=lambda item: int(item.get("count", 0)))
                ]
                embed.add_field(name="Пороги", value=truncate("\n".join(lines), 1024), inline=False)

        embed.set_footer(text="Изменить: /warnconfig escalation, /warnconfig dm, /warnconfig channel")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @warnconfig.command(name="escalation", description="Добавить автоматическое наказание за N предупреждений")
    @app_commands.describe(
        count="Количество активных предупреждений",
        action="Что сделать при достижении порога",
        duration="Для тайм-аута: 1h, 30m, 2d",
    )
    @app_commands.choices(action=ESCALATION_CHOICES)
    async def warnconfig_escalation(
        self,
        interaction: discord.Interaction,
        count: int,
        action: str,
        duration: Optional[str] = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        if not 1 <= count <= 50:
            raise UserError("Количество предупреждений должно быть от 1 до 50.")
        if action not in ACTION_NAMES:
            raise UserError("Неизвестное действие.")

        rule: dict[str, Any] = {"count": count, "action": action}
        if action == "timeout":
            if not duration:
                raise UserError("Для тайм-аута укажите длительность, например `1h`.")
            try:
                seconds = parse_duration(duration)
            except ValueError as exc:
                raise UserError(f"Не удалось разобрать длительность: {exc}") from exc
            if not 60 <= seconds <= 28 * 24 * 60 * 60:
                raise UserError("Длительность тайм-аута должна быть от 1 минуты до 28 дней.")
            rule["duration"] = seconds

        rules = await self.bot.settings.get(guild.id, "warn_escalations", [])
        if isinstance(rules, list):
            rules = [item for item in rules if isinstance(item, dict)]
        else:
            rules = []
        rules = [item for item in rules if int(item.get("count", -1)) != count]
        rules.append(rule)
        rules.sort(key=lambda item: int(item.get("count", 0)))
        await self.bot.settings.set(guild.id, "warn_escalations", rules)

        await interaction.response.send_message(
            embed=success_embed(
                f"Правило добавлено: **{count}** предупреждений → {describe_rule(rule)}."
            ),
            ephemeral=True,
        )

    @warnconfig.command(name="escalation-remove", description="Убрать порог автонаказания")
    @app_commands.describe(count="Порог, который нужно удалить")
    async def warnconfig_escalation_remove(
        self,
        interaction: discord.Interaction,
        count: int,
    ) -> None:
        guild = interaction.guild
        assert guild is not None

        rules = await self.bot.settings.get(guild.id, "warn_escalations", [])
        if not isinstance(rules, list):
            rules = []
        rules = [item for item in rules if isinstance(item, dict)]
        filtered = [item for item in rules if int(item.get("count", -1)) != count]
        if len(filtered) == len(rules):
            raise UserError(f"Порога в {count} предупреждений нет.")

        await self.bot.settings.set(guild.id, "warn_escalations", filtered)
        await interaction.response.send_message(
            embed=success_embed(f"Порог в **{count}** предупреждений удалён."),
            ephemeral=True,
        )

    @warnconfig.command(name="dm", description="Отправлять предупреждения в личные сообщения")
    @app_commands.describe(state="True — включить, False — выключить")
    async def warnconfig_dm(self, interaction: discord.Interaction, state: bool) -> None:
        guild = interaction.guild
        assert guild is not None
        await self.bot.settings.set(guild.id, "warnings_dm", state)
        await interaction.response.send_message(
            embed=success_embed(f"Личные сообщения о предупреждениях {'включены' if state else 'выключены'}."),
            ephemeral=True,
        )

    @warnconfig.command(name="channel", description="Отдельный канал для логов предупреждений")
    @app_commands.describe(channel="Канал; без параметра — использовать общий канал логов")
    async def warnconfig_channel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None

        if channel is None:
            await self.bot.settings.set(guild.id, "modlog_channel", None)
            await interaction.response.send_message(
                embed=success_embed("Логи предупреждений вернутся в общий канал логов."),
                ephemeral=True,
            )
            return

        permissions = channel.permissions_for(guild.me)
        if not permissions.send_messages or not permissions.embed_links:
            raise UserError(f"У бота нет прав писать embed-сообщения в {channel.mention}.")

        await self.bot.settings.set(guild.id, "modlog_channel", channel.id)
        await interaction.response.send_message(
            embed=success_embed(f"Логи предупреждений будут отправляться в {channel.mention}."),
            ephemeral=True,
        )

    @warnconfig.command(name="reset", description="Сбросить пороги к значениям по умолчанию")
    async def warnconfig_reset(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None

        await self.bot.settings.set(guild.id, "warn_escalations", DEFAULT_WARN_ESCALATIONS)
        await interaction.response.send_message(
            embed=success_embed("Пороги автонаказаний сброшены к значениям по умолчанию."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Warnings(bot))

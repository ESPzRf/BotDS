"""Антирейд и антиспам."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.discord_utils import resolve_modlog_channel, send_temp
from core.embeds import (
    COLOR_DANGER,
    COLOR_MUTED,
    COLOR_WARNING,
    base_embed,
    success_embed,
)
from core.errors import UserError
from core.moderation import apply_timeout
from core.ratelimit import SlidingWindow
from core.repositories import to_iso
from core.timeparse import format_duration
from core.utils import truncate

log = logging.getLogger(__name__)

RAID_ACTIONS = [
    app_commands.Choice(name="Кик", value="kick"),
    app_commands.Choice(name="Бан", value="ban"),
    app_commands.Choice(name="Тайм-аут", value="timeout"),
]

SPAM_ACTIONS = [
    app_commands.Choice(name="Только удалять", value="delete"),
    app_commands.Choice(name="Удалять + тайм-аут", value="timeout"),
]


class Security(commands.Cog):
    antiraid = app_commands.Group(
        name="antiraid",
        description="Защита от массовых заходов",
        default_permissions=discord.Permissions(manage_guild=True),
    )
    antispam = app_commands.Group(
        name="antispam",
        description="Защита от спама сообщениями",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.joins = SlidingWindow()
        self.spam = SlidingWindow()
        self._spam_punished: dict[tuple[int, int], float] = {}

    # ------------------------------------------------------------------ utils

    async def _log(self, guild: discord.Guild, embed: discord.Embed) -> None:
        channel = await resolve_modlog_channel(self.bot, guild)
        if channel is None:
            return
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            log.debug("Не удалось отправить лог безопасности", exc_info=True)

    async def _raid_until(self, guild_id: int) -> datetime | None:
        raw = await self.bot.settings.get(guild_id, "raid_mode_until")
        if not raw:
            return None
        try:
            until = datetime.fromisoformat(str(raw))
        except ValueError:
            await self.bot.settings.set(guild_id, "raid_mode_until", None)
            return None
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if until <= datetime.now(timezone.utc):
            await self.bot.settings.set(guild_id, "raid_mode_until", None)
            return None
        return until

    async def _activate_raid(self, guild: discord.Guild, reason: str) -> datetime:
        duration = max(30, await self.bot.settings.get_int(guild.id, "antiraid_duration", 300))
        until = datetime.now(timezone.utc) + timedelta(seconds=duration)
        await self.bot.settings.set(guild.id, "raid_mode_until", to_iso(until))

        embed = base_embed(title="🚨 Режим рейда активирован", color=COLOR_DANGER)
        embed.add_field(name="Причина", value=reason, inline=False)
        embed.add_field(name="До", value=discord.utils.format_dt(until, "f"), inline=True)
        await self._log(guild, embed)
        return until

    # ---------------------------------------------------------------- events

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        guild = member.guild
        settings = self.bot.settings
        if not await settings.get_bool(guild.id, "antiraid_enabled", False):
            return

        joins_limit = max(2, await settings.get_int(guild.id, "antiraid_joins", 8))
        window = max(10, await settings.get_int(guild.id, "antiraid_window", 60))
        count = self.joins.hit((guild.id,), window)

        until = await self._raid_until(guild.id)
        if count >= joins_limit and until is None:
            until = await self._activate_raid(
                guild,
                f"{count} входов за {format_duration(window)}",
            )

        if until is None:
            return

        min_age = max(0, await settings.get_int(guild.id, "antiraid_min_age_days", 7))
        if min_age <= 0:
            return

        age_days = (discord.utils.utcnow() - member.created_at).days
        if age_days >= min_age:
            return

        action = str(await settings.get(guild.id, "antiraid_action", "kick"))
        reason = f"Антирейд: возраст аккаунта {age_days} дн. (< {min_age})"
        result = "не применено"
        try:
            if action == "ban":
                await guild.ban(member, reason=reason)
                result = "забанен"
            elif action == "timeout":
                seconds = await settings.get_int(guild.id, "antiraid_timeout", 3600)
                applied = await apply_timeout(member, seconds, reason)
                result = f"тайм-аут на {format_duration(applied)}"
            else:
                await guild.kick(member, reason=reason)
                result = "кикнут"
        except (discord.Forbidden, discord.HTTPException, UserError) as exc:
            result = f"не применено: {exc}"

        embed = base_embed(title="🛡️ Антирейд", color=COLOR_WARNING)
        embed.add_field(name="Участник", value=f"{member} (`{member.id}`)", inline=True)
        embed.add_field(name="Возраст", value=f"{age_days} дн.", inline=True)
        embed.add_field(name="Действие", value=result, inline=True)
        await self._log(guild, embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or not message.content:
            return
        member = message.author
        if not isinstance(member, discord.Member):
            return

        guild = message.guild
        settings = self.bot.settings
        if not await settings.get_bool(guild.id, "antispam_enabled", False):
            return
        if member.guild_permissions.manage_messages:
            return

        ignored = await settings.get_list(guild.id, "antispam_ignored_channels")
        if message.channel.id in {int(item) for item in ignored}:
            return

        limit = max(2, await settings.get_int(guild.id, "antispam_count", 5))
        window = max(2, await settings.get_int(guild.id, "antispam_window", 10))
        key = (guild.id, member.id)
        count = self.spam.hit(key, window)
        if count < limit:
            return

        now = time.monotonic()
        if self._spam_punished.get(key, 0.0) > now:
            return
        self._spam_punished[key] = now + 30
        self.spam.reset(key)
        await self._punish_spam(message)

    async def _punish_spam(self, message: discord.Message) -> None:
        guild = message.guild
        member = message.author
        if guild is None or not isinstance(member, discord.Member):
            return

        settings = self.bot.settings
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        action = str(await settings.get(guild.id, "antispam_action", "timeout"))
        result = "сообщение удалено"
        if action == "timeout":
            seconds = await settings.get_int(guild.id, "antispam_timeout", 600)
            try:
                applied = await apply_timeout(member, seconds, "Антиспам: слишком много сообщений")
                result = f"тайм-аут на {format_duration(applied)}"
            except UserError as exc:
                result = f"тайм-аут не выдан: {exc}"

        await send_temp(
            message.channel,
            f"⚠️ {member.mention}, не спамьте — сообщения удаляются.",
            mention_user=member,
        )

        embed = base_embed(title="🚫 Антиспам", color=COLOR_DANGER)
        embed.add_field(name="Участник", value=f"{member} (`{member.id}`)", inline=True)
        embed.add_field(name="Канал", value=message.channel.mention, inline=True)
        embed.add_field(name="Действие", value=result, inline=True)
        embed.add_field(name="Сообщение", value=truncate(message.content, 500) or "—", inline=False)
        await self._log(guild, embed)

    # ------------------------------------------------------------- antiraid cmd

    @antiraid.command(name="enable", description="Включить антирейд")
    async def antiraid_enable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.settings.set(interaction.guild.id, "antiraid_enabled", True)
        await interaction.response.send_message(
            embed=success_embed("Антирейд включён. Порог входа: `/antiraid joins`."),
            ephemeral=True,
        )

    @antiraid.command(name="disable", description="Выключить антирейд")
    async def antiraid_disable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.settings.set(interaction.guild.id, "antiraid_enabled", False)
        await interaction.response.send_message(
            embed=success_embed("Антирейд выключен."),
            ephemeral=True,
        )

    @antiraid.command(name="action", description="Что делать с новыми аккаунтами во время рейда")
    @app_commands.choices(action=RAID_ACTIONS)
    async def antiraid_action(self, interaction: discord.Interaction, action: str) -> None:
        assert interaction.guild is not None
        if action not in {"kick", "ban", "timeout"}:
            raise UserError("Неизвестное действие.")
        await self.bot.settings.set(interaction.guild.id, "antiraid_action", action)
        await interaction.response.send_message(
            embed=success_embed(f"Действие антирейда: **{action}**."),
            ephemeral=True,
        )

    @antiraid.command(name="joins", description="Сколько входов за окно считаются рейдом")
    @app_commands.describe(count="От 2 до 100")
    async def antiraid_joins(self, interaction: discord.Interaction, count: int) -> None:
        assert interaction.guild is not None
        if not 2 <= count <= 100:
            raise UserError("Порог должен быть от 2 до 100.")
        await self.bot.settings.set(interaction.guild.id, "antiraid_joins", count)
        await interaction.response.send_message(
            embed=success_embed(f"Порог: **{count}** входов."),
            ephemeral=True,
        )

    @antiraid.command(name="window", description="Окно подсчёта входов в секундах")
    @app_commands.describe(seconds="От 10 до 600")
    async def antiraid_window(self, interaction: discord.Interaction, seconds: int) -> None:
        assert interaction.guild is not None
        if not 10 <= seconds <= 600:
            raise UserError("Окно должно быть от 10 до 600 секунд.")
        await self.bot.settings.set(interaction.guild.id, "antiraid_window", seconds)
        await interaction.response.send_message(
            embed=success_embed(f"Окно: **{format_duration(seconds)}**."),
            ephemeral=True,
        )

    @antiraid.command(name="min-age", description="Минимальный возраст аккаунта во время рейда")
    @app_commands.describe(days="Дней; 0 — не проверять возраст")
    async def antiraid_min_age(self, interaction: discord.Interaction, days: int) -> None:
        assert interaction.guild is not None
        if not 0 <= days <= 365:
            raise UserError("Возраст должен быть от 0 до 365 дней.")
        await self.bot.settings.set(interaction.guild.id, "antiraid_min_age_days", days)
        await interaction.response.send_message(
            embed=success_embed(f"Минимальный возраст: **{days}** дн."),
            ephemeral=True,
        )

    @antiraid.command(name="duration", description="Сколько длится режим рейда")
    @app_commands.describe(seconds="От 30 до 86400")
    async def antiraid_duration(self, interaction: discord.Interaction, seconds: int) -> None:
        assert interaction.guild is not None
        if not 30 <= seconds <= 86400:
            raise UserError("Длительность должна быть от 30 секунд до 24 часов.")
        await self.bot.settings.set(interaction.guild.id, "antiraid_duration", seconds)
        await interaction.response.send_message(
            embed=success_embed(f"Режим рейда длится **{format_duration(seconds)}**."),
            ephemeral=True,
        )

    @antiraid.command(name="lockdown", description="Включить режим рейда вручную")
    @app_commands.describe(minutes="На сколько минут")
    async def antiraid_lockdown(self, interaction: discord.Interaction, minutes: int) -> None:
        assert interaction.guild is not None
        if not 1 <= minutes <= 1440:
            raise UserError("Локдаун — от 1 до 1440 минут.")
        until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        await self.bot.settings.set(interaction.guild.id, "raid_mode_until", to_iso(until))
        await self._log(
            interaction.guild,
            base_embed(
                title="🔒 Ручной локдаун",
                description=f"Включён {interaction.user.mention} до {discord.utils.format_dt(until, 'f')}",
                color=COLOR_DANGER,
            ),
        )
        await interaction.response.send_message(
            embed=success_embed(f"Локдаун включён до {discord.utils.format_dt(until, 'f')}."),
            ephemeral=True,
        )

    @antiraid.command(name="unlock", description="Снять режим рейда")
    async def antiraid_unlock(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.settings.set(interaction.guild.id, "raid_mode_until", None)
        await interaction.response.send_message(
            embed=success_embed("Режим рейда снят."),
            ephemeral=True,
        )

    @antiraid.command(name="status", description="Показать настройки антирейда")
    async def antiraid_status(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        settings = self.bot.settings

        enabled = await settings.get_bool(guild.id, "antiraid_enabled", False)
        joins = await settings.get_int(guild.id, "antiraid_joins", 8)
        window = await settings.get_int(guild.id, "antiraid_window", 60)
        min_age = await settings.get_int(guild.id, "antiraid_min_age_days", 7)
        action = str(await settings.get(guild.id, "antiraid_action", "kick"))
        duration = await settings.get_int(guild.id, "antiraid_duration", 300)
        until = await self._raid_until(guild.id)

        embed = base_embed(title="🛡️ Антирейд", color=COLOR_MUTED)
        embed.add_field(name="Статус", value="✅ включён" if enabled else "❌ выключен", inline=True)
        embed.add_field(name="Порог", value=f"{joins} за {format_duration(window)}", inline=True)
        embed.add_field(name="Действие", value=action, inline=True)
        embed.add_field(name="Мин. возраст", value=f"{min_age} дн.", inline=True)
        embed.add_field(name="Длительность", value=format_duration(duration), inline=True)
        embed.add_field(
            name="Режим рейда",
            value=f"активен до {discord.utils.format_dt(until, 'f')}" if until else "не активен",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------- antispam cmd

    @antispam.command(name="enable", description="Включить антиспам")
    async def antispam_enable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.settings.set(interaction.guild.id, "antispam_enabled", True)
        await interaction.response.send_message(
            embed=success_embed("Антиспам включён."),
            ephemeral=True,
        )

    @antispam.command(name="disable", description="Выключить антиспам")
    async def antispam_disable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.settings.set(interaction.guild.id, "antispam_enabled", False)
        await interaction.response.send_message(
            embed=success_embed("Антиспам выключен."),
            ephemeral=True,
        )

    @antispam.command(name="count", description="Сколько сообщений за окно считать спамом")
    @app_commands.describe(count="От 2 до 50")
    async def antispam_count(self, interaction: discord.Interaction, count: int) -> None:
        assert interaction.guild is not None
        if not 2 <= count <= 50:
            raise UserError("Порог должен быть от 2 до 50.")
        await self.bot.settings.set(interaction.guild.id, "antispam_count", count)
        await interaction.response.send_message(
            embed=success_embed(f"Порог антиспама: **{count}** сообщений."),
            ephemeral=True,
        )

    @antispam.command(name="window", description="Окно подсчёта сообщений в секундах")
    @app_commands.describe(seconds="От 2 до 120")
    async def antispam_window(self, interaction: discord.Interaction, seconds: int) -> None:
        assert interaction.guild is not None
        if not 2 <= seconds <= 120:
            raise UserError("Окно должно быть от 2 до 120 секунд.")
        await self.bot.settings.set(interaction.guild.id, "antispam_window", seconds)
        await interaction.response.send_message(
            embed=success_embed(f"Окно антиспама: **{format_duration(seconds)}**."),
            ephemeral=True,
        )

    @antispam.command(name="action", description="Что делать со спамером")
    @app_commands.choices(action=SPAM_ACTIONS)
    async def antispam_action(self, interaction: discord.Interaction, action: str) -> None:
        assert interaction.guild is not None
        if action not in {"delete", "timeout"}:
            raise UserError("Неизвестное действие.")
        await self.bot.settings.set(interaction.guild.id, "antispam_action", action)
        await interaction.response.send_message(
            embed=success_embed(f"Действие антиспама: **{action}**."),
            ephemeral=True,
        )

    @antispam.command(name="timeout", description="Длительность тайм-аута за спам в секундах")
    @app_commands.describe(seconds="От 60 до 604800")
    async def antispam_timeout(self, interaction: discord.Interaction, seconds: int) -> None:
        assert interaction.guild is not None
        if not 60 <= seconds <= 28 * 24 * 60 * 60:
            raise UserError("Тайм-аут — от 1 минуты до 28 дней.")
        await self.bot.settings.set(interaction.guild.id, "antispam_timeout", seconds)
        await interaction.response.send_message(
            embed=success_embed(f"Тайм-аут за спам: **{format_duration(seconds)}**."),
            ephemeral=True,
        )

    @antispam.command(name="ignore-channel", description="Не проверять канал на спам")
    @app_commands.describe(channel="Канал-исключение")
    async def antispam_ignore_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        assert interaction.guild is not None
        ignored = [int(item) for item in await self.bot.settings.get_list(interaction.guild.id, "antispam_ignored_channels")]
        if channel.id in ignored:
            raise UserError(f"{channel.mention} уже в исключениях.")
        ignored.append(channel.id)
        await self.bot.settings.set(interaction.guild.id, "antispam_ignored_channels", ignored)
        await interaction.response.send_message(
            embed=success_embed(f"{channel.mention} больше не проверяется антиспамом."),
            ephemeral=True,
        )

    @antispam.command(name="unignore-channel", description="Снова проверять канал")
    @app_commands.describe(channel="Канал, который нужно вернуть")
    async def antispam_unignore_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        assert interaction.guild is not None
        ignored = [int(item) for item in await self.bot.settings.get_list(interaction.guild.id, "antispam_ignored_channels")]
        if channel.id not in ignored:
            raise UserError(f"{channel.mention} не был в исключениях.")
        ignored.remove(channel.id)
        await self.bot.settings.set(interaction.guild.id, "antispam_ignored_channels", ignored)
        await interaction.response.send_message(
            embed=success_embed(f"{channel.mention} снова проверяется антиспамом."),
            ephemeral=True,
        )

    @antispam.command(name="status", description="Показать настройки антиспама")
    async def antispam_status(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        settings = self.bot.settings

        enabled = await settings.get_bool(guild.id, "antispam_enabled", False)
        count = await settings.get_int(guild.id, "antispam_count", 5)
        window = await settings.get_int(guild.id, "antispam_window", 10)
        action = str(await settings.get(guild.id, "antispam_action", "timeout"))
        timeout_seconds = await settings.get_int(guild.id, "antispam_timeout", 600)
        ignored = await settings.get_list(guild.id, "antispam_ignored_channels")

        embed = base_embed(title="🚫 Антиспам", color=COLOR_MUTED)
        embed.add_field(name="Статус", value="✅ включён" if enabled else "❌ выключен", inline=True)
        embed.add_field(name="Порог", value=f"{count} за {format_duration(window)}", inline=True)
        embed.add_field(name="Действие", value=action, inline=True)
        embed.add_field(name="Тайм-аут", value=format_duration(timeout_seconds), inline=True)
        embed.add_field(
            name="Исключения",
            value=", ".join(f"<#{int(item)}>" for item in ignored[:10]) if ignored else "—",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Security(bot))

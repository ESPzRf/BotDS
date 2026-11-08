"""Временные баны и тайм-ауты с автоматическим снятием."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.discord_utils import resolve_modlog_channel
from core.embeds import (
    COLOR_DANGER,
    COLOR_MUTED,
    COLOR_WARNING,
    base_embed,
    success_embed,
)
from core.errors import UserError
from core.moderation import apply_timeout, ensure_can_moderate
from core.repositories import TempAction, to_iso
from core.timeparse import format_duration, parse_duration
from core.utils import truncate

log = logging.getLogger(__name__)

MAX_TEMPBAN_SECONDS = 365 * 24 * 60 * 60
MAX_TIMEOUT_SECONDS = 28 * 24 * 60 * 60


class TempActions(commands.Cog):
    tempactions = app_commands.Group(
        name="tempactions",
        description="Временные наказания",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        if self.bot.is_ready() and not self._check_due.is_running():
            self._check_due.start()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self._check_due.is_running():
            self._check_due.start()

    def cog_unload(self) -> None:
        self._check_due.cancel()

    # ------------------------------------------------------------------ loop

    @tasks.loop(seconds=30)
    async def _check_due(self) -> None:
        await self.bot.wait_until_ready()
        try:
            now = to_iso(datetime.now(timezone.utc))
            due = await self.bot.repos.temp_actions.due(now)
            for action in due:
                guild = self.bot.get_guild(action.guild_id)
                if guild is None:
                    await self.bot.repos.temp_actions.deactivate(action.id)
                    continue

                success, note = await self._release(guild, action)
                if not success:
                    log.warning(
                        "Не удалось снять временное наказание #%s (%s): %s",
                        action.id,
                        action.action,
                        note,
                    )
                    continue
                await self.bot.repos.temp_actions.deactivate(action.id)

                embed = base_embed(title="⏰ Временное наказание истекло", color=COLOR_MUTED)
                embed.add_field(name="Участник", value=f"<@{action.user_id}> (`{action.user_id}`)", inline=True)
                embed.add_field(name="Действие", value=action.action, inline=True)
                embed.add_field(name="Результат", value=note, inline=False)
                embed.add_field(name="Причина", value=truncate(action.reason, 1024), inline=False)
                await self._log(guild, embed)
        except Exception:  # noqa: BLE001 - цикл не должен умирать
            log.exception("Не удалось проверить временные наказания")

    async def _release(self, guild: discord.Guild, action: TempAction) -> tuple[bool, str]:
        reason = f"Временное наказание #{action.id} истекло"
        try:
            if action.action == "ban":
                await guild.unban(discord.Object(id=action.user_id), reason=reason)
                return True, "бан снят"
            if action.action == "timeout":
                member = guild.get_member(action.user_id)
                if member is None:
                    try:
                        member = await guild.fetch_member(action.user_id)
                    except (discord.NotFound, discord.HTTPException):
                        return True, "участник уже не на сервере"
                await member.timeout(None, reason=reason)
                return True, "тайм-аут снят"
        except discord.NotFound:
            return True, "наказание уже снято"
        except (discord.Forbidden, discord.HTTPException) as exc:
            return False, f"не удалось снять: {exc}"
        return False, "неизвестное действие"

    async def _log(self, guild: discord.Guild, embed: discord.Embed) -> None:
        channel = await resolve_modlog_channel(self.bot, guild)
        if channel is None:
            return
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            log.debug("Не удалось отправить лог временного наказания", exc_info=True)

    # --------------------------------------------------------------- commands

    @tempactions.command(name="ban", description="Забанить участника на время")
    @app_commands.describe(member="Участник", duration="Например 1h, 2d, 1w", reason="Причина")
    @app_commands.default_permissions(ban_members=True)
    async def tempban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        duration: str,
        reason: str = "Временный бан",
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        await ensure_can_moderate(interaction, member)

        try:
            seconds = parse_duration(duration)
        except ValueError as exc:
            raise UserError(f"Не удалось разобрать длительность: {exc}") from exc
        if not 60 <= seconds <= MAX_TEMPBAN_SECONDS:
            raise UserError("Длительность временного бана — от 1 минуты до 365 дней.")

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await guild.ban(member, reason=f"Временный бан: {reason}")
        except discord.Forbidden as exc:
            raise UserError("У бота нет прав банить участников.") from exc
        except discord.HTTPException as exc:
            raise UserError(f"Discord отклонил бан: {exc}") from exc

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        action_id = await self.bot.repos.temp_actions.add(
            guild.id,
            member.id,
            interaction.user.id,
            "ban",
            reason,
            expires_at,
        )

        embed = success_embed(
            f"{member} забанен на **{format_duration(seconds)}**.\n"
            f"ID наказания: `{action_id}`, снятие: {discord.utils.format_dt(expires_at, 'f')}"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        log_embed = base_embed(title="🔨 Временный бан", color=COLOR_DANGER)
        log_embed.add_field(name="Участник", value=f"{member} (`{member.id}`)", inline=True)
        log_embed.add_field(name="Модератор", value=f"{interaction.user} (`{interaction.user.id}`)", inline=True)
        log_embed.add_field(name="До", value=discord.utils.format_dt(expires_at, "f"), inline=True)
        log_embed.add_field(name="Причина", value=truncate(reason, 1024), inline=False)
        await self._log(guild, log_embed)

    @tempactions.command(name="mute", description="Выдать тайм-аут на время")
    @app_commands.describe(member="Участник", duration="Например 10m, 1h, 2d", reason="Причина")
    @app_commands.default_permissions(moderate_members=True)
    async def tempmute(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        duration: str,
        reason: str = "Тайм-аут",
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        await ensure_can_moderate(interaction, member)

        try:
            seconds = parse_duration(duration)
        except ValueError as exc:
            raise UserError(f"Не удалось разобрать длительность: {exc}") from exc
        if not 60 <= seconds <= MAX_TIMEOUT_SECONDS:
            raise UserError("Длительность тайм-аута — от 1 минуты до 28 дней.")

        await interaction.response.defer(ephemeral=True, thinking=True)
        applied = await apply_timeout(member, seconds, f"Тайм-аут: {reason}")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=applied)
        action_id = await self.bot.repos.temp_actions.add(
            guild.id,
            member.id,
            interaction.user.id,
            "timeout",
            reason,
            expires_at,
        )

        await interaction.followup.send(
            embed=success_embed(
                f"{member} получил тайм-аут на **{format_duration(applied)}**.\n"
                f"ID наказания: `{action_id}`"
            ),
            ephemeral=True,
        )

        log_embed = base_embed(title="🔇 Тайм-аут", color=COLOR_WARNING)
        log_embed.add_field(name="Участник", value=f"{member} (`{member.id}`)", inline=True)
        log_embed.add_field(name="Модератор", value=f"{interaction.user} (`{interaction.user.id}`)", inline=True)
        log_embed.add_field(name="До", value=discord.utils.format_dt(expires_at, "f"), inline=True)
        log_embed.add_field(name="Причина", value=truncate(reason, 1024), inline=False)
        await self._log(guild, log_embed)

    @tempactions.command(name="list", description="Активные временные наказания")
    @app_commands.default_permissions(moderate_members=True)
    async def tempactions_list(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        actions = await self.bot.repos.temp_actions.active_for_guild(guild.id)
        if not actions:
            await interaction.response.send_message(
                embed=base_embed(
                    title="⏰ Временные наказания",
                    description="Активных наказаний нет.",
                    color=COLOR_MUTED,
                ),
                ephemeral=True,
            )
            return

        embed = base_embed(title="⏰ Временные наказания", color=COLOR_MUTED)
        for action in actions[:15]:
            embed.add_field(
                name=f"#{action.id} · {action.action} · <@{action.user_id}>",
                value=(
                    f"До: {discord.utils.format_dt(action.expires_at, 'R')}\n"
                    f"Причина: {truncate(action.reason, 200)}"
                ),
                inline=False,
            )
        if len(actions) > 15:
            embed.set_footer(text=f"Показаны первые 15 из {len(actions)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tempactions.command(name="cancel", description="Снять временное наказание досрочно")
    @app_commands.describe(action_id="ID из /tempactions list")
    @app_commands.default_permissions(moderate_members=True)
    async def tempactions_cancel(self, interaction: discord.Interaction, action_id: int) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        action = await self.bot.repos.temp_actions.get(action_id)
        if action is None or action.guild_id != guild.id:
            raise UserError(f"Наказание #{action_id} не найдено.")
        if not action.active:
            raise UserError(f"Наказание #{action_id} уже снято.")

        await interaction.response.defer(ephemeral=True, thinking=True)
        note = await self._release(guild, action)
        await self.bot.repos.temp_actions.deactivate(action.id)
        await interaction.followup.send(
            embed=success_embed(f"Наказание **#{action.id}** снято: {note}."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TempActions(bot))

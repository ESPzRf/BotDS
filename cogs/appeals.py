"""Апелляции предупреждений: заявка участника и решение поддержки."""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.discord_utils import resolve_channel, resolve_modlog_channel
from core.embeds import (
    COLOR_DANGER,
    COLOR_MUTED,
    COLOR_SUCCESS,
    COLOR_WARNING,
    base_embed,
    success_embed,
    warning_embed,
)
from core.errors import UserError
from core.repositories import Appeal, Warning
from core.utils import truncate

log = logging.getLogger(__name__)


class AppealReviewView(discord.ui.View):
    def __init__(self, cog: "Appeals", appeal_id: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.appeal_id = appeal_id

        approve = discord.ui.Button(
            label="Одобрить",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"appeal:approve:{appeal_id}",
        )
        approve.callback = self._approve
        self.add_item(approve)

        deny = discord.ui.Button(
            label="Отклонить",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"appeal:deny:{appeal_id}",
        )
        deny.callback = self._deny
        self.add_item(deny)

    async def _approve(self, interaction: discord.Interaction) -> None:
        await self.cog.review(interaction, self.appeal_id, approve=True)

    async def _deny(self, interaction: discord.Interaction) -> None:
        await self.cog.review(interaction, self.appeal_id, approve=False)


class Appeals(commands.Cog):
    appeals = app_commands.Group(
        name="appeals",
        description="Настройка апелляций",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        try:
            pending = await self.bot.repos.appeals.all_pending()
        except Exception:  # noqa: BLE001
            log.exception("Не удалось восстановить апелляции")
            return
        for appeal in pending:
            try:
                self.bot.add_view(AppealReviewView(self, appeal.id))
            except Exception:  # noqa: BLE001
                log.exception("Не удалось восстановить апелляцию #%s", appeal.id)

    # ---------------------------------------------------------------- embeds

    def _embed(self, appeal: Appeal, warning: Warning, user: discord.User | None) -> discord.Embed:
        embed = base_embed(
            title=f"⚖️ Апелляция #{appeal.id}",
            description=f"Предупреждение #{warning.id}",
            color=COLOR_WARNING,
        )
        embed.add_field(
            name="Участник",
            value=f"<@{appeal.user_id}> (`{appeal.user_id}`)",
            inline=True,
        )
        embed.add_field(name="Статус", value=appeal.status, inline=True)
        embed.add_field(name="Создана", value=discord.utils.format_dt(appeal.created_at, "R"), inline=True)
        embed.add_field(name="Причина предупреждения", value=truncate(warning.reason, 1024), inline=False)
        embed.add_field(name="Текст апелляции", value=truncate(appeal.reason, 1024), inline=False)
        if user is not None:
            embed.set_thumbnail(url=user.display_avatar.url)
        return embed

    async def _resolve_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        configured = await self.bot.settings.get(guild.id, "appeals_channel")
        channel = await resolve_channel(guild, configured)
        if isinstance(channel, discord.TextChannel):
            return channel
        return await resolve_modlog_channel(self.bot, guild)

    async def _edit_message(self, appeal: Appeal, embed: discord.Embed) -> None:
        if appeal.channel_id is None or appeal.message_id is None:
            return
        guild = self.bot.get_guild(appeal.guild_id)
        if guild is None:
            return
        channel = guild.get_channel(appeal.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(appeal.message_id)
            await message.edit(embed=embed, view=None)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            log.debug("Не удалось обновить сообщение апелляции", exc_info=True)

    # ---------------------------------------------------------------- review

    async def review(self, interaction: discord.Interaction, appeal_id: int, *, approve: bool) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return
        if not (
            member.guild_permissions.manage_guild
            or member.guild_permissions.moderate_members
            or member.guild_permissions.manage_messages
        ):
            await interaction.response.send_message(
                embed=warning_embed("Рассматривать апелляции может только поддержка."),
                ephemeral=True,
            )
            return

        appeal = await self.bot.repos.appeals.get(appeal_id)
        if appeal is None or appeal.guild_id != guild.id:
            await interaction.response.send_message(
                embed=warning_embed("Апелляция не найдена."),
                ephemeral=True,
            )
            return
        if appeal.user_id == member.id:
            await interaction.response.send_message(
                embed=warning_embed("Нельзя рассматривать собственную апелляцию."),
                ephemeral=True,
            )
            return
        if appeal.status != "pending":
            await interaction.response.send_message(
                embed=warning_embed("Эта апелляция уже рассмотрена."),
                ephemeral=True,
            )
            return

        warning = await self.bot.repos.warnings.get(appeal.warning_id)
        if warning is None or warning.guild_id != guild.id:
            await interaction.response.send_message(
                embed=warning_embed("Предупреждение не найдено."),
                ephemeral=True,
            )
            return

        status = "approved" if approve else "denied"
        if not await self.bot.repos.appeals.set_status(appeal.id, status, member.id):
            await interaction.response.send_message(
                embed=warning_embed("Эту апелляцию уже рассмотрел другой модератор."),
                ephemeral=True,
            )
            return

        if approve:
            await self.bot.repos.warnings.deactivate(warning.id, guild.id)
            status_text = "✅ Апелляция одобрена, предупреждение снято."
        else:
            status_text = "❌ Апелляция отклонена."

        updated = await self.bot.repos.appeals.get(appeal.id)
        if updated is not None:
            user = self.bot.get_user(appeal.user_id)
            await self._edit_message(updated, self._embed(updated, warning, user))

        await interaction.response.send_message(embed=success_embed(status_text), ephemeral=True)

        try:
            user = self.bot.get_user(appeal.user_id) or await self.bot.fetch_user(appeal.user_id)
            await user.send(
                embed=base_embed(
                    title=f"⚖️ Апелляция #{appeal.id}",
                    description=status_text,
                    color=COLOR_SUCCESS if approve else COLOR_DANGER,
                )
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

        log_embed = base_embed(title="⚖️ Решение по апелляции", color=COLOR_MUTED)
        log_embed.add_field(name="Апелляция", value=f"#{appeal.id}", inline=True)
        log_embed.add_field(name="Решение", value="одобрена" if approve else "отклонена", inline=True)
        log_embed.add_field(name="Рассмотрел", value=f"{member} (`{member.id}`)", inline=True)
        await self._send_log(guild, log_embed)

    async def _send_log(self, guild: discord.Guild, embed: discord.Embed) -> None:
        channel = await resolve_modlog_channel(self.bot, guild)
        if channel is None:
            return
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            log.debug("Не удалось отправить лог апелляции", exc_info=True)

    # --------------------------------------------------------------- commands

    @app_commands.command(name="appeal", description="Подать апелляцию на предупреждение")
    @app_commands.describe(warning_id="ID предупреждения из /warnings", reason="Почему стоит снять предупреждение")
    async def appeal(
        self,
        interaction: discord.Interaction,
        warning_id: int,
        reason: str,
    ) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            raise UserError("Команда доступна только на сервере.")

        warning = await self.bot.repos.warnings.get(warning_id)
        if warning is None or warning.guild_id != guild.id:
            raise UserError(f"Предупреждение #{warning_id} не найдено.")
        if warning.user_id != member.id:
            raise UserError("Можно апеллировать только свои предупреждения.")
        if not warning.active:
            raise UserError("Это предупреждение уже снято.")

        pending = await self.bot.repos.appeals.pending_for_warning(warning_id)
        if pending is not None:
            raise UserError(f"По предупреждению #{warning_id} уже есть апелляция #{pending.id}.")

        channel = await self._resolve_channel(guild)
        if channel is None:
            raise UserError("Администратор не настроил канал для апелляций (/appeals channel).")

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            appeal_id = await self.bot.repos.appeals.add(
                guild.id, warning_id, member.id, reason.strip()[:1500]
            )
        except Exception as exc:  # noqa: BLE001 - гонка unique-индекса pending
            pending = await self.bot.repos.appeals.pending_for_warning(warning_id)
            if pending is not None:
                raise UserError(
                    f"По предупреждению #{warning_id} уже есть апелляция #{pending.id}."
                ) from exc
            raise UserError("Не удалось создать апелляцию. Попробуйте ещё раз.") from exc
        appeal = await self.bot.repos.appeals.get(appeal_id)
        if appeal is None:  # pragma: no cover
            raise UserError("Не удалось создать апелляцию.")

        embed = self._embed(appeal, warning, member)
        try:
            message = await channel.send(
                embed=embed,
                view=AppealReviewView(self, appeal_id),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            raise UserError(f"Не удалось отправить апелляцию в канал: {exc}") from exc

        self.bot.add_view(AppealReviewView(self, appeal_id))
        await self.bot.repos.appeals.set_message(appeal_id, channel.id, message.id)

        await interaction.followup.send(
            embed=success_embed(
                f"Апелляция **#{appeal_id}** отправлена. Решение придёт вам в личные сообщения."
            ),
            ephemeral=True,
        )

    @appeals.command(name="list", description="Ожидающие апелляции")
    @app_commands.default_permissions(manage_messages=True)
    async def appeals_list(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        pending = await self.bot.repos.appeals.pending(guild.id)
        if not pending:
            await interaction.response.send_message(
                embed=base_embed(
                    title="⚖️ Апелляции",
                    description="Ожидающих апелляций нет.",
                    color=COLOR_MUTED,
                ),
                ephemeral=True,
            )
            return

        embed = base_embed(title="⚖️ Ожидающие апелляции", color=COLOR_WARNING)
        for appeal in pending[:15]:
            embed.add_field(
                name=f"#{appeal.id} · предупреждение #{appeal.warning_id}",
                value=f"Участник: <@{appeal.user_id}>\n{truncate(appeal.reason, 200)}",
                inline=False,
            )
        if len(pending) > 15:
            embed.set_footer(text=f"Показаны первые 15 из {len(pending)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @appeals.command(name="channel", description="Канал для апелляций")
    @app_commands.describe(channel="Канал; без параметра — канал модерации")
    @app_commands.default_permissions(manage_guild=True)
    async def appeals_channel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        if channel is None:
            await self.bot.settings.set(guild.id, "appeals_channel", None)
            await interaction.response.send_message(
                embed=success_embed("Апелляции будут идти в канал модерации."),
                ephemeral=True,
            )
            return

        permissions = channel.permissions_for(guild.me)
        if not permissions.send_messages or not permissions.embed_links:
            raise UserError(f"У бота нет прав писать embed-сообщения в {channel.mention}.")

        await self.bot.settings.set(guild.id, "appeals_channel", channel.id)
        await interaction.response.send_message(
            embed=success_embed(f"Апелляции будут отправляться в {channel.mention}."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Appeals(bot))

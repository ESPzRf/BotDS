"""Тикеты: панель с кнопкой, приватные каналы, закрытие с транскриптом."""

from __future__ import annotations

import io
import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.discord_utils import ConfirmView, resolve_channel, resolve_modlog_channel
from core.embeds import (
    COLOR_MUTED,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    base_embed,
    success_embed,
    warning_embed,
)
from core.errors import UserError
from core.repositories import Ticket, TicketPanel
from core.utils import truncate

log = logging.getLogger(__name__)

COLOR_TICKET = 0x2B8A3E
TRANSCRIPT_LIMIT = 500


def ticket_channel_name(member: discord.Member) -> str:
    base = re.sub(r"[^0-9a-zA-Zа-яА-ЯёЁ_-]+", "-", member.name).strip("-").lower()
    if not base:
        base = f"user-{member.id}"
    return truncate(f"ticket-{base}", 90, suffix="")


class TicketPanelView(discord.ui.View):
    def __init__(self, cog: "Tickets", panel_id: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.panel_id = panel_id
        button = discord.ui.Button(
            label="Открыть тикет",
            style=discord.ButtonStyle.primary,
            emoji="🎫",
            custom_id=f"ticket:open:{panel_id}",
        )
        button.callback = self._open
        self.add_item(button)

    async def _open(self, interaction: discord.Interaction) -> None:
        await self.cog.open_ticket(interaction, self.panel_id)


class TicketControlView(discord.ui.View):
    def __init__(self, cog: "Tickets", ticket_id: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.ticket_id = ticket_id

        close = discord.ui.Button(
            label="Закрыть",
            style=discord.ButtonStyle.danger,
            emoji="🔒",
            custom_id=f"ticket:close:{ticket_id}",
        )
        close.callback = self._close
        self.add_item(close)

        claim = discord.ui.Button(
            label="Взять",
            style=discord.ButtonStyle.success,
            emoji="🙋",
            custom_id=f"ticket:claim:{ticket_id}",
        )
        claim.callback = self._claim
        self.add_item(claim)

    async def _close(self, interaction: discord.Interaction) -> None:
        await self.cog.request_close(interaction, self.ticket_id)

    async def _claim(self, interaction: discord.Interaction) -> None:
        await self.cog.claim_ticket(interaction, self.ticket_id)


class Tickets(commands.Cog):
    ticket = app_commands.Group(name="ticket", description="Система обращений в поддержку")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        try:
            panels = await self.bot.repos.tickets.all_panels()
            tickets = await self.bot.repos.tickets.all_open_tickets()
        except Exception:  # noqa: BLE001
            log.exception("Не удалось восстановить тикеты")
            return

        for panel in panels:
            if panel.message_id:
                try:
                    self.bot.add_view(TicketPanelView(self, panel.id))
                except Exception:  # noqa: BLE001
                    log.exception("Не удалось восстановить панель тикетов #%s", panel.id)
        for ticket in tickets:
            try:
                self.bot.add_view(TicketControlView(self, ticket.id))
            except Exception:  # noqa: BLE001
                log.exception("Не удалось восстановить тикет #%s", ticket.id)

        log.info("Восстановлено панелей тикетов: %s, открытых тикетов: %s", len(panels), len(tickets))

    # ------------------------------------------------------------------ embeds

    def _panel_embed(self, panel: TicketPanel, guild: discord.Guild) -> discord.Embed:
        embed = base_embed(
            title=panel.title,
            description=panel.description or "Нажмите кнопку ниже, чтобы открыть обращение.",
            color=COLOR_PRIMARY,
        )
        if panel.support_role_id:
            role = guild.get_role(panel.support_role_id)
            if role is not None:
                embed.add_field(name="Команда поддержки", value=role.mention, inline=False)
        embed.set_footer(text=f"Панель #{panel.id} • один открытый тикет на участника")
        return embed

    def _ticket_embed(self, ticket: Ticket, member: discord.Member, panel: TicketPanel | None) -> discord.Embed:
        embed = base_embed(
            title=f"🎫 Тикет #{ticket.id}",
            description=(
                "Опишите проблему одним сообщением — поддержка ответит здесь.\n"
                "Закрыть обращение можно кнопкой ниже."
            ),
            color=COLOR_TICKET,
        )
        embed.add_field(name="Пользователь", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Статус", value="🟢 Открыт", inline=True)
        embed.add_field(name="Создан", value=discord.utils.format_dt(ticket.created_at, "R"), inline=True)

        if ticket.claimed_by:
            embed.add_field(name="Взял в работу", value=f"<@{ticket.claimed_by}>", inline=True)
        if panel is not None and panel.support_role_id:
            role = member.guild.get_role(panel.support_role_id)
            if role is not None:
                embed.add_field(name="Поддержка", value=role.mention, inline=True)

        embed.set_footer(text="Кнопки ниже доступны поддержке и автору обращения")
        return embed

    async def _ticket_message(self, ticket: Ticket) -> discord.Message | None:
        if ticket.message_id is None:
            return None
        guild = self.bot.get_guild(ticket.guild_id)
        if guild is None:
            return None
        channel = guild.get_channel(ticket.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return None
        try:
            return await channel.fetch_message(ticket.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _refresh_ticket_message(self, ticket: Ticket) -> None:
        message = await self._ticket_message(ticket)
        if message is None:
            return
        guild = self.bot.get_guild(ticket.guild_id)
        member = guild.get_member(ticket.user_id) if guild else None
        if guild is None or member is None:
            return
        panel = await self.bot.repos.tickets.get_panel(ticket.panel_id) if ticket.panel_id else None
        try:
            await message.edit(embed=self._ticket_embed(ticket, member, panel))
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ------------------------------------------------------------------ staff

    @staticmethod
    def _is_staff(member: discord.Member, panel: TicketPanel | None) -> bool:
        if member.guild_permissions.manage_channels:
            return True
        if panel is not None and panel.support_role_id:
            return any(role.id == panel.support_role_id for role in member.roles)
        return False

    async def _log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        configured = await self.bot.settings.get(guild.id, "ticket_log_channel")
        channel = await resolve_channel(guild, configured)
        if isinstance(channel, discord.TextChannel):
            return channel
        return await resolve_modlog_channel(self.bot, guild)

    async def _log(self, guild: discord.Guild, embed: discord.Embed) -> None:
        channel = await self._log_channel(guild)
        if channel is None:
            return
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            log.debug("Не удалось отправить лог тикета", exc_info=True)

    async def _transcript(self, channel: discord.TextChannel) -> str:
        lines: list[str] = []
        try:
            async for message in channel.history(limit=TRANSCRIPT_LIMIT, oldest_first=True):
                content = message.content or ""
                if message.embeds:
                    content += " [embed]"
                for attachment in message.attachments:
                    content += f" [файл: {attachment.filename} — {attachment.url}]"
                lines.append(
                    f"[{message.created_at:%Y-%m-%d %H:%M:%S}] "
                    f"{message.author} ({message.author.id}): {content}"
                )
        except (discord.Forbidden, discord.HTTPException):
            lines.append("Не удалось загрузить историю сообщений.")
        return "\n".join(lines) or "Сообщений не было."

    # --------------------------------------------------------------- actions

    async def open_ticket(self, interaction: discord.Interaction, panel_id: int) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(
                embed=warning_embed("Тикеты работают только на сервере."),
                ephemeral=True,
            )
            return

        panel = await self.bot.repos.tickets.get_panel(panel_id)
        if panel is None or panel.guild_id != guild.id:
            await interaction.response.send_message(
                embed=warning_embed("Панель тикетов не найдена."),
                ephemeral=True,
            )
            return

        existing = await self.bot.repos.tickets.open_ticket_for(guild.id, member.id)
        if existing is not None:
            await interaction.response.send_message(
                embed=warning_embed(f"У вас уже есть открытый тикет: <#{existing.channel_id}>."),
                ephemeral=True,
            )
            return
        if guild.me is None or not guild.me.guild_permissions.manage_channels:
            await interaction.response.send_message(
                embed=warning_embed("У бота нет права «Управлять каналами» — тикет создать нельзя."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        category: discord.CategoryChannel | None = None
        if panel.category_id:
            resolved = guild.get_channel(panel.category_id)
            if isinstance(resolved, discord.CategoryChannel):
                category = resolved

        support_role = guild.get_role(panel.support_role_id) if panel.support_role_id else None

        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            ),
        }
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                manage_messages=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
            )
        if support_role is not None:
            overwrites[support_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
                attach_files=True,
            )

        try:
            channel = await guild.create_text_channel(
                ticket_channel_name(member),
                category=category,
                overwrites=overwrites,
                reason=f"Тикет от {member} ({member.id})",
            )
        except discord.Forbidden as exc:
            raise UserError("У бота нет прав создавать каналы (нужно право «Управлять каналами»).") from exc
        except discord.HTTPException as exc:
            raise UserError(f"Не удалось создать канал тикета: {exc}") from exc

        try:
            ticket_id = await self.bot.repos.tickets.create_ticket(
                guild.id,
                panel.id,
                channel.id,
                member.id,
            )
        except Exception as exc:  # noqa: BLE001 - чаще всего гонка unique-индекса
            try:
                await channel.delete(reason="Не удалось создать тикет")
            except (discord.Forbidden, discord.HTTPException):
                pass
            existing = await self.bot.repos.tickets.open_ticket_for(guild.id, member.id)
            if existing is not None:
                raise UserError(f"У вас уже есть открытый тикет: <#{existing.channel_id}>.") from exc
            raise UserError("Не удалось создать тикет. Попробуйте ещё раз.") from exc
        ticket = await self.bot.repos.tickets.get_ticket(ticket_id)
        if ticket is None:  # pragma: no cover
            raise UserError("Не удалось создать запись тикета.")

        view = TicketControlView(self, ticket_id)
        message = await channel.send(
            content=member.mention,
            embed=self._ticket_embed(ticket, member, panel),
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        self.bot.add_view(view)
        await self.bot.repos.tickets.set_ticket_message(ticket_id, message.id)

        log_embed = base_embed(title="🎫 Тикет открыт", color=COLOR_SUCCESS)
        log_embed.add_field(name="Пользователь", value=f"{member} (`{member.id}`)", inline=True)
        log_embed.add_field(name="Канал", value=channel.mention, inline=True)
        log_embed.add_field(name="Панель", value=f"#{panel.id}", inline=True)
        await self._log(guild, log_embed)

        await interaction.followup.send(
            embed=success_embed(f"Тикет открыт: {channel.mention}"),
            ephemeral=True,
        )

    async def claim_ticket(self, interaction: discord.Interaction, ticket_id: int) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return

        ticket = await self.bot.repos.tickets.get_ticket(ticket_id)
        if ticket is None or ticket.status != "open" or ticket.channel_id != interaction.channel_id:
            await interaction.response.send_message(
                embed=warning_embed("Тикет не найден или уже закрыт."),
                ephemeral=True,
            )
            return

        panel = await self.bot.repos.tickets.get_panel(ticket.panel_id) if ticket.panel_id else None
        if not self._is_staff(member, panel):
            await interaction.response.send_message(
                embed=warning_embed("Взять тикет может только поддержка."),
                ephemeral=True,
            )
            return
        if ticket.claimed_by is not None:
            await interaction.response.send_message(
                embed=warning_embed(f"Тикет уже взял <@{ticket.claimed_by}>."),
                ephemeral=True,
            )
            return

        await self.bot.repos.tickets.claim_ticket(ticket_id, member.id)
        updated = await self.bot.repos.tickets.get_ticket(ticket_id)
        if updated is not None:
            await self._refresh_ticket_message(updated)

        await interaction.response.send_message(
            embed=success_embed(f"Вы взяли тикет **#{ticket_id}**."),
            ephemeral=True,
        )

    async def request_close(self, interaction: discord.Interaction, ticket_id: int) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return

        ticket = await self.bot.repos.tickets.get_ticket(ticket_id)
        if ticket is None or ticket.status != "open":
            await interaction.response.send_message(
                embed=warning_embed("Тикет уже закрыт."),
                ephemeral=True,
            )
            return

        panel = await self.bot.repos.tickets.get_panel(ticket.panel_id) if ticket.panel_id else None
        if member.id != ticket.user_id and not self._is_staff(member, panel):
            await interaction.response.send_message(
                embed=warning_embed("Закрыть тикет может автор или поддержка."),
                ephemeral=True,
            )
            return

        view = ConfirmView(member.id, confirm_label="Закрыть тикет")
        await interaction.response.send_message(
            embed=warning_embed(
                f"Закрыть тикет **#{ticket.id}**? Канал будет удалён, транскрипт попадёт в лог."
            ),
            view=view,
            ephemeral=True,
        )
        await view.wait()

        if view.value is not True:
            await interaction.edit_original_response(
                embed=base_embed(title="Закрытие отменено", color=COLOR_MUTED),
                view=None,
            )
            return

        await self._close_ticket(guild, ticket, member, interaction)

    async def _close_ticket(
        self,
        guild: discord.Guild,
        ticket: Ticket,
        closed_by: discord.Member,
        interaction: discord.Interaction | None = None,
    ) -> None:
        channel = guild.get_channel(ticket.channel_id)
        changed = await self.bot.repos.tickets.close_ticket(ticket.id, closed_by.id)
        if not changed:
            raise UserError("Тикет уже закрыт.")

        transcript = await self._transcript(channel) if isinstance(channel, discord.TextChannel) else "Канал уже удалён."

        log_channel = await self._log_channel(guild)
        if log_channel is not None:
            embed = base_embed(title=f"🔒 Тикет #{ticket.id} закрыт", color=COLOR_MUTED)
            embed.add_field(name="Автор", value=f"<@{ticket.user_id}>", inline=True)
            embed.add_field(name="Закрыл", value=f"{closed_by} (`{closed_by.id}`)", inline=True)
            embed.add_field(name="Сообщений", value=str(len(transcript.splitlines())), inline=True)
            file = discord.File(
                io.BytesIO(transcript.encode("utf-8")),
                filename=f"ticket-{ticket.id}.txt",
            )
            try:
                await log_channel.send(embed=embed, file=file)
            except (discord.Forbidden, discord.HTTPException):
                log.debug("Не удалось отправить транскрипт тикета", exc_info=True)

        if interaction is not None:
            try:
                await interaction.edit_original_response(
                    embed=success_embed(f"Тикет **#{ticket.id}** закрыт."),
                    view=None,
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        if isinstance(channel, discord.TextChannel):
            try:
                await channel.delete(reason=f"Тикет #{ticket.id} закрыт")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        try:
            user = self.bot.get_user(ticket.user_id) or await self.bot.fetch_user(ticket.user_id)
            await user.send(
                embed=base_embed(
                    title="🎫 Тикет закрыт",
                    description=f"Обращение **#{ticket.id}** на сервере **{guild.name}** закрыто.",
                    color=COLOR_MUTED,
                )
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    # --------------------------------------------------------------- commands

    @ticket.command(name="setup", description="Создать панель тикетов в канале")
    @app_commands.describe(
        channel="Канал для панели",
        title="Заголовок панели",
        description="Описание/инструкция",
        support_role="Роль поддержки, которая видит тикеты",
        category="Категория, в которой создавать каналы тикетов",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def ticket_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        title: str,
        description: Optional[str] = None,
        support_role: Optional[discord.Role] = None,
        category: Optional[discord.CategoryChannel] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        permissions = channel.permissions_for(guild.me)
        if not permissions.send_messages or not permissions.embed_links:
            raise UserError(f"У бота нет прав писать embed-сообщения в {channel.mention}.")
        if support_role is not None and support_role.is_default():
            raise UserError("Роль поддержки не может быть @everyone.")
        if guild.me is None or not guild.me.guild_permissions.manage_channels:
            raise UserError("У бота нет права «Управлять каналами» — без него тикеты не создать.")

        await interaction.response.defer(ephemeral=True, thinking=True)

        panel_id = await self.bot.repos.tickets.create_panel(
            guild.id,
            channel.id,
            title.strip()[:256] or "Поддержка",
            (description or "").strip()[:2048],
            category.id if category else None,
            support_role.id if support_role else None,
        )
        panel = await self.bot.repos.tickets.get_panel(panel_id)
        if panel is None:  # pragma: no cover
            raise UserError("Не удалось создать панель.")

        view = TicketPanelView(self, panel_id)
        message = await channel.send(
            embed=self._panel_embed(panel, guild),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.bot.add_view(view)
        await self.bot.repos.tickets.set_panel_message(panel_id, message.id)

        await interaction.followup.send(
            embed=success_embed(
                f"Панель тикетов **#{panel_id}** создана в {channel.mention}.\n"
                f"[Перейти к сообщению]({message.jump_url})"
            ),
            ephemeral=True,
        )

    @ticket.command(name="close", description="Закрыть тикет в текущем канале")
    @app_commands.default_permissions(manage_channels=True)
    async def ticket_close(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or interaction.channel_id is None:
            raise UserError("Команда доступна только в канале тикета.")
        ticket = await self.bot.repos.tickets.ticket_by_channel(interaction.channel_id)
        if ticket is None or ticket.status != "open":
            raise UserError("Этот канал не является открытым тикетом.")
        await self.request_close(interaction, ticket.id)

    @ticket.command(name="add", description="Добавить участника в текущий тикет")
    @app_commands.describe(member="Кого добавить")
    @app_commands.default_permissions(manage_channels=True)
    async def ticket_add(self, interaction: discord.Interaction, member: discord.Member) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.channel, discord.TextChannel):
            raise UserError("Команда доступна только в текстовом канале тикета.")

        ticket = await self.bot.repos.tickets.ticket_by_channel(interaction.channel.id)
        if ticket is None or ticket.status != "open":
            raise UserError("Этот канал не является открытым тикетом.")
        panel = await self.bot.repos.tickets.get_panel(ticket.panel_id) if ticket.panel_id else None
        if not isinstance(interaction.user, discord.Member) or not self._is_staff(interaction.user, panel):
            raise UserError("Добавлять участников может только поддержка.")

        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.channel.set_permissions(
            member,
            overwrite=discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            ),
            reason=f"Добавлен в тикет #{ticket.id}",
        )
        await interaction.followup.send(
            embed=success_embed(f"{member.mention} добавлен в тикет."),
            ephemeral=True,
        )

    @ticket.command(name="remove", description="Убрать участника из текущего тикета")
    @app_commands.describe(member="Кого убрать")
    @app_commands.default_permissions(manage_channels=True)
    async def ticket_remove(self, interaction: discord.Interaction, member: discord.Member) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.channel, discord.TextChannel):
            raise UserError("Команда доступна только в текстовом канале тикета.")

        ticket = await self.bot.repos.tickets.ticket_by_channel(interaction.channel.id)
        if ticket is None or ticket.status != "open":
            raise UserError("Этот канал не является открытым тикетом.")
        panel = await self.bot.repos.tickets.get_panel(ticket.panel_id) if ticket.panel_id else None
        if not isinstance(interaction.user, discord.Member) or not self._is_staff(interaction.user, panel):
            raise UserError("Убирать участников может только поддержка.")
        if member.id == ticket.user_id:
            raise UserError("Нельзя убрать автора тикета.")

        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.channel.set_permissions(member, overwrite=None, reason=f"Удалён из тикета #{ticket.id}")
        await interaction.followup.send(
            embed=success_embed(f"{member.mention} убран из тикета."),
            ephemeral=True,
        )

    @ticket.command(name="panels", description="Список панелей тикетов")
    @app_commands.default_permissions(manage_guild=True)
    async def ticket_panels(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        panels = await self.bot.repos.tickets.panels(guild.id)
        if not panels:
            await interaction.response.send_message(
                embed=base_embed(
                    title="🎫 Панели тикетов",
                    description="Панелей нет. Создайте: `/ticket setup`.",
                    color=COLOR_MUTED,
                ),
                ephemeral=True,
            )
            return

        embed = base_embed(title="🎫 Панели тикетов", color=COLOR_MUTED)
        for panel in panels[:15]:
            value = f"Канал: <#{panel.channel_id}>\nРоль: <@&{panel.support_role_id}>" if panel.support_role_id else f"Канал: <#{panel.channel_id}>"
            if panel.message_id:
                value += f"\n[Сообщение](https://discord.com/channels/{guild.id}/{panel.channel_id}/{panel.message_id})"
            embed.add_field(name=f"#{panel.id} — {truncate(panel.title, 200)}", value=value, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ticket.command(name="list", description="Список открытых тикетов")
    @app_commands.default_permissions(manage_guild=True)
    async def ticket_list(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        tickets = await self.bot.repos.tickets.open_tickets(guild.id)
        if not tickets:
            await interaction.response.send_message(
                embed=base_embed(
                    title="🎫 Тикеты",
                    description="Открытых тикетов нет.",
                    color=COLOR_MUTED,
                ),
                ephemeral=True,
            )
            return

        embed = base_embed(title="🎫 Открытые тикеты", color=COLOR_MUTED)
        for ticket in tickets[:15]:
            claimed = f"<@{ticket.claimed_by}>" if ticket.claimed_by else "не взят"
            embed.add_field(
                name=f"#{ticket.id} — <#{ticket.channel_id}>",
                value=f"Автор: <@{ticket.user_id}>\nВзял: {claimed}",
                inline=False,
            )
        if len(tickets) > 15:
            embed.set_footer(text=f"Показаны первые 15 из {len(tickets)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ticket.command(name="delete", description="Удалить панель тикетов")
    @app_commands.describe(panel_id="ID панели из /ticket list")
    @app_commands.default_permissions(manage_guild=True)
    async def ticket_delete(self, interaction: discord.Interaction, panel_id: int) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        panel = await self.bot.repos.tickets.get_panel(panel_id)
        if panel is None or panel.guild_id != guild.id:
            raise UserError(f"Панель #{panel_id} не найдена.")

        await interaction.response.defer(ephemeral=True, thinking=True)
        if panel.message_id:
            channel = guild.get_channel(panel.channel_id)
            if isinstance(channel, discord.TextChannel):
                try:
                    message = await channel.fetch_message(panel.message_id)
                    await message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
        await self.bot.repos.tickets.delete_panel(panel_id)
        await interaction.followup.send(
            embed=success_embed(f"Панель тикетов **#{panel_id}** удалена."),
            ephemeral=True,
        )

    @ticket.command(name="log", description="Канал для логов тикетов и транскриптов")
    @app_commands.describe(channel="Канал; без параметра — канал модерации")
    @app_commands.default_permissions(manage_guild=True)
    async def ticket_log(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        if channel is None:
            await self.bot.settings.set(guild.id, "ticket_log_channel", None)
            await interaction.response.send_message(
                embed=success_embed("Логи тикетов будут идти в канал модерации."),
                ephemeral=True,
            )
            return

        permissions = channel.permissions_for(guild.me)
        if not permissions.send_messages or not permissions.attach_files:
            raise UserError(f"У бота нет прав писать файлы в {channel.mention}.")

        await self.bot.settings.set(guild.id, "ticket_log_channel", channel.id)
        await interaction.response.send_message(
            embed=success_embed(f"Логи и транскрипты тикетов: {channel.mention}."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tickets(bot))

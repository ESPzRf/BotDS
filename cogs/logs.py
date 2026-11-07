"""Ког логирования действий на сервере."""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.discord_utils import find_audit_entry, resolve_log_channel
from core.embeds import (
    COLOR_DANGER,
    COLOR_MUTED,
    COLOR_SUCCESS,
    COLOR_WARNING,
    base_embed,
)
from core.errors import UserError
from core.utils import truncate

log = logging.getLogger(__name__)

LOG_GROUPS: dict[str, str] = {
    "messages": "Сообщения",
    "members": "Участники",
    "roles": "Роли",
    "channels": "Каналы",
    "voice": "Голосовые каналы",
    "invites": "Приглашения",
    "moderation": "Модерация",
}


class Logs(commands.Cog):
    """Логи сервера: сообщения, участники, роли, каналы, войс, инвайты."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ utils

    def _embed(self, guild: discord.Guild, title: str, color: int) -> discord.Embed:
        embed = base_embed(title=title, color=color)
        embed.set_footer(text=truncate(f"{guild.name} • {guild.id}", 2048))
        return embed

    @staticmethod
    def _add(embed: discord.Embed, name: str, value: str | None, *, inline: bool = True) -> None:
        if value:
            embed.add_field(name=name, value=truncate(value, 1024), inline=inline)

    async def _send(self, guild: discord.Guild | None, group: str, embed: discord.Embed) -> None:
        if guild is None:
            return
        if not await self.bot.settings.get_bool(guild.id, f"log_{group}", True):
            return
        channel = await resolve_log_channel(self.bot, guild)
        if channel is None:
            return
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            log.debug("Не удалось отправить лог (%s) на сервере %s", group, guild.id, exc_info=True)

    # --------------------------------------------------------------- messages

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if self.bot.user is not None and message.author.id == self.bot.user.id:
            return

        embed = self._embed(message.guild, "🗑️ Сообщение удалено", COLOR_DANGER)
        self._add(embed, "Автор", f"{message.author} (`{message.author.id}`)")
        self._add(embed, "Канал", message.channel.mention)
        self._add(embed, "ID", f"`{message.id}`")
        self._add(
            embed,
            "Содержимое",
            truncate(message.content, 1000) or "*(пусто или сообщение не было в кэше)*",
            inline=False,
        )
        if message.attachments:
            files = "\n".join(f"• {item.filename}" for item in message.attachments[:10])
            self._add(embed, "Вложения", files, inline=False)

        await self._send(message.guild, "messages", embed)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]) -> None:
        if not messages or messages[0].guild is None:
            return
        guild = messages[0].guild
        authors = {message.author.id for message in messages}
        embed = self._embed(guild, "🧹 Массовое удаление сообщений", COLOR_DANGER)
        self._add(embed, "Канал", messages[0].channel.mention)
        self._add(embed, "Удалено сообщений", str(len(messages)))
        self._add(embed, "Уникальных авторов", str(len(authors)))
        await self._send(guild, "messages", embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.guild is None or before.author.bot:
            return
        if before.content == after.content:
            return

        embed = self._embed(before.guild, "✏️ Сообщение изменено", COLOR_WARNING)
        self._add(embed, "Автор", f"{before.author} (`{before.author.id}`)")
        self._add(embed, "Канал", before.channel.mention)
        self._add(embed, "Ссылка", f"[перейти]({after.jump_url})")
        self._add(embed, "Было", truncate(before.content, 1000) or "*(пусто)*", inline=False)
        self._add(embed, "Стало", truncate(after.content, 1000) or "*(пусто)*", inline=False)

        await self._send(before.guild, "messages", embed)

    # ---------------------------------------------------------------- members

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        embed = self._embed(member.guild, "📥 Участник присоединился", COLOR_SUCCESS)
        embed.set_thumbnail(url=member.display_avatar.url)
        self._add(embed, "Участник", f"{member} (`{member.id}`)")
        self._add(embed, "Аккаунт создан", discord.utils.format_dt(member.created_at, "R"))
        self._add(embed, "Участников на сервере", str(member.guild.member_count))

        age_days = (discord.utils.utcnow() - member.created_at).days
        if age_days < 7:
            self._add(embed, "⚠️ Внимание", f"Аккаунт создан {age_days} дн. назад", inline=False)

        await self._send(member.guild, "members", embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild = member.guild
        ban_entry = await find_audit_entry(guild, discord.AuditLogAction.ban, member.id, within_seconds=10)
        if ban_entry is not None:
            return  # событие бана логируется отдельно

        kick_entry = await find_audit_entry(guild, discord.AuditLogAction.kick, member.id)

        if kick_entry is not None:
            embed = self._embed(guild, "👢 Участник кикнут", COLOR_DANGER)
            self._add(
                embed,
                "Модератор",
                f"{kick_entry.user} (`{kick_entry.user.id}`)" if kick_entry.user else "неизвестен",
            )
            if kick_entry.reason:
                self._add(embed, "Причина", kick_entry.reason, inline=False)
        else:
            embed = self._embed(guild, "📤 Участник покинул сервер", COLOR_MUTED)
            self._add(embed, "Присоединился", discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "—")

        self._add(embed, "Участник", f"{member} (`{member.id}`)")
        roles = [role.mention for role in member.roles if not role.is_default()]
        self._add(embed, "Роли", ", ".join(roles[:20]) if roles else "—", inline=False)
        self._add(embed, "Участников на сервере", str(guild.member_count))

        await self._send(guild, "members", embed)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        changes: list[str] = []

        if before.nick != after.nick:
            changes.append(f"**Никнейм:** {before.nick or '*нет*'} → {after.nick or '*нет*'}")

        before_roles = {role.id for role in before.roles}
        after_roles = {role.id for role in after.roles}
        added = [after.guild.get_role(role_id) for role_id in after_roles - before_roles]
        removed = [after.guild.get_role(role_id) for role_id in before_roles - after_roles]
        added = [role for role in added if role is not None and not role.is_default()]
        removed = [role for role in removed if role is not None and not role.is_default()]
        if added:
            changes.append("**Выданы роли:** " + ", ".join(role.mention for role in added))
        if removed:
            changes.append("**Сняты роли:** " + ", ".join(role.mention for role in removed))

        before_timeout = getattr(before, "timed_out_until", None)
        after_timeout = getattr(after, "timed_out_until", None)
        if before_timeout != after_timeout:
            if after_timeout is None:
                changes.append("**Тайм-аут:** снят")
            else:
                changes.append(f"**Тайм-аут:** до {discord.utils.format_dt(after_timeout, 'f')}")

        if not changes:
            return

        embed = self._embed(after.guild, "🔄 Участник обновлён", COLOR_MUTED)
        embed.set_thumbnail(url=after.display_avatar.url)
        self._add(embed, "Участник", f"{after} (`{after.id}`)")
        embed.add_field(name="Изменения", value=truncate("\n".join(changes), 1024), inline=False)

        await self._send(after.guild, "members", embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        entry = await find_audit_entry(guild, discord.AuditLogAction.ban, user.id)
        embed = self._embed(guild, "🔨 Участник забанен", COLOR_DANGER)
        self._add(embed, "Участник", f"{user} (`{user.id}`)")
        if entry is not None:
            self._add(embed, "Модератор", f"{entry.user} (`{entry.user.id}`)" if entry.user else "неизвестен")
            if entry.reason:
                self._add(embed, "Причина", entry.reason, inline=False)
        await self._send(guild, "moderation", embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        entry = await find_audit_entry(guild, discord.AuditLogAction.unban, user.id)
        embed = self._embed(guild, "🔓 Участник разбанен", COLOR_SUCCESS)
        self._add(embed, "Участник", f"{user} (`{user.id}`)")
        if entry is not None and entry.user:
            self._add(embed, "Модератор", f"{entry.user} (`{entry.user.id}`)")
        await self._send(guild, "moderation", embed)

    # --------------------------------------------------------------- channels

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        embed = self._embed(channel.guild, "📁 Канал создан", COLOR_SUCCESS)
        self._add(embed, "Канал", f"{channel.mention} (`{channel.id}`)")
        self._add(embed, "Тип", str(channel.type))
        self._add(embed, "Категория", getattr(getattr(channel, "category", None), "name", None) or "—")
        await self._send(channel.guild, "channels", embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        embed = self._embed(channel.guild, "🗂️ Канал удалён", COLOR_DANGER)
        self._add(embed, "Канал", f"#{channel.name} (`{channel.id}`)")
        self._add(embed, "Тип", str(channel.type))
        self._add(embed, "Категория", getattr(getattr(channel, "category", None), "name", None) or "—")
        await self._send(channel.guild, "channels", embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ) -> None:
        changes: list[str] = []
        if before.name != after.name:
            changes.append(f"**Название:** `{before.name}` → `{after.name}`")

        before_topic = getattr(before, "topic", None)
        after_topic = getattr(after, "topic", None)
        if before_topic != after_topic:
            changes.append(f"**Тема:** {truncate(before_topic or '*нет*', 300)} → {truncate(after_topic or '*нет*', 300)}")

        before_nsfw = getattr(before, "nsfw", None)
        after_nsfw = getattr(after, "nsfw", None)
        if before_nsfw != after_nsfw:
            changes.append(f"**NSFW:** {before_nsfw} → {after_nsfw}")

        before_slowmode = getattr(before, "slowmode_delay", None)
        after_slowmode = getattr(after, "slowmode_delay", None)
        if before_slowmode != after_slowmode:
            changes.append(f"**Slowmode:** {before_slowmode} → {after_slowmode}")

        if getattr(before, "category_id", None) != getattr(after, "category_id", None):
            changes.append("**Категория изменена**")

        if not changes:
            return

        embed = self._embed(after.guild, "🛠️ Канал изменён", COLOR_MUTED)
        self._add(embed, "Канал", f"{after.mention} (`{after.id}`)", inline=False)
        embed.add_field(name="Изменения", value=truncate("\n".join(changes), 1024), inline=False)
        await self._send(after.guild, "channels", embed)

    # ------------------------------------------------------------------ roles

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        embed = self._embed(role.guild, "🎭 Роль создана", COLOR_SUCCESS)
        self._add(embed, "Роль", f"{role.mention} (`{role.id}`)")
        self._add(embed, "Цвет", str(role.color))
        self._add(embed, "Участников", str(len(role.members)))
        await self._send(role.guild, "roles", embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        embed = self._embed(role.guild, "🎭 Роль удалена", COLOR_DANGER)
        self._add(embed, "Роль", f"{role.name} (`{role.id}`)")
        self._add(embed, "Цвет", str(role.color))
        self._add(embed, "Участников", str(len(role.members)))
        await self._send(role.guild, "roles", embed)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        changes: list[str] = []
        if before.name != after.name:
            changes.append(f"**Название:** `{before.name}` → `{after.name}`")
        if before.color != after.color:
            changes.append(f"**Цвет:** {before.color} → {after.color}")
        if before.hoist != after.hoist:
            changes.append(f"**Отображать отдельно:** {before.hoist} → {after.hoist}")
        if before.mentionable != after.mentionable:
            changes.append(f"**Упоминание:** {before.mentionable} → {after.mentionable}")

        before_perms = dict(before.permissions)
        after_perms = dict(after.permissions)
        permission_changes = [
            f"{'✅' if after_perms[name] else '❌'} {name}"
            for name in after_perms
            if before_perms.get(name) != after_perms[name]
        ][:15]
        if permission_changes:
            changes.append("**Права:**\n" + "\n".join(permission_changes))

        if not changes:
            return

        embed = self._embed(after.guild, "🎭 Роль изменена", COLOR_MUTED)
        self._add(embed, "Роль", f"{after.mention} (`{after.id}`)", inline=False)
        embed.add_field(name="Изменения", value=truncate("\n".join(changes), 1024), inline=False)
        await self._send(after.guild, "roles", embed)

    # ------------------------------------------------------------------ voice

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return

        if before.channel is None and after.channel is not None:
            embed = self._embed(member.guild, "🔊 Подключился к голосовому каналу", COLOR_SUCCESS)
            self._add(embed, "Участник", f"{member} (`{member.id}`)")
            self._add(embed, "Канал", after.channel.mention)
        elif before.channel is not None and after.channel is None:
            embed = self._embed(member.guild, "🔇 Покинул голосовой канал", COLOR_MUTED)
            self._add(embed, "Участник", f"{member} (`{member.id}`)")
            self._add(embed, "Канал", before.channel.mention)
        elif before.channel is not None and after.channel is not None and before.channel != after.channel:
            embed = self._embed(member.guild, "🔀 Перешёл в другой голосовой канал", COLOR_MUTED)
            self._add(embed, "Участник", f"{member} (`{member.id}`)")
            self._add(embed, "Откуда", before.channel.mention)
            self._add(embed, "Куда", after.channel.mention)
        else:
            return

        await self._send(member.guild, "voice", embed)

    # ---------------------------------------------------------------- invites

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        guild = invite.guild
        if not isinstance(guild, discord.Guild):
            return
        embed = self._embed(guild, "🔗 Приглашение создано", COLOR_SUCCESS)
        self._add(embed, "Код", f"`{invite.code}`")
        self._add(
            embed,
            "Автор",
            f"{invite.inviter} (`{invite.inviter.id}`)" if invite.inviter else "неизвестен",
        )
        self._add(embed, "Канал", getattr(invite.channel, "mention", "—"))
        self._add(embed, "Макс. использований", str(invite.max_uses or "∞"))
        await self._send(guild, "invites", embed)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        guild = invite.guild
        if not isinstance(guild, discord.Guild):
            return
        embed = self._embed(guild, "🔗 Приглашение удалено", COLOR_MUTED)
        self._add(embed, "Код", f"`{invite.code}`")
        self._add(embed, "Канал", getattr(invite.channel, "mention", "—"))
        await self._send(guild, "invites", embed)

    # ------------------------------------------------------------------ guild

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        changes: list[str] = []
        if before.name != after.name:
            changes.append(f"**Название:** `{before.name}` → `{after.name}`")
        if before.icon != after.icon:
            changes.append("**Иконка сервера изменена**")
        if before.owner_id != after.owner_id:
            changes.append("**Владелец сервера изменён**")
        if not changes:
            return

        embed = self._embed(after, "🏠 Сервер обновлён", COLOR_MUTED)
        embed.add_field(name="Изменения", value=truncate("\n".join(changes), 1024), inline=False)
        await self._send(after, "moderation", embed)

    # --------------------------------------------------------------- commands

    logs_group = app_commands.Group(
        name="logs",
        description="Настройка логирования сервера",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @logs_group.command(name="channel", description="Канал для логов сервера (без параметра — выключить)")
    @app_commands.describe(channel="Канал, куда бот будет писать события")
    async def logs_channel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None

        if channel is None:
            await self.bot.settings.set(guild.id, "log_channel", None)
            await interaction.response.send_message(
                embed=base_embed(title="✅ Логи выключены", color=COLOR_SUCCESS),
                ephemeral=True,
            )
            return

        permissions = channel.permissions_for(guild.me)
        if not permissions.send_messages or not permissions.embed_links:
            raise UserError(f"У бота нет прав писать embed-сообщения в {channel.mention}.")

        await self.bot.settings.set(guild.id, "log_channel", channel.id)
        await interaction.response.send_message(
            embed=base_embed(
                title="✅ Канал логов установлен",
                description=f"Все события будут отправляться в {channel.mention}.",
                color=COLOR_SUCCESS,
            ),
            ephemeral=True,
        )

    @logs_group.command(name="modlog", description="Отдельный канал для модерации (предупреждения, баны)")
    @app_commands.describe(channel="Канал модерации; без параметра — использовать общий канал логов")
    async def logs_modlog(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None

        if channel is None:
            await self.bot.settings.set(guild.id, "modlog_channel", None)
            await interaction.response.send_message(
                embed=base_embed(
                    title="✅ Канал модерации сброшен",
                    description="Модерация будет логироваться в общий канал логов.",
                    color=COLOR_SUCCESS,
                ),
                ephemeral=True,
            )
            return

        permissions = channel.permissions_for(guild.me)
        if not permissions.send_messages or not permissions.embed_links:
            raise UserError(f"У бота нет прав писать embed-сообщения в {channel.mention}.")

        await self.bot.settings.set(guild.id, "modlog_channel", channel.id)
        await interaction.response.send_message(
            embed=base_embed(
                title="✅ Канал модерации установлен",
                description=f"Предупреждения и наказания будут отправляться в {channel.mention}.",
                color=COLOR_SUCCESS,
            ),
            ephemeral=True,
        )

    @logs_group.command(name="toggle", description="Включить или выключить группу событий")
    @app_commands.choices(
        group=[
            app_commands.Choice(name="Сообщения", value="messages"),
            app_commands.Choice(name="Участники", value="members"),
            app_commands.Choice(name="Роли", value="roles"),
            app_commands.Choice(name="Каналы", value="channels"),
            app_commands.Choice(name="Голосовые каналы", value="voice"),
            app_commands.Choice(name="Приглашения", value="invites"),
            app_commands.Choice(name="Модерация", value="moderation"),
        ]
    )
    async def logs_toggle(
        self,
        interaction: discord.Interaction,
        group: str,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        if group not in LOG_GROUPS:
            raise UserError("Неизвестная группа событий.")
        key = f"log_{group}"
        current = await self.bot.settings.get_bool(guild.id, key, True)
        await self.bot.settings.set(guild.id, key, not current)
        state = "выключены" if current else "включены"
        await interaction.response.send_message(
            embed=base_embed(
                title="✅ Настройка сохранена",
                description=f"Логи группы «{LOG_GROUPS[group]}» теперь {state}.",
                color=COLOR_SUCCESS,
            ),
            ephemeral=True,
        )

    @logs_group.command(name="status", description="Показать текущие настройки логов")
    async def logs_status(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        settings = self.bot.settings

        log_channel_id = await settings.get(guild.id, "log_channel")
        modlog_channel_id = await settings.get(guild.id, "modlog_channel")

        def channel_text(channel_id: int | None) -> str:
            if not channel_id:
                return "не задан"
            channel = guild.get_channel(int(channel_id))
            return channel.mention if channel else "канал удалён"

        embed = base_embed(title="📋 Логи сервера", color=COLOR_MUTED)
        embed.add_field(name="Общий канал", value=channel_text(log_channel_id), inline=True)
        embed.add_field(name="Канал модерации", value=channel_text(modlog_channel_id), inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        for key, label in LOG_GROUPS.items():
            enabled = await settings.get_bool(guild.id, f"log_{key}", True)
            embed.add_field(name=label, value="✅ включено" if enabled else "❌ выключено", inline=True)

        embed.set_footer(text="Изменить: /logs channel, /logs modlog, /logs toggle")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Logs(bot))

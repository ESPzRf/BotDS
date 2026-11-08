"""Небольшие помощники, которым нужен discord.py."""

from __future__ import annotations

import logging

import discord

from core.utils import truncate

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Каналы и сообщения
# ---------------------------------------------------------------------------


async def resolve_channel(
    guild: discord.Guild,
    channel_id: int | str | None,
    *,
    channel_types: tuple[type[discord.abc.GuildChannel], ...] = (discord.TextChannel,),
) -> discord.abc.GuildChannel | None:
    if not channel_id:
        return None
    try:
        identifier = int(channel_id)
    except (TypeError, ValueError):
        return None

    channel = guild.get_channel(identifier)
    if channel is None:
        try:
            channel = await guild.fetch_channel(identifier)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
    return channel if isinstance(channel, channel_types) else None


async def resolve_log_channel(bot, guild: discord.Guild) -> discord.TextChannel | None:
    channel_id = await bot.settings.get(guild.id, "log_channel")
    channel = await resolve_channel(guild, channel_id)
    return channel if isinstance(channel, discord.TextChannel) else None


async def resolve_modlog_channel(bot, guild: discord.Guild) -> discord.TextChannel | None:
    channel_id = await bot.settings.get(guild.id, "modlog_channel")
    channel = await resolve_channel(guild, channel_id)
    if isinstance(channel, discord.TextChannel):
        return channel
    return await resolve_log_channel(bot, guild)


async def safe_send(channel: discord.abc.Messageable, embed: discord.Embed) -> bool:
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return True
    except (discord.Forbidden, discord.HTTPException):
        log.debug("Не удалось отправить сообщение в %s", getattr(channel, "id", "?"), exc_info=True)
        return False


async def send_temp(
    channel: discord.abc.Messageable,
    content: str,
    *,
    seconds: float = 8.0,
    mention_user: discord.abc.User | None = None,
) -> None:
    """Короткое уведомление, которое само исчезает."""

    allowed = discord.AllowedMentions(users=True) if mention_user else discord.AllowedMentions.none()
    try:
        await channel.send(
            content=truncate(content, 2000),
            allowed_mentions=allowed,
            delete_after=seconds,
        )
    except (discord.Forbidden, discord.HTTPException):
        pass


async def safe_dm(user: discord.abc.User, embed: discord.Embed) -> bool:
    try:
        await user.send(embed=embed)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


# ---------------------------------------------------------------------------
# Аудит-лог
# ---------------------------------------------------------------------------


async def find_audit_entry(
    guild: discord.Guild,
    action: discord.AuditLogAction,
    target_id: int,
    *,
    within_seconds: int = 20,
    limit: int = 5,
) -> discord.AuditLogEntry | None:
    """Ищет свежую запись аудита по цели (чтобы понять, кто забанил/кикнул)."""

    try:
        async for entry in guild.audit_logs(limit=limit, action=action):
            target = getattr(entry.target, "id", None)
            if target != target_id:
                continue
            age = (discord.utils.utcnow() - entry.created_at).total_seconds()
            if age <= within_seconds:
                return entry
    except (discord.Forbidden, discord.HTTPException):
        log.debug("Нет доступа к аудит-логу сервера %s", guild.id, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# Роли
# ---------------------------------------------------------------------------


_DANGEROUS_ROLE_PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("administrator", "администратор"),
    ("manage_guild", "управление сервером"),
    ("manage_roles", "управление ролями"),
    ("manage_channels", "управление каналами"),
    ("kick_members", "кик участников"),
    ("ban_members", "бан участников"),
    ("moderate_members", "тайм-аут участников"),
    ("manage_webhooks", "управление вебхуками"),
    ("mention_everyone", "упоминание @everyone"),
)


def dangerous_role_permissions(role: discord.Role) -> str | None:
    """Возвращает описание опасных прав роли, если их нельзя выдавать массово."""

    permissions = role.permissions
    found = [label for attr, label in _DANGEROUS_ROLE_PERMISSIONS if getattr(permissions, attr, False)]
    if not found:
        return None
    return ", ".join(found)


def role_assign_problem(guild: discord.Guild, role: discord.Role) -> str | None:
    """Возвращает причину, по которой бот не сможет безопасно выдать роль, или None."""

    if role.is_default():
        return "роль @everyone нельзя выдавать"
    if role.managed:
        return "роль управляется интеграцией и не может быть назначена вручную"

    dangerous = dangerous_role_permissions(role)
    if dangerous:
        return f"у роли опасные права: {dangerous}"

    me = guild.me
    if me is None:
        return "бот не найден на сервере"
    if not me.guild_permissions.manage_roles:
        return "у бота нет права «Управлять ролями»"
    if role >= me.top_role:
        return "роль находится выше роли бота"

    return None


# ---------------------------------------------------------------------------
# Подтверждение действий
# ---------------------------------------------------------------------------


class ConfirmView(discord.ui.View):
    """Кнопки «Подтвердить / Отмена» с проверкой автора."""

    def __init__(self, author_id: int, *, timeout: float = 60.0, confirm_label: str = "Подтвердить") -> None:
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value: bool | None = None

        confirm = discord.ui.Button(label=confirm_label, style=discord.ButtonStyle.danger)
        confirm.callback = self._on_confirm  # type: ignore[method-assign]
        self.add_item(confirm)

        cancel = discord.ui.Button(label="Отмена", style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel  # type: ignore[method-assign]
        self.add_item(cancel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Эта кнопка не для вас.",
                ephemeral=True,
            )
            return False
        return True

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        self.value = True
        await interaction.response.defer()
        self.stop()

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self.value = False
        await interaction.response.defer()
        self.stop()

    async def on_timeout(self) -> None:
        self.value = None

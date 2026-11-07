"""Проверки прав и безопасное применение наказаний."""

from __future__ import annotations

from datetime import timedelta

import discord

from core.errors import UserError
from core.utils import clamp

MAX_TIMEOUT_SECONDS = 28 * 24 * 60 * 60


async def ensure_can_moderate(interaction: discord.Interaction, target: discord.Member) -> None:
    """Проверяет, что модератор вправе применить действие к участнику."""

    guild = interaction.guild
    actor = interaction.user
    if guild is None or not isinstance(actor, discord.Member):
        raise UserError("Команда доступна только на сервере.")

    if target.id == actor.id:
        raise UserError("Нельзя применить действие к самому себе.")
    if target.id == guild.owner_id:
        raise UserError("Нельзя применить действие к владельцу сервера.")
    if target.bot:
        raise UserError("Нельзя модерировать бота.")

    if actor.id != guild.owner_id:
        if target.guild_permissions.administrator:
            raise UserError("Нельзя модерировать администратора сервера.")
        if actor.top_role <= target.top_role:
            raise UserError("Ваша роль должна быть выше роли участника.")

    me = guild.me
    if me is not None and target.top_role >= me.top_role:
        raise UserError("Роль участника выше или равна роли бота — действие невозможно.")


def ensure_bot_hierarchy(guild: discord.Guild, target: discord.Member | discord.User) -> None:
    me = guild.me
    if me is None:
        raise UserError("Бот не найден на сервере.")
    top_role = getattr(target, "top_role", None)
    if top_role is not None and top_role >= me.top_role:
        raise UserError("Роль участника выше или равна роли бота — действие невозможно.")


async def apply_timeout(member: discord.Member, seconds: int, reason: str) -> int:
    guild = member.guild
    me = guild.me
    if me is None or not me.guild_permissions.moderate_members:
        raise UserError("У бота нет права «Тайм-аут участников».")

    seconds = clamp(int(seconds), 60, MAX_TIMEOUT_SECONDS)
    until = discord.utils.utcnow() + timedelta(seconds=seconds)
    try:
        await member.timeout(until, reason=reason)
    except discord.Forbidden as exc:
        raise UserError("Не удалось выдать тайм-аут: недостаточно прав у бота.") from exc
    except discord.HTTPException as exc:
        raise UserError(f"Discord отклонил тайм-аут: {exc}") from exc
    return seconds


async def kick_member(member: discord.Member, reason: str) -> None:
    guild = member.guild
    me = guild.me
    if me is None or not me.guild_permissions.kick_members:
        raise UserError("У бота нет права «Кикать участников».")
    try:
        await guild.kick(member, reason=reason)
    except discord.Forbidden as exc:
        raise UserError("Не удалось кикнуть: недостаточно прав у бота.") from exc
    except discord.HTTPException as exc:
        raise UserError(f"Discord отклонил кик: {exc}") from exc


async def ban_member(user: discord.Member | discord.User, reason: str, guild: discord.Guild) -> None:
    me = guild.me
    if me is None or not me.guild_permissions.ban_members:
        raise UserError("У бота нет права «Баннить участников».")
    try:
        await guild.ban(user, reason=reason)
    except discord.Forbidden as exc:
        raise UserError("Не удалось забанить: недостаточно прав у бота.") from exc
    except discord.HTTPException as exc:
        raise UserError(f"Discord отклонил бан: {exc}") from exc

"""Единый стиль embed-сообщений."""

from __future__ import annotations

import discord

from core.leveling import level_title, loyalty_tier, progress
from core.repositories import MemberStats
from core.utils import format_number, truncate

COLOR_PRIMARY = 0x5865F2
COLOR_SUCCESS = 0x57F287
COLOR_DANGER = 0xED4245
COLOR_WARNING = 0xFEE75C
COLOR_INFO = 0x5865F2
COLOR_MUTED = 0x2B2D31
COLOR_LOG = 0x2B2D31

FOOTER_SEPARATOR = " • "


def base_embed(
    *,
    title: str | None = None,
    description: str | None = None,
    color: int = COLOR_PRIMARY,
) -> discord.Embed:
    embed = discord.Embed(
        title=truncate(title, 256) if title else None,
        description=truncate(description, 4096) if description else None,
        color=color,
    )
    embed.timestamp = discord.utils.utcnow()
    return embed


def success_embed(description: str, *, title: str | None = None) -> discord.Embed:
    return base_embed(title=title or "✅ Готово", description=description, color=COLOR_SUCCESS)


def error_embed(description: str, *, title: str | None = None) -> discord.Embed:
    return base_embed(title=title or "❌ Ошибка", description=description, color=COLOR_DANGER)


def warning_embed(description: str, *, title: str | None = None) -> discord.Embed:
    return base_embed(title=title or "⚠️ Внимание", description=description, color=COLOR_WARNING)


def info_embed(description: str, *, title: str | None = None) -> discord.Embed:
    return base_embed(title=title, description=description, color=COLOR_INFO)


def user_tag(user: discord.abc.User) -> str:
    """'Имя (@username, 1234567890)' — без пинга."""

    return f"{user} (`{user.id}`)"


def set_guild_footer(embed: discord.Embed, guild: discord.Guild) -> discord.Embed:
    embed.set_footer(text=truncate(f"{guild.name}{FOOTER_SEPARATOR}{guild.id}", 2048))
    return embed


def progress_bar(ratio: float, length: int = 10, filled: str = "▰", empty: str = "▱") -> str:
    ratio = min(1.0, max(0.0, ratio))
    filled_count = int(round(ratio * length))
    if filled_count == 0 and ratio > 0:
        filled_count = 1
    return filled * filled_count + empty * (length - filled_count)


def rank_embed(
    member: discord.Member,
    stats: MemberStats,
    *,
    position: int | None,
    total_members: int,
) -> discord.Embed:
    prog = progress(stats.xp)
    tier = loyalty_tier(stats.loyalty)

    embed = base_embed(
        title=f"Профиль — {member.display_name}",
        color=COLOR_PRIMARY,
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    embed.add_field(
        name="Уровень",
        value=f"**{prog.level}** · {level_title(prog.level)}",
        inline=True,
    )
    embed.add_field(
        name="Место",
        value=f"#{position} из {format_number(total_members)}" if position else "—",
        inline=True,
    )
    embed.add_field(
        name="Лояльность",
        value=f"{tier.emoji} **{format_number(stats.loyalty)}** · {tier.name}",
        inline=True,
    )

    xp_line = f"{progress_bar(prog.ratio)} `{prog.xp_into_level}/{prog.xp_needed}`"
    xp_line += f"\nДо **{prog.level + 1}** уровня: **{format_number(prog.xp_to_next)}** XP"
    embed.add_field(name="Опыт", value=xp_line, inline=False)

    activity = f"Сообщений: **{format_number(stats.messages)}**"
    if stats.daily_streak > 1:
        activity += f" · Серия /daily: **{stats.daily_streak}**"
    embed.add_field(name="Активность", value=activity, inline=False)

    if tier.next_threshold is not None and tier.next_name is not None:
        left = max(0, tier.next_threshold - stats.loyalty)
        embed.set_footer(
            text=f"Всего опыта: {format_number(stats.xp)} · До уровня «{tier.next_name}»: {format_number(left)} лояльности"
        )
    else:
        embed.set_footer(text=f"Всего опыта: {format_number(stats.xp)} · Максимальный статус лояльности")

    return embed


def level_up_embed(member: discord.Member, level: int, rewards: list[discord.Role]) -> discord.Embed:
    embed = base_embed(
        title="🎉 Новый уровень!",
        description=f"{member.mention} достиг **{level}** уровня — {level_title(level)}.",
        color=COLOR_SUCCESS,
    )
    if rewards:
        embed.add_field(
            name="Награда",
            value=", ".join(role.mention for role in rewards[:10]),
            inline=False,
        )
    embed.set_thumbnail(url=member.display_avatar.url)
    return embed


def warning_dm_embed(
    *,
    guild_name: str,
    reason: str,
    count: int,
    action_note: str | None = None,
    source: str = "manual",
) -> discord.Embed:
    embed = base_embed(
        title="⚠️ Вам выдано предупреждение",
        description=f"Сервер: **{guild_name}**",
        color=COLOR_WARNING,
    )
    embed.add_field(name="Причина", value=truncate(reason, 1024), inline=False)
    embed.add_field(name="Всего предупреждений", value=str(count), inline=True)
    embed.add_field(
        name="Источник",
        value="Автомодерация" if source == "automod" else "Модератор",
        inline=True,
    )
    if action_note:
        embed.add_field(name="Действие", value=truncate(action_note, 1024), inline=False)
    return embed

"""Единая логика предупреждений: запись в БД, эскалация, DM и лог."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import discord

from core.discord_utils import resolve_modlog_channel, safe_dm
from core.embeds import COLOR_WARNING, base_embed, warning_dm_embed
from core.moderation import apply_timeout, ban_member, kick_member
from core.repositories import Warning
from core.timeparse import format_duration
from core.utils import truncate

if TYPE_CHECKING:  # pragma: no cover
    from bot import ModerationBot

log = logging.getLogger(__name__)


@dataclass(slots=True)
class WarnResult:
    warning: Warning
    count: int
    rule: dict[str, Any] | None
    action_note: str | None


class WarningService:
    """Сервис живёт на боте и используется и командами, и автомодом."""

    def __init__(self, bot: "ModerationBot") -> None:
        self.bot = bot

    async def add(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
        moderator: discord.User | discord.Member | None,
        reason: str,
        source: str = "manual",
        *,
        log_event: bool = True,
    ) -> WarnResult:
        reason = truncate(reason.strip() or "Без причины", 900)
        moderator_id = moderator.id if moderator is not None else None

        warning_id = await self.bot.repos.warnings.add(
            guild.id,
            user.id,
            moderator_id,
            reason,
            source,
        )
        warning = await self.bot.repos.warnings.get(warning_id)
        if warning is None:  # pragma: no cover - защита от гонок в БД
            warning = Warning(
                id=warning_id,
                guild_id=guild.id,
                user_id=user.id,
                moderator_id=moderator_id,
                reason=reason,
                source=source,
                created_at=discord.utils.utcnow(),
                active=True,
            )

        count = await self.bot.repos.warnings.count_active(guild.id, user.id)
        rules = await self.bot.settings.get(guild.id, "warn_escalations", [])
        rule = self._find_rule(rules, count)

        planned = self._describe_rule(rule) if rule else None

        if await self.bot.settings.get_bool(guild.id, "warnings_dm", True):
            embed = warning_dm_embed(
                guild_name=guild.name,
                reason=reason,
                count=count,
                action_note=f"Достигнут порог: {planned}" if planned else None,
                source=source,
            )
            await safe_dm(user, embed)

        action_note = await self._apply_rule(guild, user, rule, count) if rule else None
        if log_event:
            await self._log(guild, user, moderator, reason, source, count, action_note)

        return WarnResult(warning=warning, count=count, rule=rule, action_note=action_note)

    # ------------------------------------------------------------------ rules

    @staticmethod
    def _find_rule(rules: Any, count: int) -> dict[str, Any] | None:
        if not isinstance(rules, list):
            return None
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            try:
                if int(rule.get("count", -1)) == count:
                    return rule
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _describe_rule(rule: dict[str, Any]) -> str:
        action = str(rule.get("action", "")).lower()
        if action == "timeout":
            try:
                seconds = int(rule.get("duration") or 3600)
            except (TypeError, ValueError):
                seconds = 3600
            return f"тайм-аут на {format_duration(seconds)}"
        if action == "kick":
            return "кик"
        if action == "ban":
            return "бан"
        return action or "неизвестное действие"

    async def _apply_rule(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
        rule: dict[str, Any],
        count: int,
    ) -> str:
        action = str(rule.get("action", "")).lower()
        reason = f"Автоматическое наказание: {count} предупреждений"

        if user.id == guild.owner_id:
            return "не применено: владелец сервера"
        if not isinstance(user, discord.Member):
            return "не применено: участник уже покинул сервер"

        me = guild.me
        if me is None:
            return "не применено: бот не найден на сервере"
        if user.top_role >= me.top_role:
            return "не применено: роль участника выше роли бота"

        try:
            if action == "timeout":
                try:
                    seconds = int(rule.get("duration") or 3600)
                except (TypeError, ValueError):
                    seconds = 3600
                applied = await apply_timeout(user, seconds, reason)
                return f"выдан тайм-аут на {format_duration(applied)}"

            if action == "kick":
                await kick_member(user, reason)
                return "участник кикнут"

            if action == "ban":
                await ban_member(user, reason, guild)
                return "участник забанен"
        except Exception as exc:  # noqa: BLE001 - причину показываем в логе
            log.warning("Не удалось применить автонаказание на %s: %s", guild.id, exc)
            return f"не применено: {exc}"

        return "не применено: неизвестное действие"

    # -------------------------------------------------------------------- log

    async def _log(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
        moderator: discord.User | discord.Member | None,
        reason: str,
        source: str,
        count: int,
        action_note: str | None,
    ) -> None:
        if not await self.bot.settings.get_bool(guild.id, "log_moderation", True):
            return

        channel = await resolve_modlog_channel(self.bot, guild)
        if channel is None:
            return

        embed = base_embed(
            title="⚠️ Предупреждение",
            color=COLOR_WARNING,
        )
        embed.add_field(name="Участник", value=f"{user} (`{user.id}`)", inline=True)
        embed.add_field(
            name="Модератор",
            value=f"{moderator} (`{moderator.id}`)" if moderator else "Автомодерация",
            inline=True,
        )
        embed.add_field(name="Всего", value=str(count), inline=True)
        embed.add_field(name="Причина", value=truncate(reason, 1024), inline=False)
        embed.add_field(
            name="Источник",
            value="Автомодерация" if source == "automod" else "Вручную",
            inline=True,
        )
        if action_note:
            embed.add_field(name="Автодействие", value=truncate(action_note, 1024), inline=False)

        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            log.debug("Не удалось отправить лог предупреждения", exc_info=True)

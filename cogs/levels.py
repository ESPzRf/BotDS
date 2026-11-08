"""Уровни, опыт и система лояльности."""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.discord_utils import resolve_modlog_channel, role_assign_problem
from core.embeds import (
    COLOR_MUTED,
    COLOR_PRIMARY,
    COLOR_WARNING,
    base_embed,
    level_up_embed,
    rank_embed,
    success_embed,
)
from core.errors import UserError
from core.leveling import (
    best_role_multiplier,
    combined_multiplier,
    event_active,
    loyalty_reward,
    loyalty_tier,
    progress,
    scaled_amount,
)
from core.repositories import MemberStats, to_iso
from core.settings import DEFAULTS
from core.timeparse import format_duration, parse_duration
from core.utils import format_number, truncate

log = logging.getLogger(__name__)

METRIC_CHOICES = [
    app_commands.Choice(name="Опыт", value="xp"),
    app_commands.Choice(name="Лояльность", value="loyalty"),
    app_commands.Choice(name="Сообщения", value="messages"),
]

DAILY_COOLDOWN_SECONDS = 20 * 60 * 60
DAILY_STREAK_WINDOW_SECONDS = 48 * 60 * 60


def _as_float(value: Any, default: float = 1.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class Levels(commands.Cog):
    levelconfig = app_commands.Group(
        name="levelconfig",
        description="Настройка уровней и лояльности",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ---------------------------------------------------------------- events

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return
        if not message.content:
            return

        guild = message.guild
        settings = self.bot.settings

        if not await settings.get_bool(guild.id, "levels_enabled", True):
            return

        ignored = await settings.get_list(guild.id, "levels_ignored_channels")
        if message.channel.id in {int(channel_id) for channel_id in ignored}:
            return

        stats = await self.bot.repos.members.ensure(guild.id, message.author.id)
        now = discord.utils.utcnow()
        cooldown = await settings.get_int(guild.id, "levels_cooldown", 60)
        if stats.last_xp_at is not None:
            elapsed = (now - stats.last_xp_at).total_seconds()
            if elapsed < max(cooldown, 5):
                return

        xp_min = await settings.get_int(guild.id, "levels_xp_min", 15)
        xp_max = await settings.get_int(guild.id, "levels_xp_max", 25)
        if xp_min > xp_max:
            xp_min, xp_max = xp_max, xp_min
        base_xp = random.randint(max(1, xp_min), max(1, xp_max))
        base_loyalty = max(0, await settings.get_int(guild.id, "loyalty_per_message", 1))

        xp_multiplier, loyalty_multiplier = await self._multipliers(guild, message.author)
        gain = scaled_amount(base_xp, xp_multiplier)
        loyalty_gain = scaled_amount(base_loyalty, loyalty_multiplier)

        old_level = progress(stats.xp).level
        new_stats = await self.bot.repos.members.add_progress(
            guild.id,
            message.author.id,
            xp=gain,
            loyalty=loyalty_gain,
            messages=1,
            last_xp_at=now,
        )
        new_level = progress(new_stats.xp).level
        if new_level > new_stats.level:
            await self.bot.repos.members.raise_level(guild.id, message.author.id, new_level)

        if new_level > old_level:
            await self._on_level_up(
                guild,
                message.author,
                old_level,
                new_level,
                channel=message.channel,
            )

    async def _multipliers(
        self,
        guild: discord.Guild,
        member: discord.Member,
    ) -> tuple[float, float]:
        """Возвращает (множитель XP, множитель лояльности) для участника."""

        settings = self.bot.settings
        gid = guild.id

        xp_base = await settings.get(gid, "levels_xp_multiplier", 1.0)
        loyalty_base = await settings.get(gid, "levels_loyalty_multiplier", 1.0)
        booster_base = await settings.get(gid, "levels_booster_multiplier", 1.0)
        role_boosts = await settings.get(gid, "levels_role_boosts", {})

        event_multiplier = 1.0
        event_until_raw = await settings.get(gid, "levels_event_until")
        if event_until_raw:
            try:
                event_until = datetime.fromisoformat(str(event_until_raw))
            except ValueError:
                event_until = None
            if event_until is not None and event_until.tzinfo is None:
                event_until = event_until.replace(tzinfo=timezone.utc)
            if event_until is not None and event_active(event_until, discord.utils.utcnow()):
                event_multiplier = _as_float(
                    await settings.get(gid, "levels_event_multiplier", 1.0)
                )

        role_multiplier = best_role_multiplier(
            (role.id for role in member.roles),
            role_boosts if isinstance(role_boosts, dict) else {},
        )
        booster_multiplier = _as_float(booster_base) if member.premium_since else 1.0

        xp_multiplier = combined_multiplier(
            _as_float(xp_base),
            event_multiplier,
            role_multiplier,
            booster_multiplier,
        )
        loyalty_multiplier = combined_multiplier(
            _as_float(loyalty_base),
            event_multiplier,
            role_multiplier,
            booster_multiplier,
        )
        return xp_multiplier, loyalty_multiplier

    async def _on_level_up(
        self,
        guild: discord.Guild,
        member: discord.Member,
        old_level: int,
        new_level: int,
        *,
        channel: Any | None = None,
    ) -> None:
        settings = self.bot.settings

        rewards = await self.bot.repos.level_rewards.list(guild.id)
        granted: list[discord.Role] = []
        for level, role_id in rewards.items():
            if not old_level < level <= new_level:
                continue
            role = guild.get_role(role_id)
            if role is None or role in member.roles:
                continue
            problem = role_assign_problem(guild, role)
            if problem:
                log.warning("Не удалось выдать награду за уровень %s: %s", level, problem)
                continue
            try:
                await member.add_roles(role, reason=f"Награда за {level} уровень")
            except (discord.Forbidden, discord.HTTPException):
                log.warning("Discord не дал выдать роль за уровень", exc_info=True)
                continue
            granted.append(role)

        if not await settings.get_bool(guild.id, "levels_announce", True):
            return

        channel_id = await settings.get(guild.id, "levels_channel")
        if channel_id:
            resolved = guild.get_channel(int(channel_id))
            if isinstance(resolved, discord.TextChannel):
                channel = resolved
        if channel is None:
            return

        template = str(
            await settings.get(
                guild.id,
                "levels_message",
                DEFAULTS["levels_message"],
            )
        )
        try:
            text = template.format(
                user=member.mention,
                name=member.display_name,
                level=new_level,
            )
        except (KeyError, IndexError, ValueError):
            text = f"🎉 {member.display_name}, достигнут **{new_level}** уровень!"

        try:
            await channel.send(
                content=truncate(text, 2000),
                embed=level_up_embed(member, new_level, granted),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            log.debug("Не удалось отправить сообщение о новом уровне", exc_info=True)

    # ------------------------------------------------------------ admin boost

    async def _change_stats(
        self,
        member: discord.Member,
        *,
        xp_delta: int = 0,
        xp_set: int | None = None,
        loyalty_delta: int = 0,
        loyalty_set: int | None = None,
        channel: Any | None = None,
    ) -> tuple[MemberStats, int, int, int]:
        """Меняет XP/лояльность вручную, пересчитывает уровень и награды."""

        guild = member.guild
        current = await self.bot.repos.members.ensure(guild.id, member.id)
        old_xp = current.xp
        old_loyalty = current.loyalty
        old_level = progress(old_xp).level

        if xp_set is not None:
            await self.bot.repos.members.set_values(guild.id, member.id, xp=max(0, int(xp_set)))
        elif xp_delta:
            if xp_delta > 0:
                await self.bot.repos.members.add_progress(guild.id, member.id, xp=int(xp_delta))
            else:
                await self.bot.repos.members.set_values(
                    guild.id, member.id, xp=max(0, old_xp + int(xp_delta))
                )

        if loyalty_set is not None:
            await self.bot.repos.members.set_values(
                guild.id, member.id, loyalty=max(0, int(loyalty_set))
            )
        elif loyalty_delta:
            if loyalty_delta > 0:
                await self.bot.repos.members.add_progress(
                    guild.id, member.id, loyalty=int(loyalty_delta)
                )
            else:
                await self.bot.repos.members.set_values(
                    guild.id, member.id, loyalty=max(0, old_loyalty + int(loyalty_delta))
                )

        stats = await self.bot.repos.members.ensure(guild.id, member.id)
        new_level = progress(stats.xp).level
        if new_level > stats.level:
            await self.bot.repos.members.raise_level(guild.id, member.id, new_level)
            stats.level = new_level

        if new_level > old_level:
            await self._on_level_up(guild, member, old_level, new_level, channel=channel)

        return stats, old_xp, old_loyalty, old_level

    async def _run_admin_change(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: int,
        reason: str | None,
        *,
        field: str,
        mode: str,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        if member.bot:
            raise UserError("Ботам нельзя начислять опыт и лояльность.")
        if mode in {"add", "remove"} and not 1 <= amount <= 1_000_000_000:
            raise UserError("Количество должно быть от 1 до 1 000 000 000.")
        if mode == "set" and not 0 <= amount <= 1_000_000_000:
            raise UserError("Значение должно быть от 0 до 1 000 000 000.")

        kwargs: dict[str, Any] = {}
        if field == "xp":
            if mode == "set":
                kwargs["xp_set"] = amount
            elif mode == "add":
                kwargs["xp_delta"] = amount
            else:
                kwargs["xp_delta"] = -amount
        else:
            if mode == "set":
                kwargs["loyalty_set"] = amount
            elif mode == "add":
                kwargs["loyalty_delta"] = amount
            else:
                kwargs["loyalty_delta"] = -amount

        await interaction.response.defer(ephemeral=True, thinking=True)
        stats, old_xp, old_loyalty, old_level = await self._change_stats(
            member,
            channel=interaction.channel,
            **kwargs,
        )
        new_level = stats.level

        field_name = "XP" if field == "xp" else "лояльности"
        actions = {"add": "начислено", "remove": "снято", "set": "установлено"}
        if field == "xp":
            old_value, new_value = old_xp, stats.xp
        else:
            old_value, new_value = old_loyalty, stats.loyalty
        applied = amount if mode == "set" else abs(new_value - old_value)

        embed = success_embed(
            f"{member.mention}: {actions[mode]} **{format_number(applied)}** {field_name}.\n"
            f"Было: **{format_number(old_value)}** → стало: **{format_number(new_value)}**."
        )
        embed.add_field(name="Уровень", value=f"{old_level} → **{new_level}**", inline=True)
        if reason:
            embed.add_field(name="Причина", value=truncate(reason, 500), inline=False)
        if mode in {"remove", "set"}:
            embed.set_footer(text="Роли, выданные за прошлые уровни, не снимаются автоматически.")
        await interaction.followup.send(embed=embed, ephemeral=True)

        await self._log_admin(
            guild,
            moderator=interaction.user,
            member=member,
            field=field_name,
            mode=mode,
            amount=applied,
            old_value=old_value,
            new_value=new_value,
            old_level=old_level,
            new_level=new_level,
            reason=reason,
        )

    async def _log_admin(
        self,
        guild: discord.Guild,
        *,
        moderator: discord.abc.User,
        member: discord.Member,
        field: str,
        mode: str,
        amount: int,
        old_value: int,
        new_value: int,
        old_level: int,
        new_level: int,
        reason: str | None,
    ) -> None:
        channel = await resolve_modlog_channel(self.bot, guild)
        if channel is None:
            return
        embed = base_embed(title="🎚️ Ручное изменение прогресса", color=COLOR_WARNING)
        embed.add_field(name="Модератор", value=f"{moderator} (`{moderator.id}`)", inline=True)
        embed.add_field(name="Участник", value=f"{member} (`{member.id}`)", inline=True)
        embed.add_field(name="Операция", value=f"{mode} · {field} · {format_number(amount)}", inline=True)
        embed.add_field(
            name="Значение",
            value=f"{format_number(old_value)} → **{format_number(new_value)}**",
            inline=True,
        )
        embed.add_field(name="Уровень", value=f"{old_level} → **{new_level}**", inline=True)
        if reason:
            embed.add_field(name="Причина", value=truncate(reason, 1024), inline=False)
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            log.debug("Не удалось отправить лог изменения XP", exc_info=True)

    # --------------------------------------------------------------- commands

    @app_commands.command(name="rank", description="Показать уровень, опыт и лояльность")
    @app_commands.describe(member="Чей профиль показать (по умолчанию — ваш)")
    async def rank(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        target = member or interaction.user
        if not isinstance(target, discord.Member):
            raise UserError("Не удалось определить участника.")

        stats = await self.bot.repos.members.get(guild.id, target.id)
        if stats is None:
            stats = MemberStats.empty(guild.id, target.id)

        position = await self.bot.repos.members.rank_position(guild.id, target.id, "xp")
        embed = rank_embed(
            target,
            stats,
            position=position,
            total_members=guild.member_count or 0,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Топ участников по опыту или лояльности")
    @app_commands.choices(metric=METRIC_CHOICES)
    @app_commands.describe(metric="По чему сортировать")
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        metric: str = "xp",
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        if metric not in {"xp", "loyalty", "messages"}:
            metric = "xp"

        rows = await self.bot.repos.members.leaderboard(guild.id, metric, 10)
        if not rows:
            raise UserError("Пока нет данных — напишите пару сообщений и попробуйте снова.")

        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        lines: list[str] = []
        for index, stats in enumerate(rows):
            prefix = medals.get(index, f"**{index + 1}.**")
            if metric == "xp":
                value = f"ур. **{progress(stats.xp).level}**, {format_number(stats.xp)} XP"
            elif metric == "loyalty":
                value = f"{format_number(stats.loyalty)} лояльности"
            else:
                value = f"{format_number(stats.messages)} сообщений"
            lines.append(f"{prefix} <@{stats.user_id}> — {value}")

        titles = {"xp": "по опыту", "loyalty": "по лояльности", "messages": "по сообщениям"}
        embed = base_embed(
            title=f"🏆 Топ-10 {titles[metric]}",
            description="\n".join(lines),
            color=COLOR_PRIMARY,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="daily", description="Получить ежедневную награду лояльности")
    async def daily(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            raise UserError("Команда доступна только на сервере.")

        stats = await self.bot.repos.members.ensure(guild.id, member.id)
        now = discord.utils.utcnow()

        if stats.last_daily_at is not None:
            elapsed = (now - stats.last_daily_at).total_seconds()
            if elapsed < DAILY_COOLDOWN_SECONDS:
                remaining = int(DAILY_COOLDOWN_SECONDS - elapsed)
                raise UserError(f"Награда уже получена. Следующая — через **{format_duration(remaining)}**.")
            streak = stats.daily_streak + 1 if elapsed <= DAILY_STREAK_WINDOW_SECONDS else 1
        else:
            streak = 1

        base = await self.bot.settings.get_int(guild.id, "loyalty_daily_base", 10)
        step = await self.bot.settings.get_int(guild.id, "loyalty_daily_step", 2)
        cap = await self.bot.settings.get_int(guild.id, "loyalty_daily_cap", 50)
        reward = loyalty_reward(streak, base, step, cap)
        _, loyalty_multiplier = await self._multipliers(guild, member)
        reward = scaled_amount(reward, loyalty_multiplier)
        cutoff = now - timedelta(seconds=DAILY_COOLDOWN_SECONDS)

        claimed = await self.bot.repos.members.claim_daily(
            guild.id,
            member.id,
            reward,
            now,
            cutoff,
            streak,
        )
        if not claimed:
            raise UserError("Награда уже получена. Попробуйте позже.")

        stats = await self.bot.repos.members.ensure(guild.id, member.id)
        tier = loyalty_tier(stats.loyalty)
        embed = success_embed(
            f"Вы получили **{reward}** лояльности.\n"
            f"Серия: **{streak}** · Всего: **{format_number(stats.loyalty)}** {tier.emoji} {tier.name}"
        )
        embed.title = "🎁 Ежедневная награда"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------- levelconfig

    @levelconfig.command(name="add-xp", description="Начислить опыт участнику (накрутка)")
    @app_commands.describe(member="Кому начислить", amount="Сколько XP", reason="Причина")
    async def levelconfig_add_xp(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: int,
        reason: Optional[str] = None,
    ) -> None:
        await self._run_admin_change(interaction, member, amount, reason, field="xp", mode="add")

    @levelconfig.command(name="remove-xp", description="Снять опыт у участника")
    @app_commands.describe(member="У кого снять", amount="Сколько XP", reason="Причина")
    async def levelconfig_remove_xp(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: int,
        reason: Optional[str] = None,
    ) -> None:
        await self._run_admin_change(interaction, member, amount, reason, field="xp", mode="remove")

    @levelconfig.command(name="set-xp", description="Установить точное значение опыта")
    @app_commands.describe(member="Участник", amount="Новое значение XP", reason="Причина")
    async def levelconfig_set_xp(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: int,
        reason: Optional[str] = None,
    ) -> None:
        await self._run_admin_change(interaction, member, amount, reason, field="xp", mode="set")

    @levelconfig.command(name="add-loyalty", description="Начислить баллы лояльности")
    @app_commands.describe(member="Кому начислить", amount="Сколько баллов", reason="Причина")
    async def levelconfig_add_loyalty(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: int,
        reason: Optional[str] = None,
    ) -> None:
        await self._run_admin_change(interaction, member, amount, reason, field="loyalty", mode="add")

    @levelconfig.command(name="remove-loyalty", description="Снять баллы лояльности")
    @app_commands.describe(member="У кого снять", amount="Сколько баллов", reason="Причина")
    async def levelconfig_remove_loyalty(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: int,
        reason: Optional[str] = None,
    ) -> None:
        await self._run_admin_change(interaction, member, amount, reason, field="loyalty", mode="remove")

    @levelconfig.command(name="set-loyalty", description="Установить точное значение лояльности")
    @app_commands.describe(member="Участник", amount="Новое значение", reason="Причина")
    async def levelconfig_set_loyalty(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: int,
        reason: Optional[str] = None,
    ) -> None:
        await self._run_admin_change(interaction, member, amount, reason, field="loyalty", mode="set")

    async def _set_multiplier(
        self,
        interaction: discord.Interaction,
        key: str,
        value: float,
        label: str,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        if not 0 <= value <= 10:
            raise UserError("Множитель должен быть от 0 до 10.")

        await self.bot.settings.set(guild.id, key, float(value))
        await interaction.response.send_message(
            embed=success_embed(f"{label}: **×{value:g}**."),
            ephemeral=True,
        )

    @levelconfig.command(name="multiplier-xp", description="Глобальный множитель опыта")
    @app_commands.describe(value="0–10; 1 — без изменений")
    async def levelconfig_multiplier_xp(
        self,
        interaction: discord.Interaction,
        value: float,
    ) -> None:
        await self._set_multiplier(interaction, "levels_xp_multiplier", value, "Множитель опыта")

    @levelconfig.command(name="multiplier-loyalty", description="Глобальный множитель лояльности")
    @app_commands.describe(value="0–10; 1 — без изменений")
    async def levelconfig_multiplier_loyalty(
        self,
        interaction: discord.Interaction,
        value: float,
    ) -> None:
        await self._set_multiplier(
            interaction,
            "levels_loyalty_multiplier",
            value,
            "Множитель лояльности",
        )

    @levelconfig.command(name="multiplier-booster", description="Бонус для бустеров сервера")
    @app_commands.describe(value="0–10; 1 — без бонуса")
    async def levelconfig_multiplier_booster(
        self,
        interaction: discord.Interaction,
        value: float,
    ) -> None:
        await self._set_multiplier(
            interaction,
            "levels_booster_multiplier",
            value,
            "Бонус бустеров",
        )

    @levelconfig.command(name="event", description="Временный множитель опыта и лояльности")
    @app_commands.describe(value="Множитель 0–10", duration="Длительность, например 2h или 3d")
    async def levelconfig_event(
        self,
        interaction: discord.Interaction,
        value: float,
        duration: str,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        if not 0 <= value <= 10:
            raise UserError("Множитель должен быть от 0 до 10.")
        try:
            seconds = parse_duration(duration)
        except ValueError as exc:
            raise UserError(f"Не удалось разобрать длительность: {exc}") from exc
        if not 60 <= seconds <= 30 * 24 * 60 * 60:
            raise UserError("Длительность события — от 1 минуты до 30 дней.")

        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        await self.bot.settings.set(guild.id, "levels_event_multiplier", float(value))
        await self.bot.settings.set(guild.id, "levels_event_until", to_iso(until))
        await interaction.response.send_message(
            embed=success_embed(
                f"Событие **×{value:g}** включено до {discord.utils.format_dt(until, 'f')}."
            ),
            ephemeral=True,
        )

    @levelconfig.command(name="event-clear", description="Выключить временный множитель")
    async def levelconfig_event_clear(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        await self.bot.settings.set(guild.id, "levels_event_multiplier", 1.0)
        await self.bot.settings.set(guild.id, "levels_event_until", None)
        await interaction.response.send_message(
            embed=success_embed("Временный множитель выключен."),
            ephemeral=True,
        )

    @levelconfig.command(name="boost-role", description="Бонус к опыту и лояльности за роль")
    @app_commands.describe(role="Роль", value="Множитель 0–10")
    async def levelconfig_boost_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        value: float,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        if role.is_default():
            raise UserError("Нельзя назначить бонус роли @everyone.")
        if not 0 <= value <= 10:
            raise UserError("Множитель должен быть от 0 до 10.")

        boosts = await self.bot.settings.get(guild.id, "levels_role_boosts", {})
        if not isinstance(boosts, dict):
            boosts = {}
        boosts[str(role.id)] = float(value)
        await self.bot.settings.set(guild.id, "levels_role_boosts", boosts)
        await interaction.response.send_message(
            embed=success_embed(f"Роль {role.mention} даёт **×{value:g}** к XP и лояльности."),
            ephemeral=True,
        )

    @levelconfig.command(name="boost-role-remove", description="Убрать бонус роли")
    @app_commands.describe(role="Роль")
    async def levelconfig_boost_role_remove(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        boosts = await self.bot.settings.get(guild.id, "levels_role_boosts", {})
        if not isinstance(boosts, dict) or str(role.id) not in boosts:
            raise UserError(f"У роли {role.mention} нет бонуса.")
        boosts.pop(str(role.id), None)
        await self.bot.settings.set(guild.id, "levels_role_boosts", boosts)
        await interaction.response.send_message(
            embed=success_embed(f"Бонус роли {role.mention} убран."),
            ephemeral=True,
        )

    @levelconfig.command(name="boosts", description="Показать все множители и бонусы")
    async def levelconfig_boosts(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        settings = self.bot.settings

        xp_mult = await settings.get(guild.id, "levels_xp_multiplier", 1.0)
        loyalty_mult = await settings.get(guild.id, "levels_loyalty_multiplier", 1.0)
        booster_mult = await settings.get(guild.id, "levels_booster_multiplier", 1.0)
        event_mult = await settings.get(guild.id, "levels_event_multiplier", 1.0)
        event_until_raw = await settings.get(guild.id, "levels_event_until")
        boosts = await settings.get(guild.id, "levels_role_boosts", {})

        event_text = "не активно"
        if event_until_raw:
            try:
                until = datetime.fromisoformat(str(event_until_raw))
            except ValueError:
                until = None
            if until is not None:
                event_text = (
                    f"×{_as_float(event_mult):g} до {discord.utils.format_dt(until, 'f')}"
                    if event_active(until, discord.utils.utcnow())
                    else "истекло"
                )

        roles_text = "—"
        if isinstance(boosts, dict) and boosts:
            parts = []
            for role_id, value in list(boosts.items())[:15]:
                role = guild.get_role(int(role_id))
                name = role.mention if role is not None else f"`{role_id}`"
                parts.append(f"{name} — ×{float(value):g}")
            roles_text = "\n".join(parts)

        embed = base_embed(title="🚀 Множители опыта и лояльности", color=COLOR_PRIMARY)
        embed.add_field(name="Опыт", value=f"×{_as_float(xp_mult):g}", inline=True)
        embed.add_field(name="Лояльность", value=f"×{_as_float(loyalty_mult):g}", inline=True)
        embed.add_field(name="Бустеры", value=f"×{_as_float(booster_mult):g}", inline=True)
        embed.add_field(name="Событие", value=event_text, inline=False)
        embed.add_field(name="Роли", value=truncate(roles_text, 1024), inline=False)
        embed.set_footer(text="Итоговый множитель перемножается и ограничен ×100")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @levelconfig.command(name="status", description="Показать настройки уровней")
    async def levelconfig_status(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        settings = self.bot.settings

        enabled = await settings.get_bool(guild.id, "levels_enabled", True)
        xp_min = await settings.get_int(guild.id, "levels_xp_min", 15)
        xp_max = await settings.get_int(guild.id, "levels_xp_max", 25)
        cooldown = await settings.get_int(guild.id, "levels_cooldown", 60)
        channel_id = await settings.get(guild.id, "levels_channel")
        announce = await settings.get_bool(guild.id, "levels_announce", True)
        ignored = await settings.get_list(guild.id, "levels_ignored_channels")
        rewards = await self.bot.repos.level_rewards.list(guild.id)
        xp_mult = await settings.get(guild.id, "levels_xp_multiplier", 1.0)
        loyalty_mult = await settings.get(guild.id, "levels_loyalty_multiplier", 1.0)
        booster_mult = await settings.get(guild.id, "levels_booster_multiplier", 1.0)
        event_until_raw = await settings.get(guild.id, "levels_event_until")

        channel_text = f"<#{int(channel_id)}>" if channel_id else "тот же канал"
        ignored_text = ", ".join(f"<#{int(item)}>" for item in ignored[:15]) if ignored else "—"
        rewards_text = (
            ", ".join(f"**{level}** → <@&{role_id}>" for level, role_id in list(rewards.items())[:10])
            if rewards
            else "не настроены"
        )

        embed = base_embed(title="📈 Уровни и лояльность", color=COLOR_MUTED)
        embed.add_field(name="Статус", value="✅ включены" if enabled else "❌ выключены", inline=True)
        embed.add_field(name="Опыт за сообщение", value=f"{xp_min}–{xp_max}", inline=True)
        embed.add_field(name="Кулдаун", value=format_duration(cooldown), inline=True)
        embed.add_field(name="Канал объявлений", value=channel_text, inline=True)
        embed.add_field(name="Объявления", value="✅" if announce else "❌", inline=True)
        embed.add_field(name="Игнор-каналы", value=truncate(ignored_text, 1024), inline=False)
        embed.add_field(name="Награды за уровни", value=truncate(rewards_text, 1024), inline=False)
        embed.add_field(name="Множитель XP", value=f"×{_as_float(xp_mult):g}", inline=True)
        embed.add_field(name="Множитель лояльности", value=f"×{_as_float(loyalty_mult):g}", inline=True)
        embed.add_field(name="Бонус бустеров", value=f"×{_as_float(booster_mult):g}", inline=True)
        if event_until_raw:
            try:
                event_until = datetime.fromisoformat(str(event_until_raw))
            except ValueError:
                event_until = None
            if event_until is not None:
                embed.add_field(
                    name="Событие",
                    value=f"до {discord.utils.format_dt(event_until, 'f')}",
                    inline=False,
                )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @levelconfig.command(name="xp", description="Диапазон опыта за сообщение")
    @app_commands.describe(minimum="Минимум опыта", maximum="Максимум опыта")
    async def levelconfig_xp(
        self,
        interaction: discord.Interaction,
        minimum: int,
        maximum: int,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        if not (1 <= minimum <= maximum <= 1000):
            raise UserError("Нужно 1 ≤ минимум ≤ максимум ≤ 1000.")

        await self.bot.settings.set(guild.id, "levels_xp_min", minimum)
        await self.bot.settings.set(guild.id, "levels_xp_max", maximum)
        await interaction.response.send_message(
            embed=success_embed(f"Опыт за сообщение: **{minimum}–{maximum}**."),
            ephemeral=True,
        )

    @levelconfig.command(name="cooldown", description="Кулдаун между начислениями опыта (в секундах)")
    @app_commands.describe(seconds="От 5 до 3600 секунд")
    async def levelconfig_cooldown(self, interaction: discord.Interaction, seconds: int) -> None:
        guild = interaction.guild
        assert guild is not None
        if not 5 <= seconds <= 3600:
            raise UserError("Кулдаун должен быть от 5 до 3600 секунд.")

        await self.bot.settings.set(guild.id, "levels_cooldown", seconds)
        await interaction.response.send_message(
            embed=success_embed(f"Кулдаун опыта: **{format_duration(seconds)}**."),
            ephemeral=True,
        )

    @levelconfig.command(name="channel", description="Канал для объявлений о новом уровне")
    @app_commands.describe(channel="Канал; без параметра — писать в текущий канал")
    async def levelconfig_channel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None

        if channel is None:
            await self.bot.settings.set(guild.id, "levels_channel", None)
            await interaction.response.send_message(
                embed=success_embed("Объявления о новом уровне будут в том же канале."),
                ephemeral=True,
            )
            return

        permissions = channel.permissions_for(guild.me)
        if not permissions.send_messages or not permissions.embed_links:
            raise UserError(f"У бота нет прав писать embed-сообщения в {channel.mention}.")

        await self.bot.settings.set(guild.id, "levels_channel", channel.id)
        await interaction.response.send_message(
            embed=success_embed(f"Объявления о новом уровне: {channel.mention}."),
            ephemeral=True,
        )

    @levelconfig.command(name="announce", description="Включить или выключить объявления о новом уровне")
    @app_commands.describe(state="True — включить, False — выключить")
    async def levelconfig_announce(self, interaction: discord.Interaction, state: bool) -> None:
        guild = interaction.guild
        assert guild is not None
        await self.bot.settings.set(guild.id, "levels_announce", state)
        await interaction.response.send_message(
            embed=success_embed(f"Объявления о новом уровне {'включены' if state else 'выключены'}."),
            ephemeral=True,
        )

    @levelconfig.command(name="reward", description="Выдавать роль за достижение уровня")
    @app_commands.describe(level="Уровень", role="Роль-награда")
    async def levelconfig_reward(
        self,
        interaction: discord.Interaction,
        level: int,
        role: discord.Role,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        if not 1 <= level <= 500:
            raise UserError("Уровень должен быть от 1 до 500.")

        problem = role_assign_problem(guild, role)
        if problem:
            raise UserError(f"Нельзя использовать эту роль: {problem}.")

        await self.bot.repos.level_rewards.add(guild.id, level, role.id)
        await interaction.response.send_message(
            embed=success_embed(f"За **{level}** уровень будет выдаваться {role.mention}."),
            ephemeral=True,
        )

    @levelconfig.command(name="reward-remove", description="Убрать награду за уровень")
    @app_commands.describe(level="Уровень, награду за который нужно убрать")
    async def levelconfig_reward_remove(self, interaction: discord.Interaction, level: int) -> None:
        guild = interaction.guild
        assert guild is not None

        removed = await self.bot.repos.level_rewards.remove(guild.id, level)
        if not removed:
            raise UserError(f"Награды за {level} уровень нет.")

        await interaction.response.send_message(
            embed=success_embed(f"Награда за **{level}** уровень убрана."),
            ephemeral=True,
        )

    @levelconfig.command(name="ignore-channel", description="Не начислять опыт в канале")
    @app_commands.describe(channel="Канал-исключение")
    async def levelconfig_ignore_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        guild = interaction.guild
        assert guild is not None

        ignored = [int(item) for item in await self.bot.settings.get_list(guild.id, "levels_ignored_channels")]
        if channel.id in ignored:
            raise UserError(f"{channel.mention} уже в исключениях.")
        ignored.append(channel.id)
        await self.bot.settings.set(guild.id, "levels_ignored_channels", ignored)

        await interaction.response.send_message(
            embed=success_embed(f"В {channel.mention} опыт больше не начисляется."),
            ephemeral=True,
        )

    @levelconfig.command(name="unignore-channel", description="Снова начислять опыт в канале")
    @app_commands.describe(channel="Канал, который нужно вернуть")
    async def levelconfig_unignore_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        guild = interaction.guild
        assert guild is not None

        ignored = [int(item) for item in await self.bot.settings.get_list(guild.id, "levels_ignored_channels")]
        if channel.id not in ignored:
            raise UserError(f"{channel.mention} не был в исключениях.")
        ignored.remove(channel.id)
        await self.bot.settings.set(guild.id, "levels_ignored_channels", ignored)

        await interaction.response.send_message(
            embed=success_embed(f"В {channel.mention} опыт снова начисляется."),
            ephemeral=True,
        )

    @levelconfig.command(name="reset", description="Сбросить настройки уровней")
    async def levelconfig_reset(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None

        keys = [
            "levels_enabled",
            "levels_xp_min",
            "levels_xp_max",
            "levels_cooldown",
            "levels_channel",
            "levels_announce",
            "levels_message",
            "levels_ignored_channels",
            "levels_xp_multiplier",
            "levels_loyalty_multiplier",
            "levels_booster_multiplier",
            "levels_event_multiplier",
            "levels_event_until",
            "levels_role_boosts",
            "loyalty_per_message",
        ]
        for key in keys:
            await self.bot.settings.set(guild.id, key, DEFAULTS[key])

        await interaction.response.send_message(
            embed=success_embed("Настройки уровней сброшены к значениям по умолчанию."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Levels(bot))

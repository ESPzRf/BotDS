"""Воркер задач дашборда.

Дашборд не вызывает Discord напрямую для действий, требующих живого бота:
он кладёт задачу в bot_jobs, а этот ког выполняет её общими сервисами.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord.ext import commands, tasks

from core.errors import UserError
from core.moderation import apply_timeout, ban_member, kick_member, moderator_problem
from core.repositories import BotJob
from core.timeparse import format_duration, parse_duration

log = logging.getLogger(__name__)

MAX_TIMEOUT_SECONDS = 28 * 24 * 60 * 60
MAX_TEMPBAN_SECONDS = 365 * 24 * 60 * 60


class Jobs(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        if self.bot.is_ready() and not self._worker.is_running():
            self._worker.start()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self._worker.is_running():
            self._worker.start()

    def cog_unload(self) -> None:
        self._worker.cancel()

    @tasks.loop(seconds=1)
    async def _worker(self) -> None:
        await self.bot.wait_until_ready()
        try:
            job = await self.bot.repos.jobs.claim_next()
            if job is None:
                return
            try:
                result = await self._run(job)
            except UserError as exc:
                await self.bot.repos.jobs.fail(job.id, exc.message)
            except Exception as exc:  # noqa: BLE001 - задача не должна ронять цикл
                log.exception("Ошибка выполнения задачи #%s (%s)", job.id, job.type)
                await self.bot.repos.jobs.fail(job.id, str(exc), retry=True)
            else:
                await self.bot.repos.jobs.complete(job.id, result)
        except Exception:  # noqa: BLE001 - цикл не должен умирать
            log.exception("Не удалось обработать очередь задач")

    async def _run(self, job: BotJob) -> dict[str, Any]:
        handlers = {
            "moderation.warn": self._moderation_warn,
            "moderation.unwarn": self._moderation_unwarn,
            "moderation.clearwarnings": self._moderation_clear,
            "moderation.timeout": self._moderation_timeout,
            "moderation.ban": self._moderation_ban,
            "moderation.kick": self._moderation_kick,
            "ticket.close": self._ticket_close,
            "appeal.review": self._appeal_review,
            "giveaway.start": self._giveaway_start,
            "giveaway.end": self._giveaway_end,
            "giveaway.reroll": self._giveaway_reroll,
            "autorole.apply": self._autorole_apply,
            "stats.refresh": self._stats_refresh,
        }
        handler = handlers.get(job.type)
        if handler is None:
            raise UserError(f"Неизвестный тип задачи: {job.type}")
        return await handler(job)

    # ------------------------------------------------------------- helpers

    async def _actor(self, job: BotJob) -> tuple[discord.Guild, discord.Member]:
        guild = self.bot.get_guild(job.guild_id)
        if guild is None:
            raise UserError("Сервер задачи недоступен.")
        if job.actor_id is None:
            raise UserError("В задаче не указан модератор.")
        actor = guild.get_member(job.actor_id)
        if actor is None:
            try:
                actor = await guild.fetch_member(job.actor_id)
            except (discord.NotFound, discord.HTTPException) as exc:
                raise UserError("Модератор больше не на сервере.") from exc
        return guild, actor

    @staticmethod
    def _require(actor: discord.Member, guild: discord.Guild, permission: str, label: str) -> None:
        if actor.id == guild.owner_id:
            return
        if not getattr(actor.guild_permissions, permission, False):
            raise UserError(f"У модератора нет права «{label}».")

    async def _target(self, job: BotJob, guild: discord.Guild) -> discord.Member:
        try:
            user_id = int(job.payload.get("user_id", 0))
        except (TypeError, ValueError) as exc:
            raise UserError("Некорректный ID участника.") from exc
        target = guild.get_member(user_id)
        if target is None:
            try:
                target = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.HTTPException) as exc:
                raise UserError("Участник не найден на сервере.") from exc
        return target

    @staticmethod
    def _reason(job: BotJob, default: str) -> str:
        return str(job.payload.get("reason", "")).strip()[:900] or default

    # ------------------------------------------------------------ handlers

    async def _moderation_warn(self, job: BotJob) -> dict[str, Any]:
        guild, actor = await self._actor(job)
        self._require(actor, guild, "moderate_members", "Тайм-аут участников")
        target = await self._target(job, guild)
        problem = moderator_problem(guild, actor, target)
        if problem:
            raise UserError(problem)

        result = await self.bot.warning_service.add(
            guild,
            target,
            actor,
            self._reason(job, "Без причины"),
            source="manual",
        )
        return {
            "action": "warn",
            "warning_id": result.warning.id,
            "count": result.count,
            "action_note": result.action_note,
        }

    async def _moderation_unwarn(self, job: BotJob) -> dict[str, Any]:
        guild, actor = await self._actor(job)
        self._require(actor, guild, "moderate_members", "Тайм-аут участников")
        try:
            warning_id = int(job.payload.get("warning_id", 0))
        except (TypeError, ValueError) as exc:
            raise UserError("Некорректный ID предупреждения.") from exc
        warning = await self.bot.repos.warnings.get(warning_id)
        if warning is None or warning.guild_id != guild.id:
            raise UserError("Предупреждение не найдено на сервере.")
        if not await self.bot.repos.warnings.deactivate(warning_id, guild.id):
            raise UserError("Предупреждение уже снято.")
        count = await self.bot.repos.warnings.count_active(guild.id, warning.user_id)
        return {"action": "unwarn", "warning_id": warning_id, "count": count}

    async def _moderation_clear(self, job: BotJob) -> dict[str, Any]:
        guild, actor = await self._actor(job)
        self._require(actor, guild, "moderate_members", "Тайм-аут участников")
        target = await self._target(job, guild)
        problem = moderator_problem(guild, actor, target)
        if problem:
            raise UserError(problem)
        count = await self.bot.repos.warnings.clear(guild.id, target.id)
        if count == 0:
            raise UserError("У участника нет активных предупреждений.")
        return {"action": "clearwarnings", "user_id": target.id, "count": count}

    async def _moderation_timeout(self, job: BotJob) -> dict[str, Any]:
        guild, actor = await self._actor(job)
        self._require(actor, guild, "moderate_members", "Тайм-аут участников")
        target = await self._target(job, guild)
        problem = moderator_problem(guild, actor, target)
        if problem:
            raise UserError(problem)

        try:
            seconds = parse_duration(str(job.payload.get("duration", "10m")))
        except ValueError as exc:
            raise UserError(f"Не удалось разобрать длительность: {exc}") from exc
        if not 60 <= seconds <= MAX_TIMEOUT_SECONDS:
            raise UserError("Тайм-аут — от 1 минуты до 28 дней.")

        reason = self._reason(job, "Тайм-аут из дашборда")
        applied = await apply_timeout(target, seconds, reason)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=applied)
        action_id = await self.bot.repos.temp_actions.add(
            guild.id, target.id, actor.id, "timeout", reason, expires_at
        )
        return {
            "action": "timeout",
            "user_id": target.id,
            "seconds": applied,
            "duration": format_duration(applied),
            "action_id": action_id,
        }

    async def _moderation_ban(self, job: BotJob) -> dict[str, Any]:
        guild, actor = await self._actor(job)
        self._require(actor, guild, "ban_members", "Бан участников")
        target = await self._target(job, guild)
        problem = moderator_problem(guild, actor, target)
        if problem:
            raise UserError(problem)
        await ban_member(target, self._reason(job, "Бан из дашборда"), guild)
        return {"action": "ban", "user_id": target.id}

    async def _moderation_kick(self, job: BotJob) -> dict[str, Any]:
        guild, actor = await self._actor(job)
        self._require(actor, guild, "kick_members", "Кик участников")
        target = await self._target(job, guild)
        problem = moderator_problem(guild, actor, target)
        if problem:
            raise UserError(problem)
        await kick_member(target, self._reason(job, "Кик из дашборда"))
        return {"action": "kick", "user_id": target.id}

    async def _ticket_close(self, job: BotJob) -> dict[str, Any]:
        guild, actor = await self._actor(job)
        if not (
            actor.id == guild.owner_id
            or actor.guild_permissions.manage_channels
            or actor.guild_permissions.manage_guild
        ):
            raise UserError("Нужно право «Управлять каналами».")
        try:
            ticket_id = int(job.payload.get("ticket_id", 0))
        except (TypeError, ValueError) as exc:
            raise UserError("Некорректный ID тикета.") from exc
        ticket = await self.bot.repos.tickets.get_ticket(ticket_id)
        if ticket is None or ticket.guild_id != guild.id:
            raise UserError("Тикет не найден на сервере.")
        if ticket.status != "open":
            raise UserError("Тикет уже закрыт.")
        cog = self.bot.get_cog("Tickets")
        if cog is None or not hasattr(cog, "close_ticket_service"):
            raise UserError("Модуль тикетов недоступен.")
        await cog.close_ticket_service(guild, ticket, actor)
        return {"action": "ticket.close", "ticket_id": ticket.id}

    async def _appeal_review(self, job: BotJob) -> dict[str, Any]:
        guild, actor = await self._actor(job)
        try:
            appeal_id = int(job.payload.get("appeal_id", 0))
        except (TypeError, ValueError) as exc:
            raise UserError("Некорректный ID апелляции.") from exc
        approve = bool(job.payload.get("approve"))
        cog = self.bot.get_cog("Appeals")
        if cog is None or not hasattr(cog, "review_service"):
            raise UserError("Модуль апелляций недоступен.")
        text = await cog.review_service(guild, actor, appeal_id, approve=approve)
        return {"action": "appeal.review", "appeal_id": appeal_id, "text": text}

    async def _giveaway_start(self, job: BotJob) -> dict[str, Any]:
        guild, actor = await self._actor(job)
        self._require(actor, guild, "manage_guild", "Управлять сервером")
        try:
            channel_id = int(job.payload.get("channel_id", 0))
            winners = int(job.payload.get("winners", 1))
            seconds = parse_duration(str(job.payload.get("duration", "")))
        except (TypeError, ValueError) as exc:
            raise UserError(f"Некорректные параметры розыгрыша: {exc}") from exc
        required_role_id = job.payload.get("required_role_id")
        cog = self.bot.get_cog("Giveaways")
        if cog is None or not hasattr(cog, "start_service"):
            raise UserError("Модуль розыгрышей недоступен.")
        giveaway_id, _ = await cog.start_service(
            guild,
            actor.id,
            channel_id,
            str(job.payload.get("prize", "")),
            seconds,
            winners,
            int(required_role_id) if required_role_id else None,
        )
        return {"action": "giveaway.start", "giveaway_id": giveaway_id}

    async def _giveaway_end(self, job: BotJob) -> dict[str, Any]:
        guild, actor = await self._actor(job)
        self._require(actor, guild, "manage_guild", "Управлять сервером")
        try:
            giveaway_id = int(job.payload.get("giveaway_id", 0))
        except (TypeError, ValueError) as exc:
            raise UserError("Некорректный ID розыгрыша.") from exc
        cog = self.bot.get_cog("Giveaways")
        if cog is None or not hasattr(cog, "finish_service"):
            raise UserError("Модуль розыгрышей недоступен.")
        winners = await cog.finish_service(guild, giveaway_id)
        return {"action": "giveaway.end", "giveaway_id": giveaway_id, "winners": winners}

    async def _giveaway_reroll(self, job: BotJob) -> dict[str, Any]:
        guild, actor = await self._actor(job)
        self._require(actor, guild, "manage_guild", "Управлять сервером")
        try:
            giveaway_id = int(job.payload.get("giveaway_id", 0))
        except (TypeError, ValueError) as exc:
            raise UserError("Некорректный ID розыгрыша.") from exc
        cog = self.bot.get_cog("Giveaways")
        if cog is None or not hasattr(cog, "reroll_service"):
            raise UserError("Модуль розыгрышей недоступен.")
        winners = await cog.reroll_service(guild, giveaway_id)
        return {"action": "giveaway.reroll", "giveaway_id": giveaway_id, "winners": winners}

    async def _autorole_apply(self, job: BotJob) -> dict[str, Any]:
        guild, actor = await self._actor(job)
        self._require(actor, guild, "manage_roles", "Управлять ролями")
        cog = self.bot.get_cog("AutoRole")
        if cog is None or not hasattr(cog, "apply_service"):
            raise UserError("Модуль авторолей недоступен.")
        added, failed = await cog.apply_service(guild)
        return {"action": "autorole.apply", "added": added, "failed": failed}

    async def _stats_refresh(self, job: BotJob) -> dict[str, Any]:
        guild, actor = await self._actor(job)
        self._require(actor, guild, "manage_guild", "Управлять сервером")
        cog = self.bot.get_cog("Stats")
        if cog is None or not hasattr(cog, "refresh_guild"):
            raise UserError("Модуль статистики недоступен.")
        updated = await cog.refresh_guild(guild.id)
        return {"action": "stats.refresh", "updated": updated}


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Jobs(bot))

"""Капча на входе: код в личных сообщениях и роль «проверен»."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.discord_utils import resolve_modlog_channel, role_assign_problem
from core.embeds import (
    COLOR_MUTED,
    COLOR_SUCCESS,
    COLOR_WARNING,
    base_embed,
    success_embed,
    warning_embed,
)
from core.errors import UserError
from core.repositories import CaptchaPending, to_iso
from core.timeparse import format_duration

log = logging.getLogger(__name__)

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def make_code(length: int = 5) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


class CaptchaModal(discord.ui.Modal):
    def __init__(self, cog: "Captcha", guild_id: int, user_id: int, expected: str) -> None:
        super().__init__(title="Проверка на робота")
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.expected = expected
        self.code_input = discord.ui.TextInput(
            label="Код из сообщения",
            placeholder="Например A7K2P",
            min_length=5,
            max_length=5,
            required=True,
        )
        self.add_item(self.code_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                embed=warning_embed("Это не ваша проверка."),
                ephemeral=True,
            )
            return

        pending = await self.cog.bot.repos.captcha.get(self.guild_id, self.user_id)
        if pending is None:
            await interaction.response.send_message(
                embed=warning_embed("Проверка уже завершена или истекла."),
                ephemeral=True,
            )
            return

        entered = str(self.code_input.value or "").strip().upper()
        if entered != pending.code.upper():
            await interaction.response.send_message(
                embed=warning_embed("Неверный код. Попробуйте ещё раз."),
                ephemeral=True,
            )
            return

        result = await self.cog.verify(interaction.user, self.guild_id)
        if result:
            await interaction.response.send_message(embed=success_embed(result), ephemeral=True)
        else:
            await interaction.response.send_message(
                embed=warning_embed("Не удалось выдать роль. Обратитесь к администратору."),
                ephemeral=True,
            )


class CaptchaView(discord.ui.View):
    def __init__(self, cog: "Captcha", guild_id: int, user_id: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        button = discord.ui.Button(
            label="Ввести код",
            style=discord.ButtonStyle.primary,
            emoji="🤖",
            custom_id=f"captcha:start:{guild_id}:{user_id}",
        )
        button.callback = self._start
        self.add_item(button)

    async def _start(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                embed=warning_embed("Эта кнопка не для вас."),
                ephemeral=True,
            )
            return

        pending = await self.cog.bot.repos.captcha.get(self.guild_id, self.user_id)
        if pending is None:
            await interaction.response.send_message(
                embed=warning_embed("Проверка уже завершена или истекла."),
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            CaptchaModal(self.cog, self.guild_id, self.user_id, pending.code)
        )


class Captcha(commands.Cog):
    captcha = app_commands.Group(
        name="captcha",
        description="Проверка новых участников",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        try:
            pending = await self.bot.repos.captcha.all()
        except Exception:  # noqa: BLE001
            log.exception("Не удалось восстановить капчу")
            pending = []

        for item in pending:
            try:
                self.bot.add_view(CaptchaView(self, item.guild_id, item.user_id))
            except Exception:  # noqa: BLE001
                log.exception("Не удалось восстановить капчу для %s", item.user_id)
        if self.bot.is_ready() and not self._expire_loop.is_running():
            self._expire_loop.start()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self._expire_loop.is_running():
            self._expire_loop.start()

    def cog_unload(self) -> None:
        self._expire_loop.cancel()

    # ---------------------------------------------------------------- events

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        guild = member.guild
        settings = self.bot.settings
        if not await settings.get_bool(guild.id, "captcha_enabled", False):
            return

        verified_role_id = await settings.get(guild.id, "captcha_role")
        if not verified_role_id:
            return
        verified_role = guild.get_role(int(verified_role_id))
        if verified_role is None:
            return

        unverified_role_id = await settings.get(guild.id, "captcha_unverified_role")
        unverified_role = guild.get_role(int(unverified_role_id)) if unverified_role_id else None

        if unverified_role is not None:
            problem = role_assign_problem(guild, unverified_role)
            if problem is None:
                try:
                    await member.add_roles(unverified_role, reason="Капча: ожидает проверки")
                except (discord.Forbidden, discord.HTTPException):
                    log.warning("Не удалось выдать роль «не проверен»", exc_info=True)

        code = make_code()
        timeout_seconds = max(60, await settings.get_int(guild.id, "captcha_timeout", 600))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
        await self.bot.repos.captcha.upsert(guild.id, member.id, code, expires_at)

        embed = base_embed(
            title="🤖 Проверка на робота",
            description=(
                f"Сервер **{guild.name}** просит подтвердить, что вы человек.\n\n"
                f"Код: **{code}**\n\n"
                f"Нажмите кнопку и введите код. Время на проверку: "
                f"**{format_duration(timeout_seconds)}**."
            ),
            color=COLOR_WARNING,
        )
        view = CaptchaView(self, guild.id, member.id)
        try:
            await member.send(embed=embed, view=view)
            self.bot.add_view(view)
        except (discord.Forbidden, discord.HTTPException):
            log.info("Не удалось отправить капчу участнику %s (ЛС закрыты)", member.id)
            await self._log(
                guild,
                base_embed(
                    title="⚠️ Капча не отправлена",
                    description=f"{member.mention}: закрыты личные сообщения.",
                    color=COLOR_WARNING,
                ),
            )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self.bot.repos.captcha.delete(member.guild.id, member.id)

    @tasks.loop(seconds=60)
    async def _expire_loop(self) -> None:
        await self.bot.wait_until_ready()
        try:
            now = to_iso(datetime.now(timezone.utc))
            due = await self.bot.repos.captcha.due(now)
            for item in due:
                await self._expire(item)
        except Exception:  # noqa: BLE001 - цикл не должен умирать
            log.exception("Не удалось проверить просроченные капчи")

    async def _expire(self, item: CaptchaPending) -> None:
        guild = self.bot.get_guild(item.guild_id)
        if guild is None:
            await self.bot.repos.captcha.delete(item.guild_id, item.user_id)
            return

        member = guild.get_member(item.user_id)
        if member is None:
            await self.bot.repos.captcha.delete(item.guild_id, item.user_id)
            return

        kicked = False
        if await self.bot.settings.get_bool(guild.id, "captcha_kick", True):
            try:
                await member.kick(reason="Капча не пройдена")
                kicked = True
            except (discord.Forbidden, discord.HTTPException):
                log.debug("Не удалось кикнуть не прошедшего капчу", exc_info=True)

        await self.bot.repos.captcha.delete(item.guild_id, item.user_id)

        embed = base_embed(title="⌛ Капча истекла", color=COLOR_MUTED)
        embed.add_field(name="Участник", value=f"{member} (`{member.id}`)", inline=True)
        embed.add_field(name="Действие", value="кикнут" if kicked else "оставлен", inline=True)
        await self._log(guild, embed)

    async def _log(self, guild: discord.Guild, embed: discord.Embed) -> None:
        channel = await resolve_modlog_channel(self.bot, guild)
        if channel is None:
            return
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            log.debug("Не удалось отправить лог капчи", exc_info=True)

    async def verify(self, user: discord.User | discord.Member, guild_id: int) -> str | None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None
        member = guild.get_member(user.id)
        if member is None:
            return None

        verified_role_id = await self.bot.settings.get(guild.id, "captcha_role")
        verified_role = guild.get_role(int(verified_role_id)) if verified_role_id else None
        if verified_role is None:
            return None
        problem = role_assign_problem(guild, verified_role)
        if problem:
            log.warning("Капча: роль проверки небезопасна (%s): %s", verified_role.id, problem)
            return None

        unverified_role_id = await self.bot.settings.get(guild.id, "captcha_unverified_role")
        unverified_role = guild.get_role(int(unverified_role_id)) if unverified_role_id else None

        try:
            if unverified_role is not None and unverified_role in member.roles:
                await member.remove_roles(unverified_role, reason="Капча пройдена")
            if verified_role not in member.roles:
                await member.add_roles(verified_role, reason="Капча пройдена")
        except (discord.Forbidden, discord.HTTPException):
            log.warning("Не удалось выдать роль после капчи", exc_info=True)
            return None

        await self.bot.repos.captcha.delete(guild.id, member.id)
        await self._log(
            guild,
            base_embed(
                title="✅ Капча пройдена",
                description=f"{member} (`{member.id}`)",
                color=COLOR_SUCCESS,
            ),
        )
        return f"Проверка пройдена. Добро пожаловать на **{guild.name}**!"

    # --------------------------------------------------------------- commands

    @captcha.command(name="setup", description="Включить капчу и выбрать роли")
    @app_commands.describe(
        verified_role="Роль, которая выдаётся после проверки",
        unverified_role="Роль, которая выдаётся до проверки (необязательно)",
        timeout_minutes="Сколько минут даётся на проверку",
        kick="Кикать, если проверка не пройдена",
    )
    async def captcha_setup(
        self,
        interaction: discord.Interaction,
        verified_role: discord.Role,
        unverified_role: Optional[discord.Role] = None,
        timeout_minutes: int = 10,
        kick: bool = True,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        if not 1 <= timeout_minutes <= 1440:
            raise UserError("Таймаут капчи — от 1 до 1440 минут.")

        for role in (verified_role, unverified_role):
            if role is None:
                continue
            problem = role_assign_problem(guild, role)
            if problem:
                raise UserError(f"Роль {role.mention} не подходит: {problem}.")

        if unverified_role is not None and unverified_role.id == verified_role.id:
            raise UserError("Роль «проверен» и «не проверен» должны различаться.")

        settings = self.bot.settings
        await settings.set(guild.id, "captcha_role", verified_role.id)
        await settings.set(guild.id, "captcha_unverified_role", unverified_role.id if unverified_role else None)
        await settings.set(guild.id, "captcha_timeout", timeout_minutes * 60)
        await settings.set(guild.id, "captcha_kick", kick)
        await settings.set(guild.id, "captcha_enabled", True)

        await interaction.response.send_message(
            embed=success_embed(
                f"Капча включена.\n"
                f"После проверки: {verified_role.mention}\n"
                f"До проверки: {unverified_role.mention if unverified_role else '—'}\n"
                f"Время: **{timeout_minutes} мин**, кик при провале: {'да' if kick else 'нет'}."
            ),
            ephemeral=True,
        )

    @captcha.command(name="disable", description="Выключить капчу")
    async def captcha_disable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.settings.set(interaction.guild.id, "captcha_enabled", False)
        await interaction.response.send_message(
            embed=success_embed("Капча выключена."),
            ephemeral=True,
        )

    @captcha.command(name="status", description="Показать настройки капчи")
    async def captcha_status(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        settings = self.bot.settings

        enabled = await settings.get_bool(guild.id, "captcha_enabled", False)
        role_id = await settings.get(guild.id, "captcha_role")
        unverified_id = await settings.get(guild.id, "captcha_unverified_role")
        timeout_seconds = await settings.get_int(guild.id, "captcha_timeout", 600)
        kick = await settings.get_bool(guild.id, "captcha_kick", True)

        embed = base_embed(title="🤖 Капча", color=COLOR_MUTED)
        embed.add_field(name="Статус", value="✅ включена" if enabled else "❌ выключена", inline=True)
        embed.add_field(name="Роль проверенных", value=f"<@&{int(role_id)}>" if role_id else "не задана", inline=True)
        embed.add_field(
            name="Роль до проверки",
            value=f"<@&{int(unverified_id)}>" if unverified_id else "—",
            inline=True,
        )
        embed.add_field(name="Время", value=format_duration(timeout_seconds), inline=True)
        embed.add_field(name="Кик при провале", value="✅" if kick else "❌", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Captcha(bot))

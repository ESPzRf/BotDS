"""Ког автоматической выдачи ролей при входе."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.discord_utils import role_assign_problem
from core.embeds import COLOR_MUTED, COLOR_SUCCESS, base_embed
from core.errors import UserError
from core.utils import truncate

log = logging.getLogger(__name__)


class AutoRole(commands.Cog):
    autorole = app_commands.Group(
        name="autorole",
        description="Автоматическая выдача ролей новым участникам",
        default_permissions=discord.Permissions(manage_roles=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ logic

    async def _grant(self, member: discord.Member, role: discord.Role) -> str | None:
        problem = role_assign_problem(member.guild, role)
        if problem:
            return f"{role.name}: {problem}"
        if role in member.roles:
            return None
        try:
            await member.add_roles(role, reason="Автовыдача роли")
        except (discord.Forbidden, discord.HTTPException) as exc:
            return f"{role.name}: {exc}"
        return None

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not await self.bot.settings.get_bool(member.guild.id, "autorole_enabled", True):
            return

        kind = "bot" if member.bot else "human"
        roles = await self.bot.repos.autoroles.list(member.guild.id, kind)
        if not roles:
            return

        for entry in roles:
            role = member.guild.get_role(entry.role_id)
            if role is None:
                continue
            problem = await self._grant(member, role)
            if problem:
                log.warning("Автовыдача роли не удалась (%s / %s): %s", member.guild.id, member.id, problem)

    # --------------------------------------------------------------- commands

    @autorole.command(name="add", description="Добавить роль в автовыдачу")
    @app_commands.describe(
        role="Роль, которую бот будет выдавать",
        target="Выдавать людям или ботам",
    )
    @app_commands.choices(
        target=[
            app_commands.Choice(name="Людям", value="human"),
            app_commands.Choice(name="Ботам", value="bot"),
        ]
    )
    async def autorole_add(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        target: Optional[str] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        kind = target or "human"
        problem = role_assign_problem(guild, role)
        if problem:
            raise UserError(f"Нельзя использовать эту роль: {problem}.")

        created = await self.bot.repos.autoroles.add(
            guild.id,
            role.id,
            kind,
            added_by=interaction.user.id,
        )
        if not created:
            raise UserError(f"Роль {role.mention} уже есть в автовыдаче для этой категории.")

        target_name = "ботов" if kind == "bot" else "людей"
        await interaction.response.send_message(
            embed=base_embed(
                title="✅ Роль добавлена в автовыдачу",
                description=f"{role.mention} будет автоматически выдаваться новым {target_name}.",
                color=COLOR_SUCCESS,
            ),
            ephemeral=True,
        )

    @autorole.command(name="remove", description="Убрать роль из автовыдачи")
    @app_commands.describe(role="Роль, которую нужно убрать")
    async def autorole_remove(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        removed = await self.bot.repos.autoroles.remove(guild.id, role.id)
        if not removed:
            raise UserError(f"Роли {role.mention} нет в автовыдаче.")

        await interaction.response.send_message(
            embed=base_embed(
                title="✅ Роль убрана из автовыдачи",
                description=f"{role.mention} больше не будет выдаваться автоматически.",
                color=COLOR_SUCCESS,
            ),
            ephemeral=True,
        )

    @autorole.command(name="list", description="Показать роли автовыдачи")
    async def autorole_list(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        entries = await self.bot.repos.autoroles.list(guild.id)
        enabled = await self.bot.settings.get_bool(guild.id, "autorole_enabled", True)

        embed = base_embed(title="🎭 Автовыдача ролей", color=COLOR_MUTED)
        embed.add_field(name="Статус", value="✅ включена" if enabled else "❌ выключена", inline=False)

        if not entries:
            embed.add_field(name="Роли", value="Пока не добавлено ни одной роли.", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        human = [f"<@&{entry.role_id}>" for entry in entries if entry.kind == "human"]
        bots = [f"<@&{entry.role_id}>" for entry in entries if entry.kind == "bot"]
        if human:
            embed.add_field(name="Людям", value=truncate("\n".join(human), 1024), inline=False)
        if bots:
            embed.add_field(name="Ботам", value=truncate("\n".join(bots), 1024), inline=False)

        embed.set_footer(text="Выдать роли текущим участникам: /autorole apply")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @autorole.command(name="apply", description="Выдать роли автовыдачи всем текущим участникам")
    async def autorole_apply(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        await interaction.response.defer(ephemeral=True, thinking=True)
        added, failed = await self.apply_service(guild)
        await interaction.followup.send(
            embed=base_embed(
                title="✅ Автовыдача применена",
                description=f"Выдано ролей: **{added}**\nОшибок: **{failed}**",
                color=COLOR_SUCCESS,
            ),
            ephemeral=True,
        )

    async def apply_service(self, guild: discord.Guild) -> tuple[int, int]:
        entries = await self.bot.repos.autoroles.list(guild.id)
        if not entries:
            raise UserError("Сначала добавьте роли через /autorole add.")

        human_roles = [
            role
            for entry in entries
            if entry.kind == "human"
            for role in [guild.get_role(entry.role_id)]
            if role is not None
        ]
        bot_roles = [
            role
            for entry in entries
            if entry.kind == "bot"
            for role in [guild.get_role(entry.role_id)]
            if role is not None
        ]

        added = 0
        failed = 0
        for index, member in enumerate(guild.members):
            roles = bot_roles if member.bot else human_roles
            for role in roles:
                if role in member.roles:
                    continue
                problem = await self._grant(member, role)
                if problem:
                    failed += 1
                else:
                    added += 1
            if index % 25 == 0:
                await asyncio.sleep(0)
        return added, failed

    @autorole.command(name="enabled", description="Включить или выключить автовыдачу")
    @app_commands.describe(state="True — включить, False — выключить")
    async def autorole_enabled(self, interaction: discord.Interaction, state: bool) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        await self.bot.settings.set(guild.id, "autorole_enabled", state)
        await interaction.response.send_message(
            embed=base_embed(
                title="✅ Настройка сохранена",
                description=f"Автовыдача ролей {'включена' if state else 'выключена'}.",
                color=COLOR_SUCCESS,
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoRole(bot))

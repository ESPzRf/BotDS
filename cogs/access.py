"""Ограничение слэш-команд по ролям и ID пользователей."""

from __future__ import annotations

import logging
from typing import Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.command_access import COMMAND_PRESETS, CommandRule, normalize_command
from core.embeds import (
    COLOR_MUTED,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    base_embed,
    success_embed,
)
from core.errors import UserError
from core.utils import truncate

log = logging.getLogger(__name__)


async def command_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Подсказывает группы и команды, чтобы не вводить путь вручную."""

    tree = getattr(interaction.client, "tree", None)
    if tree is None:
        return []
    query = (current or "").strip().lstrip("/").casefold()
    names: set[str] = set()
    for command in tree.walk_commands():
        parts = command.qualified_name.split()
        for index in range(1, len(parts) + 1):
            names.add(" ".join(parts[:index]))
    ordered = sorted(names, key=lambda item: (item.count(" "), item))
    if query:
        exact = [item for item in ordered if item.casefold() == query]
        prefix = [item for item in ordered if item.casefold().startswith(query) and item not in exact]
        rest = [item for item in ordered if query in item.casefold() and item not in exact and item not in prefix]
        ordered = exact + prefix + rest
    presets = [
        app_commands.Choice(name=f"{name} (список)", value=name)
        for name in COMMAND_PRESETS
        if not query or query in name
    ]
    return presets[:10] + [app_commands.Choice(name=item[:100], value=item) for item in ordered[:25 - len(presets[:10])]]


def _role_list(ids: set[int]) -> str:
    return ", ".join(f"<@&{role_id}>" for role_id in sorted(ids)) or "—"


def _user_list(ids: set[int]) -> str:
    return ", ".join(f"<@{user_id}>" for user_id in sorted(ids)) or "—"


class Access(commands.Cog):
    access = app_commands.Group(
        name="access",
        description="Ограничения команд по ролям и пользователям",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ utils

    def _command_names(self) -> set[str]:
        names: set[str] = set()
        for command in self.bot.tree.walk_commands():
            parts = command.qualified_name.split()
            for index in range(1, len(parts) + 1):
                names.add(" ".join(parts[:index]))
        return names

    def _validate(self, value: str) -> str:
        name = normalize_command(value)
        if not name:
            raise UserError("Укажите команду или список, например `warn` или `moderator`.")
        if name in COMMAND_PRESETS:
            return name
        if name not in self._command_names():
            raise UserError(
                f"Команда `{name}` не найдена. Укажите точное имя или список: "
                + ", ".join(COMMAND_PRESETS)
            )
        return name

    def _resolve(self, value: str) -> tuple[list[str], str | None]:
        name = self._validate(value)
        if name in COMMAND_PRESETS:
            return list(COMMAND_PRESETS[name]), name
        if name == "access" or name.startswith("access "):
            raise UserError("Нельзя ограничивать саму команду `/access` — иначе можно потерять управление.")
        return [name], None

    async def _get_rule(self, guild_id: int, command: str) -> CommandRule:
        rule = await self.bot.repos.access.get(guild_id, command)
        return rule or CommandRule(guild_id=guild_id, command=command)

    async def _save(self, rule: CommandRule) -> None:
        if rule.is_empty():
            await self.bot.repos.access.delete(rule.guild_id, rule.command)
        else:
            await self.bot.repos.access.save(rule)

    def _summary(self, rule: CommandRule) -> discord.Embed:
        if rule.is_empty():
            embed = base_embed(
                title=f"🔓 /{rule.command}",
                description="Ограничений нет — команда доступна всем.",
                color=COLOR_SUCCESS,
            )
            return embed

        embed = base_embed(
            title=f"🔐 /{rule.command}",
            description="✅ команда включена" if rule.enabled else "⛔ команда отключена",
            color=COLOR_PRIMARY if rule.enabled else COLOR_WARNING,
        )
        embed.add_field(name="Разрешённые роли", value=truncate(_role_list(rule.allow_roles), 1024), inline=False)
        embed.add_field(name="Разрешённые пользователи", value=truncate(_user_list(rule.allow_users), 1024), inline=False)
        embed.add_field(name="Запрещённые роли", value=truncate(_role_list(rule.deny_roles), 1024), inline=False)
        embed.add_field(name="Запрещённые пользователи", value=truncate(_user_list(rule.deny_users), 1024), inline=False)
        embed.set_footer(text="Администраторы и владелец сервера всегда имеют доступ")
        return embed

    async def _apply(
        self,
        interaction: discord.Interaction,
        command_value: str,
        mutate: Callable[[CommandRule], None],
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        targets, preset = self._resolve(command_value)

        for command in targets:
            rule = await self._get_rule(guild.id, command)
            mutate(rule)
            await self._save(rule)
        self.bot.invalidate_access(guild.id)

        if preset is not None:
            embed = base_embed(
                title=f"📋 Список {preset}",
                description=f"Обновлено команд: **{len(targets)}**.",
                color=COLOR_PRIMARY,
            )
            embed.add_field(
                name="Команды",
                value=truncate(", ".join(f"`/{item}`" for item in targets), 1024),
                inline=False,
            )
            embed.set_footer(text="Список можно выдавать/снимать одной командой /access")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        name = targets[0]
        rule = await self._get_rule(guild.id, name)
        await interaction.response.send_message(embed=self._summary(rule), ephemeral=True)

    # --------------------------------------------------------------- commands

    @access.command(name="allow-role", description="Разрешить команду только указанной роли")
    @app_commands.describe(command="Команда, например levelconfig add-xp", role="Роль")
    @app_commands.autocomplete(command=command_autocomplete)
    async def allow_role(
        self,
        interaction: discord.Interaction,
        command: str,
        role: discord.Role,
    ) -> None:
        if role.is_default():
            raise UserError("Нельзя ограничивать команду ролью @everyone.")

        def mutate(rule: CommandRule) -> None:
            rule.allow_roles.add(role.id)
            rule.deny_roles.discard(role.id)

        await self._apply(interaction, command, mutate)

    @access.command(name="remove-role", description="Убрать роль из списка разрешённых")
    @app_commands.describe(command="Команда", role="Роль")
    @app_commands.autocomplete(command=command_autocomplete)
    async def remove_role(
        self,
        interaction: discord.Interaction,
        command: str,
        role: discord.Role,
    ) -> None:
        def mutate(rule: CommandRule) -> None:
            rule.allow_roles.discard(role.id)

        await self._apply(interaction, command, mutate)

    @access.command(name="allow-user", description="Разрешить команду конкретному пользователю")
    @app_commands.describe(command="Команда", user="Пользователь (можно по ID)")
    @app_commands.autocomplete(command=command_autocomplete)
    async def allow_user(
        self,
        interaction: discord.Interaction,
        command: str,
        user: discord.User,
    ) -> None:
        def mutate(rule: CommandRule) -> None:
            rule.allow_users.add(user.id)
            rule.deny_users.discard(user.id)

        await self._apply(interaction, command, mutate)

    @access.command(name="remove-user", description="Убрать пользователя из разрешённых")
    @app_commands.describe(command="Команда", user="Пользователь")
    @app_commands.autocomplete(command=command_autocomplete)
    async def remove_user(
        self,
        interaction: discord.Interaction,
        command: str,
        user: discord.User,
    ) -> None:
        def mutate(rule: CommandRule) -> None:
            rule.allow_users.discard(user.id)

        await self._apply(interaction, command, mutate)

    @access.command(name="deny-role", description="Запретить команду указанной роли")
    @app_commands.describe(command="Команда", role="Роль")
    @app_commands.autocomplete(command=command_autocomplete)
    async def deny_role(
        self,
        interaction: discord.Interaction,
        command: str,
        role: discord.Role,
    ) -> None:
        if role.is_default():
            raise UserError("Нельзя запретить команду через @everyone — используйте /access disable.")

        def mutate(rule: CommandRule) -> None:
            rule.deny_roles.add(role.id)
            rule.allow_roles.discard(role.id)

        await self._apply(interaction, command, mutate)

    @access.command(name="undeny-role", description="Убрать роль из запрещённых")
    @app_commands.describe(command="Команда", role="Роль")
    @app_commands.autocomplete(command=command_autocomplete)
    async def undeny_role(
        self,
        interaction: discord.Interaction,
        command: str,
        role: discord.Role,
    ) -> None:
        def mutate(rule: CommandRule) -> None:
            rule.deny_roles.discard(role.id)

        await self._apply(interaction, command, mutate)

    @access.command(name="deny-user", description="Запретить команду пользователю")
    @app_commands.describe(command="Команда", user="Пользователь")
    @app_commands.autocomplete(command=command_autocomplete)
    async def deny_user(
        self,
        interaction: discord.Interaction,
        command: str,
        user: discord.User,
    ) -> None:
        def mutate(rule: CommandRule) -> None:
            rule.deny_users.add(user.id)
            rule.allow_users.discard(user.id)

        await self._apply(interaction, command, mutate)

    @access.command(name="undeny-user", description="Убрать пользователя из запрещённых")
    @app_commands.describe(command="Команда", user="Пользователь")
    @app_commands.autocomplete(command=command_autocomplete)
    async def undeny_user(
        self,
        interaction: discord.Interaction,
        command: str,
        user: discord.User,
    ) -> None:
        def mutate(rule: CommandRule) -> None:
            rule.deny_users.discard(user.id)

        await self._apply(interaction, command, mutate)

    @access.command(name="disable", description="Полностью отключить команду на сервере")
    @app_commands.describe(command="Команда")
    @app_commands.autocomplete(command=command_autocomplete)
    async def disable(self, interaction: discord.Interaction, command: str) -> None:
        def mutate(rule: CommandRule) -> None:
            rule.enabled = False

        await self._apply(interaction, command, mutate)

    @access.command(name="enable", description="Включить команду обратно")
    @app_commands.describe(command="Команда")
    @app_commands.autocomplete(command=command_autocomplete)
    async def enable(self, interaction: discord.Interaction, command: str) -> None:
        def mutate(rule: CommandRule) -> None:
            rule.enabled = True

        await self._apply(interaction, command, mutate)

    @access.command(name="reset", description="Снять все ограничения с команды")
    @app_commands.describe(command="Команда")
    @app_commands.autocomplete(command=command_autocomplete)
    async def reset(self, interaction: discord.Interaction, command: str) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        targets, preset = self._resolve(command)
        deleted = 0
        for name in targets:
            if await self.bot.repos.access.delete(guild.id, name):
                deleted += 1
        self.bot.invalidate_access(guild.id)
        if preset is not None:
            await interaction.response.send_message(
                embed=success_embed(
                    f"Список **{preset}**: сняты ограничения с {deleted} команд."
                ),
                ephemeral=True,
            )
            return
        name = targets[0]
        await interaction.response.send_message(
            embed=success_embed(
                f"Ограничения для `/{name}` {'сняты' if deleted else 'не были заданы'}."
            ),
            ephemeral=True,
        )

    @access.command(name="presets", description="Показать готовые списки команд")
    async def presets_list(self, interaction: discord.Interaction) -> None:
        embed = base_embed(
            title="📋 Готовые списки команд",
            description=(
                "Список можно использовать вместо команды в `/access`: "
                "`/access allow-role command:moderator role:@Модератор`."
            ),
            color=COLOR_PRIMARY,
        )
        for name, commands in COMMAND_PRESETS.items():
            embed.add_field(
                name=f"{name} ({len(commands)})",
                value=truncate("\n".join(f"`/{item}`" for item in commands), 1024),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @access.command(name="list", description="Показать ограничения команд")
    @app_commands.describe(command="Конкретная команда (необязательно)")
    @app_commands.autocomplete(command=command_autocomplete)
    async def list_rules(
        self,
        interaction: discord.Interaction,
        command: Optional[str] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        if command:
            targets, preset = self._resolve(command)
            if preset is not None:
                embed = base_embed(
                    title=f"📋 Список {preset}",
                    description=f"Команд в списке: {len(targets)}",
                    color=COLOR_PRIMARY,
                )
                for name in targets[:20]:
                    rule = await self._get_rule(guild.id, name)
                    if rule.is_empty():
                        value = "без ограничений"
                    else:
                        bits = [
                            ("✅" if rule.enabled else "⛔")
                            + f" роли: {_role_list(rule.allow_roles)}",
                            f"пользователи: {_user_list(rule.allow_users)}",
                        ]
                        if rule.deny_roles or rule.deny_users:
                            bits.append(
                                f"запрещены: {_role_list(rule.deny_roles)} / {_user_list(rule.deny_users)}"
                            )
                        value = "\n".join(bits)
                    embed.add_field(name=f"/{name}", value=truncate(value, 1024), inline=False)
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            rule = await self._get_rule(guild.id, targets[0])
            await interaction.response.send_message(embed=self._summary(rule), ephemeral=True)
            return

        rules = await self.bot.repos.access.rules(guild.id)
        if not rules:
            await interaction.response.send_message(
                embed=base_embed(
                    title="🔐 Ограничения команд",
                    description="Ограничений нет — все команды доступны по правам Discord.",
                    color=COLOR_MUTED,
                ),
                ephemeral=True,
            )
            return

        embed = base_embed(title="🔐 Ограничения команд", color=COLOR_PRIMARY)
        for name in sorted(rules)[:20]:
            rule = rules[name]
            status = "✅" if rule.enabled else "⛔"
            bits = [f"{status} роли: {_role_list(rule.allow_roles)}", f"пользователи: {_user_list(rule.allow_users)}"]
            if rule.deny_roles or rule.deny_users:
                bits.append(f"запрещены: {_role_list(rule.deny_roles)} / {_user_list(rule.deny_users)}")
            embed.add_field(name=f"/{name}", value=truncate("\n".join(bits), 1024), inline=False)
        if len(rules) > 20:
            embed.set_footer(text=f"Показаны первые 20 из {len(rules)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Access(bot))

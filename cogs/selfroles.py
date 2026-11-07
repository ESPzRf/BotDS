"""Роли по кнопкам в embed-панелях (persistent views)."""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.discord_utils import ConfirmView, role_assign_problem
from core.embeds import (
    COLOR_MUTED,
    COLOR_SUCCESS,
    COLOR_WARNING,
    base_embed,
    error_embed,
    success_embed,
)
from core.errors import UserError
from core.repositories import SelfRoleOption, SelfRolePanel
from core.utils import parse_color, truncate

log = logging.getLogger(__name__)

MAX_BUTTONS = 25

STYLES: dict[str, discord.ButtonStyle] = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}

STYLE_CHOICES = [
    app_commands.Choice(name="Синяя", value="primary"),
    app_commands.Choice(name="Серая", value="secondary"),
    app_commands.Choice(name="Зелёная", value="success"),
    app_commands.Choice(name="Красная", value="danger"),
]


def parse_emoji(value: str | None) -> discord.PartialEmoji | str | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return discord.PartialEmoji.from_str(value)
    except (ValueError, TypeError):
        return value


def build_panel_embed(
    panel: SelfRolePanel,
    options: list[SelfRoleOption],
    guild: discord.Guild,
    groups: dict[int, str] | None = None,
) -> discord.Embed:
    embed = base_embed(
        title=panel.title,
        description=panel.description or "Выберите роли кнопками ниже.",
        color=panel.color,
    )

    if options:
        lines: list[str] = []
        for option in options:
            role = guild.get_role(option.role_id)
            name = role.mention if role is not None else f"`удалённая роль {option.role_id}`"
            prefix = f"{option.emoji} " if option.emoji else ""
            group = (groups or {}).get(option.role_id)
            suffix = f" · _группа: {group}_" if group else ""
            lines.append(f"{prefix}{name}{suffix}")
        embed.add_field(name="Доступные роли", value=truncate("\n".join(lines), 1024), inline=False)

    embed.set_footer(text=f"Панель #{panel.id} • нажмите кнопку, чтобы получить или снять роль")
    return embed


class SelfRoleButton(discord.ui.Button):
    def __init__(
        self,
        panel_id: int,
        option: SelfRoleOption,
        exclusive_role_ids: set[int] | None = None,
    ) -> None:
        super().__init__(
            label=(option.label or f"Роль {option.role_id}")[:80],
            style=STYLES.get(option.style, discord.ButtonStyle.secondary),
            custom_id=f"selfrole:{panel_id}:{option.role_id}",
            emoji=parse_emoji(option.emoji),
        )
        self.panel_id = panel_id
        self.role_id = option.role_id
        self.exclusive_role_ids = exclusive_role_ids or set()

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(
                embed=error_embed("Кнопка работает только на сервере."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        role = guild.get_role(self.role_id)
        if role is None:
            await interaction.followup.send(
                embed=error_embed("Эта роль была удалена с сервера. Сообщите администратору."),
                ephemeral=True,
            )
            return

        problem = role_assign_problem(guild, role)
        if problem:
            await interaction.followup.send(
                embed=error_embed(f"Не удалось изменить роль: {problem}."),
                ephemeral=True,
            )
            return

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Роль по кнопке (снятие)")
                embed = success_embed(f"Роль {role.mention} снята.")
            else:
                removed: list[discord.Role] = []
                if self.exclusive_role_ids:
                    for role_id in self.exclusive_role_ids:
                        if role_id == role.id:
                            continue
                        other = guild.get_role(role_id)
                        if other is not None and other in member.roles:
                            removed.append(other)
                if removed:
                    await member.remove_roles(*removed, reason="Эксклюзивная роль по кнопке")
                await member.add_roles(role, reason="Роль по кнопке (выдача)")
                text = f"Роль {role.mention} выдана."
                if removed:
                    text += "\nСняты: " + ", ".join(item.mention for item in removed)
                embed = success_embed(text)
        except discord.Forbidden:
            embed = error_embed("У бота недостаточно прав для управления этой ролью.")
        except discord.HTTPException as exc:
            embed = error_embed(f"Discord отклонил запрос: {exc}")

        await interaction.followup.send(embed=embed, ephemeral=True)


def build_panel_view(
    panel: SelfRolePanel,
    options: list[SelfRoleOption],
    groups: dict[int, str] | None = None,
) -> discord.ui.View | None:
    if not options:
        return None
    groups = groups or {}
    view = discord.ui.View(timeout=None)
    for option in options:
        group = groups.get(option.role_id)
        exclusive = {
            role_id
            for role_id, group_name in groups.items()
            if group is not None and group_name == group and role_id != option.role_id
        }
        view.add_item(SelfRoleButton(panel.id, option, exclusive))
    return view


def warning_embed_for_builder(exclusive: bool) -> discord.Embed:
    embed = base_embed(
        title="🛠️ Мастер панели ролей",
        description=(
            "Выберите роли в списке ниже и нажмите **«Создать панель»**.\n\n"
            "Бот создаст embed с кнопками: нажатие выдаёт роль, повторное — снимает."
        ),
        color=COLOR_WARNING,
    )
    if exclusive:
        embed.add_field(
            name="Эксклюзивный режим",
            value="Можно выбрать только одну роль из панели — остальные будут сняты автоматически.",
            inline=False,
        )
    embed.set_footer(text="До 25 ролей; роли выше роли бота будут пропущены")
    return embed


class SelfRoleBuilderView(discord.ui.View):
    """Мастер: выбрать роли селектором и собрать панель с кнопками."""

    def __init__(
        self,
        cog: "SelfRoles",
        author_id: int,
        channel: discord.TextChannel,
        title: str,
        description: str,
        color: int,
        exclusive: bool,
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.author_id = author_id
        self.channel = channel
        self.title = title
        self.description = description
        self.color = color
        self.exclusive = exclusive
        self.selected: list[discord.Role] = []

        self.select = discord.ui.RoleSelect(
            placeholder="Выберите роли (до 25)",
            min_values=1,
            max_values=MAX_BUTTONS,
        )
        self.select.callback = self._select
        self.add_item(self.select)

        self.create_button = discord.ui.Button(
            label="Создать панель",
            style=discord.ButtonStyle.success,
            emoji="🎛️",
        )
        self.create_button.callback = self._create
        self.add_item(self.create_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=error_embed("Этот мастер открыт другим администратором."),
                ephemeral=True,
            )
            return False
        return True

    @staticmethod
    def _selected_roles(select: discord.ui.RoleSelect) -> list[discord.Role]:
        roles: list[discord.Role] = []
        for value in select.values:
            if isinstance(value, discord.Role):
                roles.append(value)
            else:
                role = getattr(value, "role", None)
                if isinstance(role, discord.Role):
                    roles.append(role)
        return roles

    async def _select(self, interaction: discord.Interaction) -> None:
        self.selected = self._selected_roles(self.select)
        await interaction.response.send_message(
            embed=success_embed(f"Выбрано ролей: **{len(self.selected)}**. Нажмите «Создать панель»."),
            ephemeral=True,
        )

    async def _create(self, interaction: discord.Interaction) -> None:
        if not self.selected:
            await interaction.response.send_message(
                embed=error_embed("Сначала выберите роли в списке."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        valid: list[discord.Role] = []
        skipped: list[str] = []
        for role in self.selected[:MAX_BUTTONS]:
            problem = role_assign_problem(self.channel.guild, role)
            if problem:
                skipped.append(f"{role.name}: {problem}")
            else:
                valid.append(role)

        if not valid:
            await interaction.edit_original_response(
                embed=error_embed("Ни одну из выбранных ролей нельзя выдавать."),
                view=None,
            )
            return

        panel_id = await self.cog.bot.repos.selfroles.create_panel(
            self.channel.guild.id,
            self.channel.id,
            self.title,
            self.description,
            self.color,
        )
        for role in valid:
            await self.cog.bot.repos.selfroles.add_option(
                panel_id,
                role.id,
                label=role.name[:80],
                style="secondary",
            )
            if self.exclusive:
                await self.cog.bot.repos.selfroles.set_group(panel_id, role.id, "default")

        panel = await self.cog.bot.repos.selfroles.get_panel(panel_id)
        if panel is None:  # pragma: no cover
            await interaction.edit_original_response(
                embed=error_embed("Не удалось создать панель."),
                view=None,
            )
            return

        try:
            message = await self.cog._render(panel)
        except (UserError, discord.HTTPException) as exc:
            await interaction.edit_original_response(
                embed=error_embed(str(exc)),
                view=None,
            )
            return

        text = f"Панель **#{panel_id}** создана: {message.jump_url}"
        if skipped:
            text += "\nПропущены:\n" + "\n".join(skipped[:10])
        if self.exclusive:
            text += "\nРежим: одна роль из панели (остальные снимаются)."

        await interaction.edit_original_response(
            embed=success_embed(text),
            view=None,
        )


class SelfRoles(commands.Cog):
    selfrole = app_commands.Group(
        name="selfrole",
        description="Панели с ролями по кнопкам",
        default_permissions=discord.Permissions(manage_roles=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """Восстанавливает persistent views после перезапуска бота."""

        try:
            panels = await self.bot.repos.selfroles.all_panels()
        except Exception:  # noqa: BLE001 - бот должен запуститься даже при сбое
            log.exception("Не удалось восстановить панели ролей")
            return

        restored = 0
        for panel in panels:
            options = await self.bot.repos.selfroles.options(panel.id)
            groups = await self.bot.repos.selfroles.groups(panel.id)
            view = build_panel_view(panel, options, groups)
            if view is not None:
                try:
                    self.bot.add_view(view)
                    restored += 1
                except Exception:  # noqa: BLE001 - одна битая панель не должна ломать старт
                    log.exception("Не удалось восстановить панель #%s", panel.id)

        log.info("Восстановлено панелей с ролями: %s", restored)

    # ------------------------------------------------------------------ utils

    async def _get_panel(self, interaction: discord.Interaction, panel_id: int) -> SelfRolePanel:
        if interaction.guild is None:
            raise UserError("Команда доступна только на сервере.")
        panel = await self.bot.repos.selfroles.get_panel(panel_id)
        if panel is None or panel.guild_id != interaction.guild.id:
            raise UserError(f"Панель #{panel_id} не найдена на этом сервере.")
        return panel

    async def _render(self, panel: SelfRolePanel) -> discord.Message:
        guild = self.bot.get_guild(panel.guild_id)
        if guild is None:
            raise UserError("Сервер панели недоступен.")

        options = await self.bot.repos.selfroles.options(panel.id)
        groups = await self.bot.repos.selfroles.groups(panel.id)
        embed = build_panel_embed(panel, options, guild, groups)
        view = build_panel_view(panel, options, groups)

        channel = self.bot.get_channel(panel.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(panel.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                raise UserError("Канал панели не найден или недоступен.") from exc

        if not isinstance(channel, discord.TextChannel):
            raise UserError("Панель можно размещать только в текстовом канале.")

        if panel.message_id:
            try:
                message = await channel.fetch_message(panel.message_id)
                await message.edit(embed=embed, view=view)
                if view is not None:
                    self._register_view(view)
                return message
            except discord.NotFound:
                pass
            except discord.Forbidden as exc:
                raise UserError("У бота нет прав редактировать сообщение панели.") from exc

        try:
            message = await channel.send(
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden as exc:
            raise UserError("У бота нет прав писать в канал панели.") from exc

        if view is not None:
            self._register_view(view)
        await self.bot.repos.selfroles.set_message_id(panel.id, message.id)
        return message

    def _register_view(self, view: discord.ui.View) -> None:
        """Регистрирует persistent view, чтобы кнопки работали и после рестарта."""

        try:
            self.bot.add_view(view)
        except Exception:  # noqa: BLE001 - не должна ломать рендер панели
            log.warning("Не удалось зарегистрировать view панели", exc_info=True)

    # --------------------------------------------------------------- commands

    @selfrole.command(name="create", description="Создать панель с ролями в канале")
    @app_commands.describe(
        channel="Канал, где будет панель",
        title="Заголовок панели",
        description="Описание/инструкция под заголовком",
        color="Цвет панели в HEX, например #5865F2",
    )
    async def selfrole_create(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        title: str,
        description: Optional[str] = None,
        color: Optional[str] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        await interaction.response.defer(ephemeral=True, thinking=True)

        permissions = channel.permissions_for(guild.me)
        if not permissions.send_messages or not permissions.embed_links:
            raise UserError(f"У бота нет прав писать embed-сообщения в {channel.mention}.")

        try:
            panel_color = parse_color(color) if color else 0x5865F2
        except ValueError as exc:
            raise UserError(str(exc)) from exc

        if not title.strip():
            raise UserError("Заголовок панели не может быть пустым.")

        panel_id = await self.bot.repos.selfroles.create_panel(
            guild.id,
            channel.id,
            title.strip()[:256],
            (description or "Выберите роли кнопками ниже.").strip()[:2000],
            panel_color,
        )
        panel = await self.bot.repos.selfroles.get_panel(panel_id)
        if panel is None:  # pragma: no cover
            raise UserError("Не удалось создать панель.")

        message = await self._render(panel)

        embed = base_embed(
            title="✅ Панель создана",
            description=(
                f"Панель **#{panel_id}** размещена в {channel.mention}.\n"
                f"Добавьте роли: `/selfrole add panel_id:{panel_id} role:@Роль`\n"
                f"[Перейти к сообщению]({message.jump_url})"
            ),
            color=COLOR_SUCCESS,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @selfrole.command(name="build", description="Мастер: выбрать роли и создать embed-панель с кнопками")
    @app_commands.describe(
        channel="Канал, где будет панель",
        title="Заголовок панели",
        description="Описание/инструкция",
        color="Цвет в HEX, например #5865F2",
        exclusive="Только одна роль из панели: выбор новой снимает остальные",
    )
    async def selfrole_build(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        title: str,
        description: Optional[str] = None,
        color: Optional[str] = None,
        exclusive: bool = False,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        permissions = channel.permissions_for(guild.me)
        if not permissions.send_messages or not permissions.embed_links:
            raise UserError(f"У бота нет прав писать embed-сообщения в {channel.mention}.")
        if not title.strip():
            raise UserError("Заголовок панели не может быть пустым.")
        try:
            panel_color = parse_color(color) if color else 0x5865F2
        except ValueError as exc:
            raise UserError(str(exc)) from exc

        view = SelfRoleBuilderView(
            self,
            interaction.user.id,
            channel,
            title.strip()[:256],
            (description or "Выберите роли кнопками ниже.").strip()[:2000],
            panel_color,
            exclusive,
        )
        embed = warning_embed_for_builder(exclusive)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @selfrole.command(name="group", description="Сделать роли эксклюзивными: выдача одной снимает другие")
    @app_commands.describe(
        panel_id="ID панели",
        role="Роль на панели",
        name="Название группы (например «Цвет»)",
    )
    async def selfrole_group(
        self,
        interaction: discord.Interaction,
        panel_id: int,
        role: discord.Role,
        name: str,
    ) -> None:
        panel = await self._get_panel(interaction, panel_id)
        options = await self.bot.repos.selfroles.options(panel.id)
        if not any(option.role_id == role.id for option in options):
            raise UserError("На этой панели нет кнопки с такой ролью.")
        group_name = name.strip()[:32]
        if not group_name:
            raise UserError("Название группы не может быть пустым.")

        await self.bot.repos.selfroles.set_group(panel.id, role.id, group_name)
        await self._render(panel)
        await interaction.response.send_message(
            embed=success_embed(
                f"Роль {role.mention} добавлена в эксклюзивную группу **{group_name}**."
            ),
            ephemeral=True,
        )

    @selfrole.command(name="ungroup", description="Убрать роль из эксклюзивной группы")
    @app_commands.describe(panel_id="ID панели", role="Роль")
    async def selfrole_ungroup(
        self,
        interaction: discord.Interaction,
        panel_id: int,
        role: discord.Role,
    ) -> None:
        panel = await self._get_panel(interaction, panel_id)
        removed = await self.bot.repos.selfroles.clear_group(panel.id, role.id)
        if not removed:
            raise UserError(f"Роль {role.mention} не находится в эксклюзивной группе.")
        await self._render(panel)
        await interaction.response.send_message(
            embed=success_embed(f"Роль {role.mention} убрана из эксклюзивной группы."),
            ephemeral=True,
        )

    @selfrole.command(name="groups", description="Показать эксклюзивные группы панели")
    @app_commands.describe(panel_id="ID панели")
    async def selfrole_groups(self, interaction: discord.Interaction, panel_id: int) -> None:
        panel = await self._get_panel(interaction, panel_id)
        groups = await self.bot.repos.selfroles.groups(panel.id)
        embed = base_embed(title=f"🔗 Группы панели #{panel.id}", color=COLOR_MUTED)
        if not groups:
            embed.description = "Эксклюзивных групп нет."
        else:
            by_group: dict[str, list[int]] = {}
            for role_id, group_name in groups.items():
                by_group.setdefault(group_name, []).append(role_id)
            for group_name, role_ids in by_group.items():
                roles = ", ".join(f"<@&{role_id}>" for role_id in role_ids)
                embed.add_field(name=group_name, value=truncate(roles, 1024), inline=False)
        embed.set_footer(text="При выдаче роли из группы остальные роли группы снимаются")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @selfrole.command(name="add", description="Добавить кнопку с ролью на панель")
    @app_commands.describe(
        panel_id="ID панели (виден в /selfrole list)",
        role="Роль, которую будет выдавать кнопка",
        label="Текст на кнопке (по умолчанию — имя роли)",
        emoji="Эмодзи на кнопке (юникод или <:name:id>)",
        style="Стиль кнопки: primary, secondary, success, danger",
    )
    @app_commands.choices(style=STYLE_CHOICES)
    async def selfrole_add(
        self,
        interaction: discord.Interaction,
        panel_id: int,
        role: discord.Role,
        label: Optional[str] = None,
        emoji: Optional[str] = None,
        style: str = "secondary",
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        await interaction.response.defer(ephemeral=True, thinking=True)

        panel = await self._get_panel(interaction, panel_id)
        options = await self.bot.repos.selfroles.options(panel.id)

        existing = next((option for option in options if option.role_id == role.id), None)
        if existing is None and len(options) >= MAX_BUTTONS:
            raise UserError(f"На панели уже максимум кнопок ({MAX_BUTTONS}).")

        problem = role_assign_problem(guild, role)
        if problem:
            raise UserError(f"Нельзя добавить эту роль: {problem}.")

        if style not in STYLES:
            style = "secondary"

        await self.bot.repos.selfroles.add_option(
            panel.id,
            role.id,
            label=(label.strip()[:80] if label and label.strip() else None),
            emoji=(emoji.strip() if emoji and emoji.strip() else None),
            style=style,
        )
        await self._render(panel)

        action = "обновлена" if existing else "добавлена"
        await interaction.followup.send(
            embed=success_embed(f"Кнопка для {role.mention} {action} на панели **#{panel.id}**."),
            ephemeral=True,
        )

    @selfrole.command(name="remove", description="Убрать кнопку с ролью с панели")
    @app_commands.describe(panel_id="ID панели", role="Роль, которую нужно убрать")
    async def selfrole_remove(
        self,
        interaction: discord.Interaction,
        panel_id: int,
        role: discord.Role,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        panel = await self._get_panel(interaction, panel_id)
        removed = await self.bot.repos.selfroles.remove_option(panel.id, role.id)
        if not removed:
            raise UserError("На этой панели нет кнопки с такой ролью.")

        await self._render(panel)
        await interaction.followup.send(
            embed=success_embed(f"Кнопка для {role.mention} убрана с панели **#{panel.id}**."),
            ephemeral=True,
        )

    @selfrole.command(name="edit", description="Изменить заголовок, описание или цвет панели")
    @app_commands.describe(
        panel_id="ID панели",
        title="Новый заголовок",
        description="Новое описание",
        color="Новый цвет в HEX, например #ED4245",
    )
    async def selfrole_edit(
        self,
        interaction: discord.Interaction,
        panel_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        panel = await self._get_panel(interaction, panel_id)
        if title is None and description is None and color is None:
            raise UserError("Укажите хотя бы один параметр: title, description или color.")

        panel_color: int | None = None
        if color is not None:
            try:
                panel_color = parse_color(color)
            except ValueError as exc:
                raise UserError(str(exc)) from exc

        await self.bot.repos.selfroles.update_panel(
            panel.id,
            title=title.strip() if title is not None else None,
            description=description.strip() if description is not None else None,
            color=panel_color,
        )

        updated = await self.bot.repos.selfroles.get_panel(panel.id)
        if updated is not None:
            await self._render(updated)

        await interaction.followup.send(
            embed=success_embed(f"Панель **#{panel.id}** обновлена."),
            ephemeral=True,
        )

    @selfrole.command(name="delete", description="Удалить панель и её сообщение")
    @app_commands.describe(panel_id="ID панели")
    async def selfrole_delete(self, interaction: discord.Interaction, panel_id: int) -> None:
        panel = await self._get_panel(interaction, panel_id)

        view = ConfirmView(interaction.user.id, confirm_label="Удалить панель")
        await interaction.response.send_message(
            embed=warning_embed_for_delete(panel.id),
            view=view,
            ephemeral=True,
        )
        await view.wait()

        if view.value is not True:
            await interaction.edit_original_response(
                embed=base_embed(title="Удаление отменено", color=COLOR_MUTED),
                view=None,
            )
            return

        if panel.message_id:
            channel = self.bot.get_channel(panel.channel_id)
            if isinstance(channel, discord.TextChannel):
                try:
                    message = await channel.fetch_message(panel.message_id)
                    await message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

        await self.bot.repos.selfroles.delete_panel(panel.id)
        await interaction.edit_original_response(
            embed=success_embed(f"Панель **#{panel.id}** удалена."),
            view=None,
        )

    @selfrole.command(name="list", description="Список панелей с ролями")
    async def selfrole_list(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        panels = await self.bot.repos.selfroles.panels(guild.id)
        if not panels:
            await interaction.response.send_message(
                embed=base_embed(
                    title="Панели ролей",
                    description="Панелей пока нет. Создайте первую: `/selfrole create`.",
                    color=COLOR_MUTED,
                ),
                ephemeral=True,
            )
            return

        embed = base_embed(title="🎛️ Панели ролей", color=COLOR_MUTED)
        for panel in panels[:10]:
            options = await self.bot.repos.selfroles.options(panel.id)
            channel = guild.get_channel(panel.channel_id)
            channel_text = channel.mention if channel else "канал удалён"
            link = f"[сообщение](https://discord.com/channels/{guild.id}/{panel.channel_id}/{panel.message_id})"
            value = f"Канал: {channel_text}\nКнопок: **{len(options)}**"
            if panel.message_id:
                value += f" • {link}"
            embed.add_field(
                name=truncate(f"#{panel.id} — {panel.title}", 256),
                value=value,
                inline=False,
            )

        if len(panels) > 10:
            embed.set_footer(text=f"Показаны первые 10 из {len(panels)} панелей")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @selfrole.command(name="refresh", description="Перерисовать все панели сервера")
    async def selfrole_refresh(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        panels = await self.bot.repos.selfroles.panels(guild.id)
        await interaction.response.defer(ephemeral=True, thinking=True)

        updated = 0
        errors: list[str] = []
        for panel in panels:
            try:
                await self._render(panel)
                updated += 1
            except UserError as exc:
                errors.append(f"#{panel.id}: {exc}")
            except discord.HTTPException as exc:
                errors.append(f"#{panel.id}: {exc}")

        embed = base_embed(
            title="🔄 Панели обновлены",
            description=f"Обновлено: **{updated}** из **{len(panels)}**.",
            color=COLOR_SUCCESS,
        )
        if errors:
            embed.add_field(name="Ошибки", value=truncate("\n".join(errors[:10]), 1024), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)


def warning_embed_for_delete(panel_id: int) -> discord.Embed:
    return base_embed(
        title="Удалить панель?",
        description=f"Панель **#{panel_id}** и её сообщение будут удалены. Это действие необратимо.",
        color=COLOR_WARNING,
    )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SelfRoles(bot))

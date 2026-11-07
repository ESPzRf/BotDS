"""Автомодерация: пул запрещённых слов, удаление, предупреждения и тайм-ауты."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.discord_utils import ConfirmView, resolve_modlog_channel, send_temp
from core.embeds import (
    COLOR_DANGER,
    COLOR_MUTED,
    base_embed,
    error_embed,
    success_embed,
)
from core.errors import UserError
from core.moderation import apply_timeout
from core.textfilter import WordMatcher
from core.timeparse import format_duration
from core.utils import truncate

log = logging.getLogger(__name__)

ACTION_CHOICES = [
    app_commands.Choice(name="Только удалять сообщение", value="delete"),
    app_commands.Choice(name="Удалять + предупреждение", value="warn"),
    app_commands.Choice(name="Удалять + тайм-аут", value="timeout"),
]

ACTION_LABELS = {
    "delete": "удаление сообщения",
    "warn": "удаление + предупреждение",
    "timeout": "удаление + тайм-аут",
}


class AutoMod(commands.Cog):
    automod = app_commands.Group(
        name="automod",
        description="Фильтр запрещённых слов",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._matchers: dict[int, WordMatcher] = {}

    # --------------------------------------------------------------- matcher

    async def _matcher(self, guild_id: int) -> WordMatcher:
        matcher = self._matchers.get(guild_id)
        if matcher is None:
            words = await self.bot.repos.words.list(guild_id)
            matcher = WordMatcher(words)
            self._matchers[guild_id] = matcher
        return matcher

    def _invalidate(self, guild_id: int) -> None:
        self._matchers.pop(guild_id, None)

    def cog_unload(self) -> None:
        self._matchers.clear()

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

        if not await settings.get_bool(guild.id, "automod_enabled", False):
            return
        if message.author.guild_permissions.manage_messages:
            return

        ignored_channels = await settings.get_list(guild.id, "automod_ignored_channels")
        if message.channel.id in {int(channel_id) for channel_id in ignored_channels}:
            return

        ignored_roles = {int(role_id) for role_id in await settings.get_list(guild.id, "automod_ignored_roles")}
        if ignored_roles and any(role.id in ignored_roles for role in message.author.roles):
            return

        matcher = await self._matcher(guild.id)
        if not matcher:
            return

        hit = matcher.find(message.content)
        if hit is None:
            return

        await self._punish(message, hit)

    async def _punish(self, message: discord.Message, hit: str) -> None:
        guild = message.guild
        member = message.author
        if guild is None or not isinstance(member, discord.Member):
            return

        settings = self.bot.settings
        action = str(await settings.get(guild.id, "automod_action", "delete"))
        deleted = False
        try:
            await message.delete()
            deleted = True
        except discord.NotFound:
            pass
        except (discord.Forbidden, discord.HTTPException):
            log.debug("Автомод: не удалось удалить сообщение", exc_info=True)

        action_text = "только удаление" if action == "delete" else ACTION_LABELS.get(action, action)

        if action == "warn":
            result = await self.bot.warning_service.add(
                guild,
                member,
                None,
                f"Запрещённое слово: «{hit}»",
                source="automod",
                log_event=False,
            )
            action_text = f"предупреждение #{result.count}"
            if result.action_note:
                action_text += f" ({result.action_note})"
        elif action == "timeout":
            seconds = await settings.get_int(guild.id, "automod_timeout", 600)
            try:
                applied = await apply_timeout(
                    member,
                    seconds,
                    f"Автомодерация: запрещённое слово «{hit}»",
                )
                action_text = f"тайм-аут на {format_duration(applied)}"
            except UserError as exc:
                action_text = f"тайм-аут не выдан: {exc}"

        if await settings.get_bool(guild.id, "automod_notify", True):
            await send_temp(
                message.channel,
                f"⚠️ {member.mention}, сообщение содержит запрещённое слово и было удалено.",
                mention_user=member,
            )

        await self._log(guild, member, message, hit, action_text, deleted)

    async def _log(
        self,
        guild: discord.Guild,
        member: discord.Member,
        message: discord.Message,
        hit: str,
        action_text: str,
        deleted: bool,
    ) -> None:
        if not await self.bot.settings.get_bool(guild.id, "log_moderation", True):
            return
        channel = await resolve_modlog_channel(self.bot, guild)
        if channel is None:
            return

        embed = base_embed(title="🚫 Автомодерация", color=COLOR_DANGER)
        embed.add_field(name="Участник", value=f"{member} (`{member.id}`)", inline=True)
        embed.add_field(name="Канал", value=message.channel.mention, inline=True)
        embed.add_field(name="Слово", value=f"`{truncate(hit, 100)}`", inline=True)
        embed.add_field(
            name="Сообщение",
            value=truncate(message.content, 900) or "*(пусто)*",
            inline=False,
        )
        embed.add_field(
            name="Действие",
            value=truncate(action_text, 1024) + ("" if deleted else "\n⚠️ сообщение не удалено"),
            inline=False,
        )

        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            log.debug("Автомод: не удалось отправить лог", exc_info=True)

    # --------------------------------------------------------------- helpers

    async def _word_list_key(self, interaction: discord.Interaction, key: str) -> list[int]:
        assert interaction.guild is not None
        values = await self.bot.settings.get_list(interaction.guild.id, key)
        return [int(value) for value in values]

    async def _toggle_ignored(
        self,
        interaction: discord.Interaction,
        key: str,
        value: int,
        *,
        add: bool,
    ) -> bool:
        guild = interaction.guild
        assert guild is not None
        values = await self._word_list_key(interaction, key)
        changed = False
        if add and value not in values:
            values.append(value)
            changed = True
        elif not add and value in values:
            values.remove(value)
            changed = True
        if changed:
            await self.bot.settings.set(guild.id, key, values)
        return changed

    # --------------------------------------------------------------- commands

    @automod.command(name="enable", description="Включить автомодерацию")
    async def automod_enable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.settings.set(interaction.guild.id, "automod_enabled", True)
        await interaction.response.send_message(
            embed=success_embed("Автомодерация включена. Не забудьте добавить слова: `/automod add`."),
            ephemeral=True,
        )

    @automod.command(name="disable", description="Выключить автомодерацию")
    async def automod_disable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.settings.set(interaction.guild.id, "automod_enabled", False)
        await interaction.response.send_message(
            embed=success_embed("Автомодерация выключена."),
            ephemeral=True,
        )

    @automod.command(name="add", description="Добавить слово в пул запрещённых")
    @app_commands.describe(
        word="Слово или фраза; * внутри включает режим подстроки, например дур*",
    )
    async def automod_add(self, interaction: discord.Interaction, word: str) -> None:
        guild = interaction.guild
        assert guild is not None

        cleaned = word.strip().casefold()
        if len(cleaned) < 2:
            raise UserError("Слово должно содержать минимум 2 символа.")
        if len(cleaned) > 64:
            raise UserError("Слово не должно быть длиннее 64 символов.")

        added = await self.bot.repos.words.add(guild.id, cleaned, interaction.user.id)
        if not added:
            raise UserError(f"Слово `{cleaned}` уже есть в пуле.")

        self._invalidate(guild.id)
        await interaction.response.send_message(
            embed=success_embed(f"Слово `{cleaned}` добавлено в пул запрещённых."),
            ephemeral=True,
        )

    @automod.command(name="remove", description="Убрать слово из пула запрещённых")
    @app_commands.describe(word="Слово, которое нужно удалить")
    async def automod_remove(self, interaction: discord.Interaction, word: str) -> None:
        guild = interaction.guild
        assert guild is not None

        cleaned = word.strip().casefold()
        removed = await self.bot.repos.words.remove(guild.id, cleaned)
        if not removed:
            raise UserError(f"Слова `{cleaned}` нет в пуле.")

        self._invalidate(guild.id)
        await interaction.response.send_message(
            embed=success_embed(f"Слово `{cleaned}` удалено из пула."),
            ephemeral=True,
        )

    @automod.command(name="list", description="Показать запрещённые слова")
    async def automod_list(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None

        words = await self.bot.repos.words.list(guild.id)
        if not words:
            await interaction.response.send_message(
                embed=base_embed(
                    title="🚫 Запрещённые слова",
                    description="Пул пуст. Добавьте слова командой `/automod add`.",
                    color=COLOR_MUTED,
                ),
                ephemeral=True,
            )
            return

        text = ", ".join(f"`{word}`" for word in words)
        embed = base_embed(
            title=f"🚫 Запрещённые слова — {len(words)}",
            description=truncate(text, 3900),
            color=COLOR_MUTED,
        )
        embed.set_footer(text="* внутри слова = поиск по подстроке")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @automod.command(name="clear", description="Очистить пул запрещённых слов")
    async def automod_clear(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None

        view = ConfirmView(interaction.user.id, confirm_label="Очистить пул")
        await interaction.response.send_message(
            embed=base_embed(
                title="Очистить пул?",
                description="Все запрещённые слова этого сервера будут удалены.",
                color=COLOR_DANGER,
            ),
            view=view,
            ephemeral=True,
        )
        await view.wait()

        if view.value is not True:
            await interaction.edit_original_response(
                embed=base_embed(title="Отменено", color=COLOR_MUTED),
                view=None,
            )
            return

        count = await self.bot.repos.words.clear(guild.id)
        self._invalidate(guild.id)
        await interaction.edit_original_response(
            embed=success_embed(f"Пул очищен. Удалено слов: **{count}**."),
            view=None,
        )

    @automod.command(name="action", description="Что делать при нарушении")
    @app_commands.choices(action=ACTION_CHOICES)
    async def automod_action(self, interaction: discord.Interaction, action: str) -> None:
        guild = interaction.guild
        assert guild is not None
        if action not in ACTION_LABELS:
            raise UserError("Неизвестное действие.")

        await self.bot.settings.set(guild.id, "automod_action", action)
        await interaction.response.send_message(
            embed=success_embed(f"Действие автомодерации: **{ACTION_LABELS[action]}**."),
            ephemeral=True,
        )

    @automod.command(name="timeout", description="Длительность тайм-аута в минутах")
    @app_commands.describe(minutes="От 1 до 40320 минут (28 дней)")
    async def automod_timeout(self, interaction: discord.Interaction, minutes: int) -> None:
        guild = interaction.guild
        assert guild is not None
        if not 1 <= minutes <= 28 * 24 * 60:
            raise UserError("Минуты должны быть в диапазоне от 1 до 40320.")

        await self.bot.settings.set(guild.id, "automod_timeout", minutes * 60)
        await interaction.response.send_message(
            embed=success_embed(f"Длительность тайм-аута: **{format_duration(minutes * 60)}**."),
            ephemeral=True,
        )

    @automod.command(name="ignore-channel", description="Не проверять сообщения в канале")
    @app_commands.describe(channel="Канал-исключение")
    async def automod_ignore_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        changed = await self._toggle_ignored(interaction, "automod_ignored_channels", channel.id, add=True)
        if not changed:
            raise UserError(f"{channel.mention} уже в исключениях.")
        await interaction.response.send_message(
            embed=success_embed(f"{channel.mention} добавлен в исключения автомодерации."),
            ephemeral=True,
        )

    @automod.command(name="unignore-channel", description="Снова проверять канал")
    @app_commands.describe(channel="Канал, который нужно вернуть в проверку")
    async def automod_unignore_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        changed = await self._toggle_ignored(interaction, "automod_ignored_channels", channel.id, add=False)
        if not changed:
            raise UserError(f"{channel.mention} не был в исключениях.")
        await interaction.response.send_message(
            embed=success_embed(f"{channel.mention} снова проверяется автомодерацией."),
            ephemeral=True,
        )

    @automod.command(name="ignore-role", description="Не проверять участников с этой ролью")
    @app_commands.describe(role="Роль-исключение")
    async def automod_ignore_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        changed = await self._toggle_ignored(interaction, "automod_ignored_roles", role.id, add=True)
        if not changed:
            raise UserError(f"{role.mention} уже в исключениях.")
        await interaction.response.send_message(
            embed=success_embed(f"{role.mention} добавлена в исключения автомодерации."),
            ephemeral=True,
        )

    @automod.command(name="unignore-role", description="Убрать роль из исключений")
    @app_commands.describe(role="Роль, которую нужно вернуть в проверку")
    async def automod_unignore_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        changed = await self._toggle_ignored(interaction, "automod_ignored_roles", role.id, add=False)
        if not changed:
            raise UserError(f"{role.mention} не была в исключениях.")
        await interaction.response.send_message(
            embed=success_embed(f"{role.mention} снова проверяется автомодерацией."),
            ephemeral=True,
        )

    @automod.command(name="test", description="Проверить текст на запрещённые слова")
    @app_commands.describe(text="Текст для проверки")
    async def automod_test(self, interaction: discord.Interaction, text: str) -> None:
        guild = interaction.guild
        assert guild is not None

        matcher = await self._matcher(guild.id)
        hit = matcher.find(text)
        if hit is None:
            await interaction.response.send_message(
                embed=success_embed("Совпадений не найдено — сообщение прошло бы фильтр."),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=error_embed(f"Найдено запрещённое слово: `{hit}`."),
            ephemeral=True,
        )

    @automod.command(name="status", description="Показать настройки автомодерации")
    async def automod_status(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        settings = self.bot.settings

        enabled = await settings.get_bool(guild.id, "automod_enabled", False)
        action = str(await settings.get(guild.id, "automod_action", "delete"))
        timeout_seconds = await settings.get_int(guild.id, "automod_timeout", 600)
        words = await self.bot.repos.words.list(guild.id)
        ignored_channels = await settings.get_list(guild.id, "automod_ignored_channels")
        ignored_roles = await settings.get_list(guild.id, "automod_ignored_roles")

        def format_ids(values: list, template: str) -> str:
            if not values:
                return "—"
            return ", ".join(template.format(int(value)) for value in values[:15])

        embed = base_embed(title="🚫 Автомодерация", color=COLOR_MUTED)
        embed.add_field(name="Статус", value="✅ включена" if enabled else "❌ выключена", inline=True)
        embed.add_field(name="Действие", value=ACTION_LABELS.get(action, action), inline=True)
        embed.add_field(name="Тайм-аут", value=format_duration(timeout_seconds), inline=True)
        embed.add_field(name="Запрещённых слов", value=str(len(words)), inline=True)
        embed.add_field(
            name="Исключённые каналы",
            value=format_ids(ignored_channels, "<#{}>"),
            inline=False,
        )
        embed.add_field(
            name="Исключённые роли",
            value=format_ids(ignored_roles, "<@&{}>"),
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoMod(bot))

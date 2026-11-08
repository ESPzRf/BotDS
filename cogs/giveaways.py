"""Розыгрыши с кнопкой участия и автоматическим подведением итогов."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.embeds import (
    COLOR_MUTED,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    base_embed,
    success_embed,
    warning_embed,
)
from core.errors import UserError
from core.giveaways import pick_winners
from core.repositories import Giveaway, to_iso
from core.timeparse import parse_duration
from core.utils import truncate

log = logging.getLogger(__name__)

MAX_GIVEAWAY_SECONDS = 30 * 24 * 60 * 60


class GiveawayView(discord.ui.View):
    def __init__(self, cog: "Giveaways", giveaway_id: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.giveaway_id = giveaway_id
        button = discord.ui.Button(
            label="Участвовать",
            style=discord.ButtonStyle.success,
            emoji="🎉",
            custom_id=f"giveaway:join:{giveaway_id}",
        )
        button.callback = self._join
        self.add_item(button)

    async def _join(self, interaction: discord.Interaction) -> None:
        await self.cog.join(interaction, self.giveaway_id)


class Giveaways(commands.Cog):
    giveaway = app_commands.Group(name="giveaway", description="Розыгрыши")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        try:
            active = await self.bot.repos.giveaways.all_active()
        except Exception:  # noqa: BLE001
            log.exception("Не удалось восстановить розыгрыши")
            active = []

        for giveaway in active:
            if giveaway.message_id:
                try:
                    self.bot.add_view(GiveawayView(self, giveaway.id))
                except Exception:  # noqa: BLE001
                    log.exception("Не удалось восстановить розыгрыш #%s", giveaway.id)
        if self.bot.is_ready() and not self._finish_loop.is_running():
            self._finish_loop.start()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self._finish_loop.is_running():
            self._finish_loop.start()

    def cog_unload(self) -> None:
        self._finish_loop.cancel()

    # ---------------------------------------------------------------- embeds

    async def _embed(self, giveaway: Giveaway, *, winners: list[int] | None = None) -> discord.Embed:
        entries = await self.bot.repos.giveaways.entry_count(giveaway.id)
        embed = base_embed(
            title=f"🎉 {giveaway.prize}",
            description=(
                "Нажмите кнопку ниже, чтобы участвовать.\n"
                "Повторное нажатие убирает заявку."
            ),
            color=COLOR_PRIMARY,
        )
        embed.add_field(name="Победителей", value=str(giveaway.winners_count), inline=True)
        embed.add_field(name="Участников", value=str(entries), inline=True)
        embed.add_field(name="ID", value=f"#{giveaway.id}", inline=True)
        embed.add_field(
            name="Окончание",
            value=discord.utils.format_dt(giveaway.ends_at, "R"),
            inline=False,
        )
        if giveaway.host_id:
            embed.add_field(name="Организатор", value=f"<@{giveaway.host_id}>", inline=True)
        if giveaway.required_role_id:
            embed.add_field(name="Требование", value=f"<@&{giveaway.required_role_id}>", inline=True)
        if winners is not None:
            value = ", ".join(f"<@{user_id}>" for user_id in winners) if winners else "нет участников"
            embed.add_field(name="🏆 Победители", value=truncate(value, 1024), inline=False)
            embed.color = COLOR_SUCCESS
        embed.set_footer(text=f"Розыгрыш #{giveaway.id} • завершён" if giveaway.ended else f"Розыгрыш #{giveaway.id}")
        return embed

    # ---------------------------------------------------------------- events

    @tasks.loop(seconds=30)
    async def _finish_loop(self) -> None:
        await self.bot.wait_until_ready()
        try:
            due = await self.bot.repos.giveaways.due(to_iso(datetime.now(timezone.utc)))
            for giveaway in due:
                await self._finish(giveaway)
        except Exception:  # noqa: BLE001 - цикл не должен умирать
            log.exception("Не удалось проверить завершённые розыгрыши")

    async def _finish(self, giveaway: Giveaway) -> None:
        guild = self.bot.get_guild(giveaway.guild_id)
        if guild is None:
            return
        channel = guild.get_channel(giveaway.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        if not await self.bot.repos.giveaways.finish(giveaway.id):
            return

        entries = await self.bot.repos.giveaways.entries(giveaway.id)
        winners = pick_winners(entries, giveaway.winners_count)
        try:
            if giveaway.message_id:
                message = await channel.fetch_message(giveaway.message_id)
                ended = await self.bot.repos.giveaways.get(giveaway.id)
                if ended is not None:
                    await message.edit(embed=await self._embed(ended, winners=winners), view=None)
            if winners:
                await channel.send(
                    content="🎉 Победители: " + ", ".join(f"<@{user_id}>" for user_id in winners),
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            else:
                await channel.send("🎉 Розыгрыш завершён без участников.")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            log.debug("Не удалось объявить победителей", exc_info=True)

        for user_id in winners:
            try:
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                await user.send(
                    embed=base_embed(
                        title="🏆 Вы выиграли!",
                        description=f"Розыгрыш **{giveaway.prize}** на сервере **{guild.name}**.",
                        color=COLOR_SUCCESS,
                    )
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        log.info("Розыгрыш #%s завершён, победителей: %s", giveaway.id, len(winners))

    async def join(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(
                embed=warning_embed("Розыгрыши работают только на сервере."),
                ephemeral=True,
            )
            return

        giveaway = await self.bot.repos.giveaways.get(giveaway_id)
        if giveaway is None or giveaway.guild_id != guild.id:
            await interaction.response.send_message(
                embed=warning_embed("Розыгрыш не найден."),
                ephemeral=True,
            )
            return
        if giveaway.ended:
            await interaction.response.send_message(
                embed=warning_embed("Розыгрыш уже завершён."),
                ephemeral=True,
            )
            return
        if giveaway.required_role_id and not any(
            role.id == giveaway.required_role_id for role in member.roles
        ):
            await interaction.response.send_message(
                embed=warning_embed("Для участия нужна роль <@&%d>." % giveaway.required_role_id),
                ephemeral=True,
            )
            return

        if await self.bot.repos.giveaways.has_entry(giveaway.id, member.id):
            await self.bot.repos.giveaways.remove_entry(giveaway.id, member.id)
            text = "Вы больше не участвуете в розыгрыше."
        else:
            await self.bot.repos.giveaways.add_entry(giveaway.id, member.id)
            text = "Вы участвуете в розыгрыше! Удачи 🍀"

        await interaction.response.send_message(embed=success_embed(text), ephemeral=True)

    # --------------------------------------------------------------- commands

    @giveaway.command(name="start", description="Запустить розыгрыш")
    @app_commands.describe(
        prize="Приз",
        duration="Длительность, например 1h, 2d",
        winners="Сколько победителей",
        required_role="Роль, обязательная для участия",
        channel="Канал для розыгрыша",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def giveaway_start(
        self,
        interaction: discord.Interaction,
        prize: str,
        duration: str,
        winners: int = 1,
        required_role: Optional[discord.Role] = None,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        try:
            seconds = parse_duration(duration)
        except ValueError as exc:
            raise UserError(f"Не удалось разобрать длительность: {exc}") from exc
        if not 60 <= seconds <= MAX_GIVEAWAY_SECONDS:
            raise UserError("Длительность розыгрыша — от 1 минуты до 30 дней.")
        if not 1 <= winners <= 20:
            raise UserError("Победителей должно быть от 1 до 20.")

        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            raise UserError("Розыгрыш можно запустить только в текстовом канале.")
        permissions = target.permissions_for(guild.me)
        if not permissions.send_messages or not permissions.embed_links:
            raise UserError(f"У бота нет прав писать embed-сообщения в {target.mention}.")

        await interaction.response.defer(ephemeral=True, thinking=True)

        ends_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        giveaway_id = await self.bot.repos.giveaways.create(
            guild.id,
            target.id,
            prize.strip()[:200],
            winners,
            interaction.user.id,
            required_role.id if required_role else None,
            ends_at,
        )
        giveaway = await self.bot.repos.giveaways.get(giveaway_id)
        if giveaway is None:  # pragma: no cover
            raise UserError("Не удалось создать розыгрыш.")

        view = GiveawayView(self, giveaway_id)
        message = await target.send(
            embed=await self._embed(giveaway),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.bot.add_view(view)
        await self.bot.repos.giveaways.set_message(giveaway_id, message.id)

        await interaction.followup.send(
            embed=success_embed(
                f"Розыгрыш **#{giveaway_id}** запущен в {target.mention}.\n"
                f"Итоги: {discord.utils.format_dt(ends_at, 'R')}."
            ),
            ephemeral=True,
        )

    @giveaway.command(name="end", description="Завершить розыгрыш досрочно")
    @app_commands.describe(giveaway_id="ID розыгрыша")
    @app_commands.default_permissions(manage_guild=True)
    async def giveaway_end(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        giveaway = await self.bot.repos.giveaways.get(giveaway_id)
        if giveaway is None or giveaway.guild_id != guild.id:
            raise UserError(f"Розыгрыш #{giveaway_id} не найден.")
        if giveaway.ended:
            raise UserError("Розыгрыш уже завершён.")

        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._finish(giveaway)
        await interaction.followup.send(
            embed=success_embed(f"Розыгрыш **#{giveaway_id}** завершён."),
            ephemeral=True,
        )

    @giveaway.command(name="reroll", description="Перевыбрать победителей")
    @app_commands.describe(giveaway_id="ID завершённого розыгрыша")
    @app_commands.default_permissions(manage_guild=True)
    async def giveaway_reroll(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        giveaway = await self.bot.repos.giveaways.get(giveaway_id)
        if giveaway is None or giveaway.guild_id != guild.id:
            raise UserError(f"Розыгрыш #{giveaway_id} не найден.")
        if not giveaway.ended:
            raise UserError("Сначала завершите розыгрыш.")

        entries = await self.bot.repos.giveaways.entries(giveaway.id)
        winners = pick_winners(entries, giveaway.winners_count)
        if not winners:
            raise UserError("В розыгрыше нет участников.")

        await interaction.response.send_message(
            embed=success_embed(
                "Новые победители: " + ", ".join(f"<@{user_id}>" for user_id in winners)
            ),
            ephemeral=True,
        )

    @giveaway.command(name="list", description="Активные розыгрыши")
    @app_commands.default_permissions(manage_guild=True)
    async def giveaway_list(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        active = await self.bot.repos.giveaways.active(guild.id)
        if not active:
            await interaction.response.send_message(
                embed=base_embed(
                    title="🎉 Розыгрыши",
                    description="Активных розыгрышей нет.",
                    color=COLOR_MUTED,
                ),
                ephemeral=True,
            )
            return

        embed = base_embed(title="🎉 Активные розыгрыши", color=COLOR_MUTED)
        for giveaway in active[:15]:
            entries = await self.bot.repos.giveaways.entry_count(giveaway.id)
            embed.add_field(
                name=f"#{giveaway.id} · {giveaway.prize}",
                value=(
                    f"<#{giveaway.channel_id}>\n"
                    f"Участников: **{entries}** • До: {discord.utils.format_dt(giveaway.ends_at, 'R')}"
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Giveaways(bot))

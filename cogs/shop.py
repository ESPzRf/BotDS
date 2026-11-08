"""Магазин за баллы лояльности."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.discord_utils import role_assign_problem
from core.embeds import (
    COLOR_MUTED,
    COLOR_PRIMARY,
    base_embed,
    success_embed,
)
from core.errors import UserError
from core.utils import format_number, truncate

log = logging.getLogger(__name__)


class Shop(commands.Cog):
    shop = app_commands.Group(name="shop", description="Магазин за баллы лояльности")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._buy_lock = asyncio.Lock()

    # --------------------------------------------------------------- commands

    @shop.command(name="list", description="Показать товары магазина")
    async def shop_list(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")

        items = await self.bot.repos.shop.list(guild.id)
        embed = base_embed(title="🛒 Магазин лояльности", color=COLOR_PRIMARY)
        if not items:
            embed.description = "Магазин пуст. Администратор может добавить товары: `/shop add`."
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        for item in items[:20]:
            stock = "∞" if item.stock is None else str(item.stock)
            embed.add_field(
                name=f"#{item.id} · {item.name} — {format_number(item.price)}",
                value=(
                    f"<@&{item.role_id}> · остаток: **{stock}**"
                    + (f"\n{truncate(item.description, 200)}" if item.description else "")
                ),
                inline=False,
            )
        embed.set_footer(text="Купить: /shop buy item_id")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @shop.command(name="add", description="Добавить товар в магазин")
    @app_commands.describe(
        role="Роль, которая выдаётся при покупке",
        name="Название товара",
        price="Цена в баллах лояльности",
        description="Описание",
        stock="Количество (пусто — без ограничений)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def shop_add(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        name: str,
        price: int,
        description: Optional[str] = None,
        stock: Optional[int] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        if not 1 <= price <= 1_000_000_000:
            raise UserError("Цена должна быть от 1 до 1 000 000 000.")
        if stock is not None and not 0 <= stock <= 1_000_000:
            raise UserError("Остаток должен быть от 0 до 1 000 000.")

        problem = role_assign_problem(guild, role)
        if problem:
            raise UserError(f"Нельзя использовать эту роль: {problem}.")

        item_id = await self.bot.repos.shop.add(
            guild.id,
            role.id,
            name.strip()[:100],
            price,
            (description or "").strip()[:500],
            stock,
        )
        await interaction.response.send_message(
            embed=success_embed(
                f"Товар **#{item_id}** «{name.strip()[:100]}» добавлен за **{format_number(price)}** лояльности."
            ),
            ephemeral=True,
        )

    @shop.command(name="remove", description="Удалить товар из магазина")
    @app_commands.describe(item_id="ID товара из /shop list")
    @app_commands.default_permissions(manage_guild=True)
    async def shop_remove(self, interaction: discord.Interaction, item_id: int) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError("Команда доступна только на сервере.")
        removed = await self.bot.repos.shop.remove(guild.id, item_id)
        if not removed:
            raise UserError(f"Товар #{item_id} не найден.")
        await interaction.response.send_message(
            embed=success_embed(f"Товар **#{item_id}** удалён."),
            ephemeral=True,
        )

    @app_commands.command(name="buy", description="Купить товар за баллы лояльности")
    @app_commands.describe(item_id="ID товара из /shop list")
    async def buy(self, interaction: discord.Interaction, item_id: int) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            raise UserError("Команда доступна только на сервере.")

        item = await self.bot.repos.shop.get(item_id)
        if item is None or item.guild_id != guild.id:
            raise UserError(f"Товар #{item_id} не найден.")
        if item.stock is not None and item.stock <= 0:
            raise UserError("Товар закончился.")
        if item.role_id == guild.default_role.id:
            raise UserError("Этот товар нельзя купить.")

        role = guild.get_role(item.role_id)
        if role is None:
            raise UserError("Роль товара удалена — сообщите администратору.")
        problem = role_assign_problem(guild, role)
        if problem:
            raise UserError(f"Бот не может выдать эту роль: {problem}.")
        if role in member.roles:
            raise UserError("У вас уже есть эта роль.")

        await interaction.response.defer(ephemeral=True, thinking=True)

        async with self._buy_lock:
            await self.bot.repos.members.ensure(guild.id, member.id)
            spent = await self.bot.repos.members.try_spend_loyalty(
                guild.id, member.id, item.price
            )
            if not spent:
                stats = await self.bot.repos.members.ensure(guild.id, member.id)
                raise UserError(
                    f"Недостаточно лояльности: нужно **{format_number(item.price)}**, "
                    f"у вас **{format_number(stats.loyalty)}**."
                )

            reserved = False
            if item.stock is not None:
                reserved = await self.bot.repos.shop.decrement_stock(item.id)
                if not reserved:
                    await self.bot.repos.members.add_loyalty(guild.id, member.id, item.price)
                    raise UserError("Товар закончился — баллы не списаны.")

            try:
                await member.add_roles(role, reason=f"Покупка в магазине #{item.id}")
            except (discord.Forbidden, discord.HTTPException) as exc:
                await self.bot.repos.members.add_loyalty(guild.id, member.id, item.price)
                if reserved:
                    await self.bot.repos.shop.increment_stock(item.id)
                raise UserError(f"Не удалось выдать роль, баллы возвращены: {exc}") from exc

            try:
                await self.bot.repos.shop.record_purchase(guild.id, member.id, item.id, item.price)
            except Exception:  # noqa: BLE001 - покупка уже совершена
                log.exception("Не удалось записать покупку товара #%s", item.id)

        stats = await self.bot.repos.members.ensure(guild.id, member.id)
        embed = success_embed(
            f"Вы купили **{item.name}** за **{format_number(item.price)}** лояльности.\n"
            f"Роль {role.mention} выдана. Остаток: **{format_number(stats.loyalty)}**."
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="balance", description="Показать баланс лояльности")
    async def balance(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            raise UserError("Команда доступна только на сервере.")

        stats = await self.bot.repos.members.ensure(guild.id, member.id)
        await interaction.response.send_message(
            embed=base_embed(
                title="💎 Баланс лояльности",
                description=f"У вас **{format_number(stats.loyalty)}** баллов.",
                color=COLOR_MUTED,
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Shop(bot))

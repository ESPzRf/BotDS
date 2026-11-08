"""Точка входа: Discord-бот для модерации сервера."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

try:
    import discord
    from discord import app_commands
    from discord.ext import commands
except ModuleNotFoundError as exc:  # pragma: no cover - зависит от окружения
    print(
        "\n[!] Не установлен discord.py.\n"
        "    Выполните: python -m pip install -r requirements.txt\n"
    )
    raise SystemExit(1) from exc

from config import PROJECT_ROOT, Config, ConfigError
from core.command_access import REASON_MESSAGES, command_path_from_data, evaluate_access
from core.database import Database
from core.embeds import error_embed
from core.errors import UserError
from core.i18n import I18n
from core.instance_lock import AlreadyRunningError, InstanceLock
from core.logging_setup import setup_logging
from core.repositories import Repositories, build_repositories
from core.settings import GuildSettingsStore
from core.warning_service import WarningService

log = logging.getLogger(__name__)

__version__ = "2.1.0"

EXTENSIONS: tuple[str, ...] = (
    "cogs.logs",
    "cogs.autorole",
    "cogs.selfroles",
    "cogs.automod",
    "cogs.warnings",
    "cogs.levels",
    "cogs.tickets",
    "cogs.tempactions",
    "cogs.security",
    "cogs.captcha",
    "cogs.appeals",
    "cogs.stats",
    "cogs.giveaways",
    "cogs.shop",
    "cogs.access",
    "cogs.jobs",
    "cogs.settings",
    "cogs.utility",
)

PERMISSIONS = discord.Permissions(
    view_channel=True,
    send_messages=True,
    send_messages_in_threads=True,
    embed_links=True,
    read_message_history=True,
    manage_messages=True,
    manage_roles=True,
    manage_channels=True,
    kick_members=True,
    ban_members=True,
    moderate_members=True,
    view_audit_log=True,
    attach_files=True,
    add_reactions=True,
)


class ModerationBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
        )

        self.config = config
        self.started_at = datetime.now(timezone.utc)
        self._ready_logged = False

        self.db = Database(config.database_url or config.db_path)
        self.settings = GuildSettingsStore(self.db)
        self.repos: Repositories = build_repositories(self.db)
        self.i18n = I18n(PROJECT_ROOT / "locales")
        self.warning_service = WarningService(self)
        self._access_cache: dict[int, tuple[float, dict]] = {}

    # ---------------------------------------------------------------- access

    def invalidate_access(self, guild_id: int | None = None) -> None:
        if guild_id is None:
            self._access_cache.clear()
        else:
            self._access_cache.pop(guild_id, None)

    async def access_rules(self, guild_id: int, *, force: bool = False) -> dict:
        cached = self._access_cache.get(guild_id)
        if not force and cached is not None and time.monotonic() - cached[0] < 15:
            return cached[1]
        rules = await self.repos.access.rules(guild_id)
        self._access_cache[guild_id] = (time.monotonic(), rules)
        return rules

    # ------------------------------------------------------------------ setup

    async def setup_hook(self) -> None:
        await self.db.connect()
        await self.repos.access.migrate_legacy()
        await self.repos.jobs.requeue_running()

        for extension in EXTENSIONS:
            await self.load_extension(extension)
            log.debug("Загружен ког: %s", extension)

        self.tree.on_error = self.on_tree_error
        self.tree.interaction_check = self.interaction_check

        await self._sync_commands()

    async def _sync_commands(self) -> None:
        try:
            if self.config.guild_id:
                guild = discord.Object(id=self.config.guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info("Команды синхронизированы для сервера %s: %d", self.config.guild_id, len(synced))
            else:
                synced = await self.tree.sync()
                log.info("Команды синхронизированы глобально: %d", len(synced))
        except discord.HTTPException:
            log.exception("Не удалось синхронизировать слэш-команды")

    async def on_ready(self) -> None:
        if self._ready_logged:
            return
        self._ready_logged = True

        assert self.user is not None
        log.info("Бот запущен: %s (ID: %s)", self.user, self.user.id)
        log.info(
            "Серверов: %d | Участников: %d",
            len(self.guilds),
            sum(guild.member_count or 0 for guild in self.guilds),
        )

        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="/help"),
            status=discord.Status.online,
        )

        # Прогреваем кэш ограничений, чтобы interaction_check не ходил в БД
        # на каждую команду и не рисковал 3-секундным дедлайном Discord.
        if self.guilds:
            await asyncio.gather(
                *(self.access_rules(guild.id, force=True) for guild in self.guilds),
                return_exceptions=True,
            )

        invite = discord.utils.oauth_url(
            self.user.id,
            permissions=PERMISSIONS,
            scopes=("bot", "applications.commands"),
        )
        log.info("Ссылка для приглашения бота: %s", invite)

    # ----------------------------------------------------------------- checks

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Команды бота работают только на серверах."),
                ephemeral=True,
            )
            return False

        if interaction.command is None:
            command_path = command_path_from_data(interaction.data)
            if not command_path:
                return True
        else:
            command_path = interaction.command.qualified_name

        try:
            rules = await asyncio.wait_for(self.access_rules(interaction.guild.id), timeout=2.0)
        except asyncio.TimeoutError:
            log.warning("Проверка доступа к команде заняла больше 2 секунд")
            await interaction.response.send_message(
                embed=error_embed("Проверка доступа заняла слишком много времени. Попробуйте ещё раз."),
                ephemeral=True,
            )
            return False
        except Exception:  # noqa: BLE001
            log.exception("Не удалось проверить доступ к команде")
            await interaction.response.send_message(
                embed=error_embed("Не удалось проверить доступ к команде. Попробуйте позже."),
                ephemeral=True,
            )
            return False

        allowed, reason = evaluate_access(
            rules,
            command_path,
            user_id=interaction.user.id,
            role_ids=(role.id for role in interaction.user.roles),
            is_admin=interaction.user.guild_permissions.administrator,
            is_owner=interaction.guild.owner_id == interaction.user.id,
        )
        if not allowed:
            message = REASON_MESSAGES.get(reason or "", "У вас нет доступа к этой команде.")
            await interaction.response.send_message(
                embed=error_embed(message),
                ephemeral=True,
            )
            return False

        return True

    async def on_tree_error(self, interaction: discord.Interaction, error: Exception) -> None:
        if isinstance(error, app_commands.CommandInvokeError):
            error = error.original

        if isinstance(error, UserError):
            message = error.message
        elif isinstance(error, app_commands.MissingPermissions):
            message = "Недостаточно прав для этой команды."
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = f"Слишком часто. Попробуйте через {error.retry_after:.1f} сек."
        elif isinstance(error, app_commands.CheckFailure):
            message = "У вас нет доступа к этой команде."
        else:
            command_name = interaction.command.qualified_name if interaction.command else "?"
            reference = f"{interaction.id:x}"[-6:]
            log.error(
                "Ошибка в команде /%s (код %s)",
                command_name,
                reference,
                exc_info=(type(error), error, error.__traceback__),
            )
            message = f"Внутренняя ошибка. Код для обращения: `{reference}`."

        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=error_embed(message), ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed(message), ephemeral=True)
        except discord.HTTPException:
            log.debug("Не удалось отправить сообщение об ошибке", exc_info=True)

    # ---------------------------------------------------------------- shutdown

    async def close(self) -> None:
        try:
            await super().close()
        finally:
            await self.db.close()

    async def run_bot(self) -> None:
        async with self:
            await self.start(self.config.token)


def main() -> int:
    try:
        config = Config.load()
    except ConfigError as exc:
        print(f"\n[!] Ошибка конфигурации:\n{exc}\n")
        return 1

    setup_logging(config.log_level, config.log_file)
    log.info("Запуск бота (версия %s)", __version__)
    if config.database_url:
        log.info("База данных: PostgreSQL")
    else:
        log.info("База данных: SQLite (%s)", config.db_path)

    lock_path = (
        PROJECT_ROOT / "data" / "bot.lock"
        if config.database_url
        else config.db_path.parent / "bot.lock"
    )
    lock = InstanceLock(lock_path)
    try:
        lock.acquire()
    except AlreadyRunningError as exc:
        log.critical("%s", exc)
        return 1
    log.info("PID %s, lock-файл: %s", os.getpid(), lock_path)

    try:
        bot = ModerationBot(config)
        try:
            asyncio.run(bot.run_bot())
        except discord.LoginFailure:
            log.critical("Неверный токен бота. Проверьте DISCORD_TOKEN в .env")
            return 1
        except discord.PrivilegedIntentsRequired as exc:
            log.critical(
                "Не включены привилегированные интенты (%s).\n"
                "Включите SERVER MEMBERS INTENT и MESSAGE CONTENT INTENT "
                "в Discord Developer Portal → Bot.",
                exc,
            )
            return 1
        except KeyboardInterrupt:
            log.info("Бот остановлен")
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())

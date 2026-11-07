"""Конфигурация отдельного веб-дашборда (не входит в процесс бота)."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from config import PROJECT_ROOT
from core.env import load_dotenv

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DashboardConfig:
    bot_token: str
    client_id: str
    client_secret: str
    secret: str
    base_url: str
    host: str
    port: int
    db_target: str
    dev_mode: bool
    dev_guild_id: int | None
    dev_user_id: int | None
    ssl_cert: str | None = None
    ssl_key: str | None = None

    @classmethod
    def load(cls, base_dir: Path | None = None) -> "DashboardConfig":
        base = base_dir or PROJECT_ROOT
        load_dotenv(base / ".env")

        def env(name: str, default: str = "") -> str:
            return (os.getenv(name) or default).strip()

        def optional_int(name: str) -> int | None:
            value = env(name)
            if not value:
                return None
            try:
                return int(value)
            except ValueError:
                log.warning("%s должен быть числом, получено %r", name, value)
                return None

        def optional_path(name: str) -> str | None:
            value = env(name)
            if not value:
                return None
            path = Path(value)
            return str(path if path.is_absolute() else base / path)

        bot_token = env("DISCORD_TOKEN")
        if not bot_token or bot_token == "PASTE_TOKEN_HERE":
            raise RuntimeError(
                "DISCORD_TOKEN не задан. Веб-панель использует токен бота для "
                "чтения каналов и ролей сервера."
            )

        host = env("DASHBOARD_HOST", "127.0.0.1")
        port = optional_int("DASHBOARD_PORT") or 8080
        base_url = env("DASHBOARD_BASE_URL", f"http://{host}:{port}").rstrip("/")

        database_url = env("DATABASE_URL")
        db_raw = env("DB_PATH", "data/bot.db")
        if database_url:
            db_target = database_url
        elif db_raw == ":memory:":
            db_target = ":memory:"
        else:
            path = Path(db_raw)
            db_target = str(path if path.is_absolute() else base / path)

        secret = env("DASHBOARD_SECRET")
        if not secret:
            secret = secrets.token_urlsafe(32)
            log.warning(
                "DASHBOARD_SECRET не задан — сессии будут сбрасываться после перезапуска. "
                "Добавьте длинную случайную строку в .env."
            )

        dev_mode = env("DASHBOARD_DEV_MODE").lower() in {"1", "true", "yes", "on"}
        dev_guild_id = optional_int("DASHBOARD_DEV_GUILD_ID") or optional_int("GUILD_ID")
        dev_user_id = optional_int("DASHBOARD_DEV_USER_ID") or 0
        if dev_mode and not dev_guild_id:
            raise RuntimeError(
                "Для DASHBOARD_DEV_MODE=1 задайте DASHBOARD_DEV_GUILD_ID (ID сервера)."
            )

        ssl_cert = optional_path("DASHBOARD_SSL_CERT")
        ssl_key = optional_path("DASHBOARD_SSL_KEY")
        if bool(ssl_cert) != bool(ssl_key):
            raise RuntimeError("Задайте оба файла: DASHBOARD_SSL_CERT и DASHBOARD_SSL_KEY.")

        return cls(
            bot_token=bot_token,
            client_id=env("DISCORD_CLIENT_ID"),
            client_secret=env("DISCORD_CLIENT_SECRET"),
            secret=secret,
            base_url=base_url,
            host=host,
            port=port,
            db_target=db_target,
            ssl_cert=ssl_cert,
            ssl_key=ssl_key,
            dev_mode=dev_mode,
            dev_guild_id=dev_guild_id,
            dev_user_id=dev_user_id,
        )

    @property
    def oauth_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def redirect_uri(self) -> str:
        return f"{self.base_url}/auth/callback"

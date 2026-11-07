"""Загрузка и валидация конфигурации из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from core.env import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent


class ConfigError(Exception):
    """Ошибка конфигурации, которую нужно показать пользователю."""


@dataclass(frozen=True, slots=True)
class Config:
    """Настройки, которые не меняются во время работы бота."""

    token: str
    guild_id: int | None
    db_path: Path
    database_url: str | None
    log_level: str
    log_file: Path | None

    @classmethod
    def load(cls, base_dir: Path | None = None) -> "Config":
        base = base_dir or PROJECT_ROOT
        load_dotenv(base / ".env")

        token = (os.getenv("DISCORD_TOKEN") or "").strip()
        if not token or token == "PASTE_TOKEN_HERE" or token.lower().startswith("your_"):
            raise ConfigError(
                "Не задан DISCORD_TOKEN.\n"
                "Скопируйте .env.example в .env и вставьте токен бота "
                "(Discord Developer Portal → ваше приложение → Bot → Reset Token)."
            )

        guild_id: int | None = None
        guild_raw = (os.getenv("GUILD_ID") or "").strip()
        if guild_raw:
            try:
                guild_id = int(guild_raw)
            except ValueError as exc:
                raise ConfigError("GUILD_ID должен быть числом — ID сервера Discord.") from exc

        db_raw = (os.getenv("DB_PATH") or "data/bot.db").strip()
        db_path = Path(db_raw)
        if not db_path.is_absolute():
            db_path = base / db_path

        database_url = (os.getenv("DATABASE_URL") or "").strip() or None
        if database_url and not database_url.startswith(("postgres://", "postgresql://")):
            raise ConfigError(
                "DATABASE_URL поддерживает только PostgreSQL "
                "(postgresql://user:password@host:5432/dbname).\n"
                "Для SQLite используйте DB_PATH."
            )

        log_level = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            log_level = "INFO"

        log_file: Path | None = None
        file_raw = (os.getenv("LOG_FILE") or "").strip()
        if file_raw and file_raw.lower() not in {"none", "off", "0"}:
            log_file = Path(file_raw)
            if not log_file.is_absolute():
                log_file = base / log_file

        return cls(
            token=token,
            guild_id=guild_id,
            db_path=db_path,
            database_url=database_url,
            log_level=log_level,
            log_file=log_file,
        )

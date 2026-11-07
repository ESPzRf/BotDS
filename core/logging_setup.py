"""Настройка логирования (консоль + необязательный файл)."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Чтобы повторный вызов (например, в тестах) не дублировал сообщения.
    for handler in list(root.handlers):
        if getattr(handler, "_dsh_bot_handler", False):
            root.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(FORMAT, DATE_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console._dsh_bot_handler = True  # type: ignore[attr-defined]
    root.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler._dsh_bot_handler = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)

    # discord.py очень болтлив на DEBUG, оставляем только предупреждения.
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)

"""Запуск веб-дашборда как отдельного сервиса.

    python dashboard.py

Бот при этом может быть запущен отдельно: оба процесса используют одну БД.
"""

from __future__ import annotations

import logging

import uvicorn

from core.logging_setup import setup_logging
from dashboard.app import create_app
from dashboard.config import DashboardConfig

log = logging.getLogger(__name__)


def main() -> int:
    try:
        config = DashboardConfig.load()
    except Exception as exc:  # noqa: BLE001
        print(f"\n[!] Ошибка конфигурации панели:\n{exc}\n")
        return 1

    setup_logging("INFO", None)
    log.info("Веб-панель: %s", config.base_url)
    if not config.oauth_configured:
        log.warning("DISCORD_CLIENT_ID/SECRET не заданы — вход только через dev-режим.")
    if config.dev_mode:
        log.warning("DASHBOARD_DEV_MODE включён: вход без Discord OAuth. Не используйте в продакшене.")

    app = create_app(config)
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
        proxy_headers=True,
        ssl_certfile=config.ssl_cert,
        ssl_keyfile=config.ssl_key,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

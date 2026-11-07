#!/usr/bin/env bash

set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "[1/3] Создаю виртуальное окружение..."
    python3 -m venv .venv
fi

echo "[2/3] Устанавливаю зависимости..."
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null 2>&1 || true
python -m pip install -r requirements.txt

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo
    echo "[!] Создан файл .env"
    echo "    Вставьте токен бота в DISCORD_TOKEN и запустите ./start.sh снова."
    echo
    exit 1
fi

echo "[3/3] Запуск бота..."
exec python bot.py

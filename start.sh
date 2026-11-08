#!/usr/bin/env bash

set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-bot}"

usage() {
    cat <<'EOF'
Использование:
  ./start.sh              запустить бота (по умолчанию)
  ./start.sh bot          запустить только бота
  ./start.sh dashboard    запустить только веб-панель
  ./start.sh all          запустить бота и панель вместе
  ./start.sh --help       эта справка

Панель использует те же .env и базу данных, что и бот.
Остановка: Ctrl+C.
EOF
}

case "$MODE" in
    bot|dashboard|all) ;;
    -h|--help|help) usage; exit 0 ;;
    *)
        echo "[!] Неизвестный режим: $MODE"
        usage
        exit 2
        ;;
esac

if ! command -v python3 >/dev/null 2>&1; then
    echo "[!] Не найден python3. Установите Python 3.11+."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "[1/3] Создаю виртуальное окружение..."
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "[2/3] Устанавливаю зависимости..."
python -m pip install --upgrade pip >/dev/null 2>&1 || true

case "$MODE" in
    bot)
        python -m pip install -r requirements.txt
        ;;
    dashboard)
        python -m pip install -r requirements-dashboard.txt
        ;;
    all)
        python -m pip install -r requirements.txt -r requirements-dashboard.txt
        ;;
esac

if [ ! -f ".env" ]; then
    cp .env.example .env
    chmod 600 .env 2>/dev/null || true
    echo
    echo "[!] Создан файл .env"
    echo "    Вставьте DISCORD_TOKEN и настройки панели, затем запустите ./start.sh снова."
    echo
    exit 1
fi
chmod 600 .env 2>/dev/null || true

if grep -Eq '^DISCORD_TOKEN=(PASTE_TOKEN_HERE)?[[:space:]]*$' .env; then
    echo
    echo "[!] В .env не заполнен DISCORD_TOKEN."
    echo "    Вставьте токен бота из Discord Developer Portal → Bot."
    echo
    exit 1
fi

# Для PostgreSQL нужен asyncpg.
if grep -Eq '^DATABASE_URL=[^[:space:]]+' .env; then
    echo "    Обнаружен PostgreSQL — устанавливаю asyncpg..."
    python -m pip install -r requirements-postgres.txt
fi

if [ "$MODE" = "dashboard" ] || [ "$MODE" = "all" ]; then
    if ! grep -Eq '^DISCORD_CLIENT_ID=[^[:space:]]+' .env; then
        echo
        echo "[!] OAuth не настроен: задайте DISCORD_CLIENT_ID и DISCORD_CLIENT_SECRET."
        echo "    Без OAuth панель откроется, но войти можно только через dev-режим."
    fi
    BASE_URL="$(grep -E '^DASHBOARD_BASE_URL=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
    if [ -n "${BASE_URL:-}" ]; then
        echo "    Панель: $BASE_URL"
    fi
    BIND_HOST="$(grep -E '^DASHBOARD_HOST=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
    if [ "${BIND_HOST:-127.0.0.1}" = "127.0.0.1" ]; then
        echo "[!] DASHBOARD_HOST=127.0.0.1: панель будет доступна только на самом сервере."
        echo "    Для доступа по IP укажите DASHBOARD_HOST=0.0.0.0."
    fi
    if ! grep -Eq '^DASHBOARD_SECRET=[^[:space:]]+' .env; then
        echo "[!] DASHBOARD_SECRET не задан — сессии будут сбрасываться после перезапуска."
    fi
fi

echo "[3/3] Запуск: $MODE"

run_bot() {
    exec python bot.py
}

run_dashboard() {
    exec python dashboard.py
}

run_all() {
    python bot.py &
    BOT_PID=$!

    python dashboard.py &
    DASHBOARD_PID=$!

    cleanup() {
        kill "$BOT_PID" "$DASHBOARD_PID" 2>/dev/null || true
        wait "$BOT_PID" "$DASHBOARD_PID" 2>/dev/null || true
    }
    trap cleanup INT TERM EXIT

    # wait -n появился в Bash 4.3; на старом macOS Bash 3.2 ждём бота.
    if help wait 2>/dev/null | grep -q -- '-n'; then
        if wait -n "$BOT_PID" "$DASHBOARD_PID"; then
            STATUS=0
        else
            STATUS=$?
        fi
    else
        if wait "$BOT_PID"; then
            STATUS=0
        else
            STATUS=$?
        fi
    fi
    cleanup
    exit "$STATUS"
}

case "$MODE" in
    bot) run_bot ;;
    dashboard) run_dashboard ;;
    all) run_all ;;
esac

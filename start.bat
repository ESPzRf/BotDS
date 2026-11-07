@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Создаю виртуальное окружение...
    python -m venv .venv
    if errorlevel 1 goto :error
)

echo [2/3] Устанавливаю зависимости...
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo [!] Создан файл .env
    echo     Вставьте токен бота в DISCORD_TOKEN и запустите start.bat снова.
    echo.
    notepad ".env"
    pause
    exit /b 1
)

echo [3/3] Запуск бота...
python bot.py
goto :eof

:error
echo.
echo [X] Не удалось установить зависимости.
echo     Проверьте подключение к интернету и версию Python ^(нужен 3.10+^).
pause
exit /b 1

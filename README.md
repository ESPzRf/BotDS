# Discord Moderation Bot

Бот для модерации Discord-сервера на **Python + discord.py**.
Данные хранятся в **SQLite** (по умолчанию) или **PostgreSQL** — переключается одной
переменной окружения. Внешние платные API не нужны; для веб-панели используется Discord OAuth.

Версия **2.1**: добавлен отдельный веб-дашборд; к базовым модулям — тикеты, временные
наказания, антирейд, антиспам, капча, апелляции, статистика в каналах, розыгрыши и
магазин лояльности.

## Возможности

| Модуль | Что делает |
|---|---|
| 🧾 **Логирование** | Сообщения, участники, роли, каналы, войс, инвайты, баны/кики с аудит-логом. Отдельный канал модерации и переключатели групп. |
| 🖥️ **Веб-панель** | Отдельный сервис: автороли, логи, автомодерация, уровни, безопасность, тикеты, статистика и доступ к командам. |
| 🎭 **Автовыдача ролей** | Новым людям и/или ботам, плюс выдача текущим участникам. |
| 🎛️ **Роли по кнопкам** | Embed-панели и мастер выбора ролей, до 25 кнопок, toggle-логика, эксклюзивные группы, сохранение после рестарта. |
| 🚫 **Пул запрещённых слов** | Нормализация (регистр, homoglyphs, zero-width, leet), границы слов, `*`, исключения, действия delete/warn/timeout. |
| ⚠️ **Предупреждения** | История в БД, снятие, эскалация N → timeout/kick/ban, DM и лог. |
| 📈 **Уровни и лояльность** | XP, уровни, роли-награды, `/rank`, `/leaderboard`, `/daily`, статусы, ручная выдача XP/лояльности и множители (события, роли, бустеры). |
| 🎫 **Тикеты** | Панель с кнопкой, приватные каналы, взятие в работу, закрытие с транскриптом. |
| ⏰ **Временные наказания** | `/tempactions ban` и `mute` с автоснятием, список и досрочная отмена. |
| 🚨 **Антирейд** | Всплеск входов, режим рейда, проверка возраста аккаунта, kick/ban/timeout. |
| 🔁 **Антиспам** | Порог сообщений за окно, удаление и тайм-аут, исключения каналов. |
| 🤖 **Капча** | Код в ЛС, модальное окно, роль «проверен», кик по таймауту. |
| ⚖️ **Апелляции** | Участник подаёт заявку, поддержка одобряет/отклоняет кнопками. |
| 📊 **Статистика** | Названия голосовых каналов: участники, люди, боты, бусты. |
| 🎉 **Розыгрыши** | Кнопка участия, автоподведение итогов, перевыбор победителей. |
| 🛒 **Магазин** | Товары-роли за баллы лояльности, остатки, покупки. |
| 🔐 **Доступ к командам** | Белые/чёрные списки ролей и ID пользователей для любой команды или целой группы; полное отключение команд. |

## Требования

- Python **3.10+** (проверено на 3.13);
- `discord.py >= 2.5`;
- SQLite (встроен) **или** PostgreSQL 13+ и `asyncpg` (только для PG);
- права администратора на сервере.

## Быстрый старт

### Windows

```bat
start.bat
```

### Linux / macOS

```bash
chmod +x start.sh
./start.sh              # только бот
./start.sh dashboard    # только веб-панель
./start.sh all          # бот и панель вместе
```

Скрипт сам создаёт `.venv`, ставит нужные зависимости (`requirements.txt` и/или
`requirements-dashboard.txt`), проверяет `.env` и предупреждает, если панель
слушает только `127.0.0.1`.

Вручную:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

### Discord Developer Portal

1. [Developer Portal](https://discord.com/developers/applications) → **New Application**.
2. **Bot** → **Reset Token** → скопируйте токен.
3. Включите **Privileged Gateway Intents**:
   - ✅ **Server Members Intent** — участники, роли, антирейд, капча;
   - ✅ **Message Content Intent** — фильтр слов, антиспам, опыт.
4. **OAuth2 → General** → скопируйте **Client ID**.

### `.env`

```dotenv
DISCORD_TOKEN=ваш_токен
GUILD_ID=123456789012345678
DB_PATH=data/bot.db
# DATABASE_URL=postgresql://modbot:password@localhost:5432/modbot
```

`GUILD_ID` необязателен, но с ним слэш-команды появляются сразу.

### Приглашение

Нужны права: `View Channels`, `Send Messages`, `Send Messages in Threads`, `Embed Links`,
`Read Message History`, `Manage Messages`, `Manage Roles`, **`Manage Channels`**,
`Kick Members`, `Ban Members`, `Moderate Members`, `View Audit Log`, `Attach Files`,
`Add Reactions`.

Ссылка (замените `YOUR_CLIENT_ID`):

```
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=1116960058582&scope=bot%20applications.commands
```

При первом запуске бот печатает персональную ссылку в консоль.

### Запуск

```bash
python bot.py
```

### Первичная настройка

```
/logs channel #логи
/logs modlog #модерация
/autorole add role:@Участник target:Людям
/automod add word:запрещённое
/automod enable
/selfrole build channel:#роли title:Роли
/levelconfig reward level:10 role:@Активный
/ticket setup channel:#поддержка title:Поддержка support_role:@Support
```

## Команды

Команды остались с прежними именами: `/warn`, `/warnings`, `/rank`, `/buy`, `/appeal`,
`/help` и остальные. Для ограничений не нужно выдавать доступ по одной команде — есть
готовые списки.

### Списки команд

| Список | Для кого | Что входит |
|---|---|---|
| `player` | игроки | `/rank`, `/leaderboard`, `/daily`, `/buy`, `/balance`, `/shop list`, `/appeal`, `/help`, `/ping`, `/botinfo` |
| `moderator` | модераторы | предупреждения, временные наказания, закрытие тикетов, жалобы |
| `admin` | администрация | логи, автороли, selfrole, автомодерация, конфиги уровней/предупреждений, статистика, розыгрыши, магазин, настройки |

Список используется вместо имени команды:

```
/access allow-role command:moderator role:@Модератор
/access remove-role command:moderator role:@Модератор
/access presets
/access list command:moderator
```

Можно ограничивать и отдельную команду: `/access allow-role command:warn role:@Модератор`.
Правила списка применяются ко всем его командам сразу, но не отменяют базовые права
Discord: например, для команд модерации у роли должно быть право `Moderate Members`.

### Логи

| Команда | Описание |
|---|---|
| `/logs channel [#канал]` | Канал логов; без параметра — выключить |
| `/logs modlog [#канал]` | Канал модерации |
| `/logs toggle <группа>` | Вкл/выкл группу событий |
| `/logs status` | Настройки |

### Автороли

| Команда | Описание |
|---|---|
| `/autorole add role [target]` | Добавить роль (людям/ботам) |
| `/autorole remove role` | Убрать |
| `/autorole list` | Список |
| `/autorole apply` | Выдать текущим участникам |
| `/autorole enabled state` | Вкл/выкл |

### Роли по кнопкам

| Команда | Описание |
|---|---|
| `/selfrole build channel title [description] [color] [exclusive]` | Мастер: выбрать роли и собрать панель |
| `/selfrole create channel title [description] [color]` | Создать пустую панель |
| `/selfrole add panel_id role [label] [emoji] [style]` | Добавить кнопку |
| `/selfrole group panel_id role name` | Эксклюзивная группа: выбор одной роли снимает другие |
| `/selfrole ungroup panel_id role` | Убрать роль из группы |
| `/selfrole groups panel_id` | Показать группы панели |
| `/selfrole remove panel_id role` | Убрать кнопку |
| `/selfrole edit panel_id [title] [description] [color]` | Изменить панель |
| `/selfrole delete panel_id` | Удалить панель |
| `/selfrole list` | Список панелей |
| `/selfrole refresh` | Перерисовать панели |

Кнопка работает как переключатель: если роль уже есть — она снимается, если нет — выдаётся.
В эксклюзивной группе при выдаче одной роли остальные роли группы снимаются автоматически.
Мастер `/selfrole build` позволяет выбрать до 25 ролей селектором и сразу получить готовый embed.

### Автомодерация

| Команда | Описание |
|---|---|
| `/automod enable` / `disable` | Вкл/выкл |
| `/automod add word` | Добавить слово/фразу (`дур*` — подстрока) |
| `/automod remove word` | Удалить слово |
| `/automod list` / `clear` | Показать/очистить пул |
| `/automod action <delete\|warn\|timeout>` | Действие |
| `/automod timeout minutes` | Длительность тайм-аута |
| `/automod ignore-channel` / `unignore-channel` | Исключения каналов |
| `/automod ignore-role` / `unignore-role` | Исключения ролей |
| `/automod test text` | Проверить текст |
| `/automod status` | Настройки |

### Предупреждения

| Команда | Описание |
|---|---|
| `/warn member reason` | Выдать |
| `/warnings member` | Активные и пороги |
| `/unwarn warning_id [reason]` | Снять |
| `/clearwarnings member` | Снять все |
| `/warnconfig show` | Настройки |
| `/warnconfig escalation count action [duration]` | Добавить порог |
| `/warnconfig escalation-remove count` | Удалить порог |
| `/warnconfig dm state` | DM-уведомления |
| `/warnconfig channel [#канал]` | Канал логов |
| `/warnconfig reset` | Сброс |

По умолчанию: 3 → тайм-аут 1 ч, 5 → кик.

### Временные наказания

| Команда | Описание |
|---|---|
| `/tempactions ban member duration [reason]` | Временный бан |
| `/tempactions mute member duration [reason]` | Тайм-аут |
| `/tempactions list` | Активные |
| `/tempactions cancel action_id` | Снять досрочно |

### Тикеты

| Команда / кнопка | Описание |
|---|---|
| `/ticket setup channel title [description] [support_role] [category]` | Панель |
| `/ticket panels` | Список панелей и ID |
| `/ticket close` | Закрыть тикет в текущем канале |
| `/ticket add member` / `remove member` | Доступ к тикету |
| `/ticket list` | Открытые тикеты |
| `/ticket delete panel_id` | Удалить панель |
| `/ticket log [#канал]` | Канал логов и транскриптов |
| Кнопки в тикете | «Взять» и «Закрыть» |

### Апелляции

| Команда / кнопка | Описание |
|---|---|
| `/appeal warning_id reason` | Подать апелляцию |
| `/appeals list` | Ожидающие |
| `/appeals channel [#канал]` | Канал апелляций |
| Кнопки «Одобрить» / «Отклонить» | Решение поддержки |

### Антирейд

| Команда | Описание |
|---|---|
| `/antiraid enable` / `disable` | Вкл/выкл |
| `/antiraid action <kick\|ban\|timeout>` | Действие с новыми аккаунтами |
| `/antiraid joins count` | Порог входов |
| `/antiraid window seconds` | Окно |
| `/antiraid min-age days` | Минимальный возраст аккаунта |
| `/antiraid duration seconds` | Длительность режима рейда |
| `/antiraid lockdown minutes` / `unlock` | Ручной локдаун |
| `/antiraid status` | Настройки |

### Антиспам

| Команда | Описание |
|---|---|
| `/antispam enable` / `disable` | Вкл/выкл |
| `/antispam count count` | Сообщений за окно |
| `/antispam window seconds` | Окно |
| `/antispam action <delete\|timeout>` | Действие |
| `/antispam timeout seconds` | Длительность тайм-аута |
| `/antispam ignore-channel` / `unignore-channel` | Исключения |
| `/antispam status` | Настройки |

### Капча

| Команда | Описание |
|---|---|
| `/captcha setup verified_role [unverified_role] [timeout_minutes] [kick]` | Включить |
| `/captcha disable` | Выключить |
| `/captcha status` | Настройки |

### Статистика

| Команда | Описание |
|---|---|
| `/stats add <kind> channel [template]` | Добавить канал |
| `/stats remove channel` | Убрать |
| `/stats list` | Список |
| `/stats enable` / `disable` | Вкл/выкл |

Типы: `members`, `humans`, `bots`, `boosts`. Шаблон должен содержать `{count}`.

### Розыгрыши

| Команда | Описание |
|---|---|
| `/giveaway start prize duration [winners] [required_role] [channel]` | Запустить |
| `/giveaway end giveaway_id` | Завершить досрочно |
| `/giveaway reroll giveaway_id` | Перевыбрать победителей |
| `/giveaway list` | Активные |

### Магазин лояльности

| Команда | Описание |
|---|---|
| `/shop list` | Товары |
| `/shop add role name price [description] [stock]` | Добавить товар |
| `/shop remove item_id` | Удалить товар |
| `/buy item_id` | Купить |
| `/balance` | Баланс |

### Уровни

| Команда | Описание |
|---|---|
| `/rank [member]` | Профиль |
| `/leaderboard [metric]` | Топ-10 |
| `/daily` | Ежедневная награда |
| `/levelconfig status` | Настройки |
| `/levelconfig xp min max` | Опыт за сообщение |
| `/levelconfig cooldown seconds` | Кулдаун |
| `/levelconfig channel [#канал]` | Канал объявлений |
| `/levelconfig announce state` | Объявления |
| `/levelconfig reward level role` | Роль за уровень |
| `/levelconfig reward-remove level` | Убрать награду |
| `/levelconfig ignore-channel` / `unignore-channel` | Исключения |
| `/levelconfig add-xp member amount [reason]` | Начислить XP |
| `/levelconfig remove-xp member amount [reason]` | Снять XP |
| `/levelconfig set-xp member amount [reason]` | Установить XP |
| `/levelconfig add-loyalty` / `remove-loyalty` / `set-loyalty` | То же для лояльности |
| `/levelconfig multiplier-xp value` | Глобальный множитель XP |
| `/levelconfig multiplier-loyalty value` | Глобальный множитель лояльности |
| `/levelconfig multiplier-booster value` | Бонус бустерам сервера |
| `/levelconfig event value duration` | Временное событие на XP и лояльность |
| `/levelconfig event-clear` | Выключить событие |
| `/levelconfig boost-role role value` | Бонус за роль |
| `/levelconfig boost-role-remove role` | Убрать бонус роли |
| `/levelconfig boosts` | Все множители |
| `/levelconfig reset` | Сброс |

### Доступ к командам

| Команда | Описание |
|---|---|
| `/access allow-role command role` | Разрешить команду только роли |
| `/access remove-role command role` | Убрать роль из разрешённых |
| `/access allow-user command user` | Разрешить конкретному пользователю |
| `/access remove-user command user` | Убрать пользователя |
| `/access deny-role` / `undeny-role` | Запретить/разрешить роль |
| `/access deny-user` / `undeny-user` | Запретить/разрешить пользователя |
| `/access disable` / `enable` | Полностью выключить/включить команду |
| `/access reset command` | Снять все ограничения |
| `/access presets` | Показать готовые списки команд |
| `/access list [command]` | Показать правила |

`command` — имя команды, группы настроек или готового списка: `warn`, `levelconfig`,
`player`, `moderator`, `admin`. Список применяется сразу ко всем своим командам, поэтому
не нужно выдавать доступ по одной команде. Ограничение группы (`levelconfig`) действует
на все её подкоманды; ограничения родителя и подкоманды складываются. В параметре `command`
работают подсказки команд и списков. Администраторы и владелец сервера всегда имеют доступ.

### Прочее

`/settings view`, `/settings reset`, `/settings language`, `/help`, `/ping`, `/botinfo`.

## Как это работает

### Фильтр слов

Текст и слова приводятся к одному виду: NFKC + casefold, удаление zero-width,
leet-цифры (`sh1t` → `shit`), «скелет» с кириллицей→латиницей для сравнения
(`аss` совпадёт с `ass`). Поиск по границам слов, `*` — подстрока.
Модераторы с `Manage Messages` не проверяются.

### Тикеты

Панель хранится в БД, кнопка создаёт приватный канал с overwrites для автора,
роли поддержки и бота. Кнопка «Взять» фиксирует исполнителя, «Закрыть» —
спрашивает подтверждение, собирает историю в `.txt`, отправляет в лог и удаляет канал.
После рестарта persistent views восстанавливаются из БД.

### Временные наказания

Запись с `expires_at` лежит в БД. Фоновая задача раз в 30 секунд снимает истёкшие
баны (`guild.unban`) и тайм-ауты. Наказания переживают перезапуск бота.

### Антирейд и антиспам

Антирейд считает входы в скользящем окне. При превышении порога включает режим
рейда на `antiraid_duration`; в это время новые аккаунты моложе `min-age` получают
kick/ban/timeout. Антиспам считает сообщения пользователя; при превышении —
удаляет и опционально выдаёт тайм-аут.

### Капча

Новому участнику выдаётся роль «не проверен» (если задана) и отправляется код в ЛС.
Кнопка открывает модальное окно; после верного кода выдаётся роль «проверен».
Если код не введён за `captcha_timeout`, участник кикается (настраивается).

### Апелляции

`/appeal` проверяет, что предупреждение принадлежит автору и ещё активно, и
отправляет embed с кнопками в канал апелляций. Одобрение снимает предупреждение,
отклонение — нет; участник получает DM.

### Уровни

XP за сообщение 15–25, кулдаун 60 секунд; переход `L → L+1` стоит `100 + 50·L` XP,
всего до уровня `L` — `25·L² + 75·L`. `/daily`: `10 + 2·(стрик−1)`, максимум 50.
Статусы: Бронза 0, Серебро 100, Золото 500, Платина 1500, Алмаз 5000.

#### Накрутка и множители

Итоговый множитель = `глобальный × событие × лучшая роль × бустер`, но не выше **×100**.
Он применяется и к XP, и к лояльности за сообщение. Администратор может вручную
начислить, снять или установить точное значение XP/лояльности — уровень пересчитывается,
награды за новые уровни выдаются, а при понижении роли не снимаются.

- `/levelconfig add-xp @user 5000` — быстро поднять уровень;
- `/levelconfig multiplier-xp 2` — удвоить XP на сервере;
- `/levelconfig event 1.5 7d` — событие ×1.5 на неделю;
- `/levelconfig boost-role @Boost 1.25` — бонус роли;
- `/levelconfig multiplier-booster 1.5` — бонус бустерам Discord.

Все ручные изменения попадают в канал модерации с указанием модератора, причины,
старого и нового значения. Множитель `0` полностью выключает начисление XP/лояльности.

### Доступ к командам

Правила лежат в таблице `command_access` и проверяются глобальным
`interaction_check` до выполнения команды. Путь команды берётся из
`interaction.data`, поэтому работают и вложенные подкоманды. Если для команды
заданы разрешённые роли/пользователи, доступ есть только у них; запрещённые
роли/пользователи блокируются всегда. Пустой список разрешённых и запрещённых —
команда без ограничений. Администраторы и владелец сервера обходят правила,
чтобы нельзя было потерять управление сервером.

## Веб-дашборд

Дашборд — **отдельный сервис**, он не входит в процесс бота. Бот и панель
используют одну базу (`data/bot.db` или `DATABASE_URL`), поэтому изменения
сохраняются для обоих. Бот подхватывает настройки из панели в течение ~15 секунд.

### Установка и запуск

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-dashboard.txt
# для PostgreSQL:
python -m pip install -r requirements-postgres.txt
python dashboard.py
```

Панель откроется на `http://127.0.0.1:8080`. Бот запускается отдельно как обычно:
`python bot.py`.

В Discord Developer Portal → OAuth2 добавьте Redirect URI:

```
http://127.0.0.1:8080/auth/callback
```

и заполните `.env`:

```dotenv
DISCORD_TOKEN=токен_бота
DISCORD_CLIENT_ID=...
DISCORD_CLIENT_SECRET=...
DASHBOARD_SECRET=длинная_случайная_строка
DASHBOARD_BASE_URL=http://127.0.0.1:8080
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8080
```

Для входа нужны права **Управлять сервером**. Бот должен быть на сервере —
панель читает каналы и роли через его токен.

### Запуск на IP `84.201.132.67`

В `.env` на хосте:

```dotenv
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=8080
DASHBOARD_BASE_URL=http://84.201.132.67:8080
```

`0.0.0.0` обязателен: если оставить `127.0.0.1`, панель будет доступна только
внутри самого сервера. После запуска проверьте:

```bash
curl http://84.201.132.67:8080/healthz
```

Ожидаемый ответ: `{"status":"ok"}`. Также откройте порт в firewall/security group:

```bash
sudo ufw allow 8080/tcp
```

Redirect URI в Discord Developer Portal:

```
http://84.201.132.67:8080/auth/callback
```

Важно: Discord требует **HTTPS** для OAuth в production и может отклонить
HTTP-redirect на IP. Если Developer Portal не принимает такой URI, используйте
домен и reverse proxy с Let's Encrypt либо HTTPS на IP с сертификатом:

```bash
openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout dashboard-key.pem -out dashboard-cert.pem -days 365 \
  -subj "/CN=84.201.132.67" \
  -addext "subjectAltName=IP:84.201.132.67"

# .env
DASHBOARD_PORT=8443
DASHBOARD_BASE_URL=https://84.201.132.67:8443
DASHBOARD_SSL_CERT=dashboard-cert.pem
DASHBOARD_SSL_KEY=dashboard-key.pem
```

С самоподписанным сертификатом браузер покажет предупреждение — это ожидаемо,
но для постоянной работы лучше домен и нормальный сертификат. **Не включайте
`DASHBOARD_DEV_MODE` на публичном IP**: любой сможет войти без Discord.

### Что настраивается

- **Обзор** — карточки модулей с быстрыми переключателями и статистикой сервера.
- **Модерация** — последние предупреждения, активные наказания и апелляции.
- **Автороли** — роли людям и ботам, включение/выключение, удаление.
- **Роли по кнопкам** — создание embed-панелей с кнопками и удаление.
- **Логи** — общий канал, канал модерации, группы событий.
- **Автомодерация** — действие, тайм-аут, исключения, пул запрещённых слов.
- **Предупреждения** — DM и пороги автонаказаний.
- **Уровни и лояльность** — XP, кулдаун, награды, множители, дейли.
- **Безопасность** — антирейд, антиспам, капча.
- **Тикеты и апелляции** — каналы и лимиты.
- **Статистика** — живые счётчики участников/бустов, каналы-счётчики и шаблоны.
- **Доступ к командам** — выбор команд, ролей и участников из списков, готовые списки `player`, `moderator`, `admin`.

Роли с административными правами и правами модерации **нельзя** добавить в
автороли, selfrole, магазин или капчу — это защита от случайной выдачи доступа.
Поиск по настройкам находится прямо в форме раздела.

Список команд для страницы доступа лежит в `dashboard/commands.json`. После
переименования или добавления команд обновите его:

```bash
python scripts/dump_dashboard_commands.py
```

### Локальный режим без OAuth

Для проверки интерфейса можно временно включить:

```dotenv
DASHBOARD_DEV_MODE=1
DASHBOARD_DEV_GUILD_ID=ID_сервера
DASHBOARD_DEV_USER_ID=ID_пользователя
```

и открыть `/auth/dev`. **Не включайте этот режим на публичном сервере.**
На публичном адресе dev-вход заблокирован; обойти можно только явно задав
`DASHBOARD_ALLOW_PUBLIC_DEV=1` — делать это не рекомендуется.

### Docker

```bash
docker compose -f docker-compose.dashboard.yml up -d --build
```

Сервис использует тот же `.env` и тот же volume `./data`, что и бот.

Для PostgreSQL dashboard-образ собирается с `WITH_POSTGRES=1` и содержит `asyncpg`.
Если база запущена в другом compose-проекте, подключите контейнеры к одной Docker-сети
или укажите доступный `DATABASE_URL`.

## PostgreSQL

Бот поддерживает обе СУБД. SQLite — по умолчанию. PostgreSQL включается переменной
`DATABASE_URL`; схема создаётся автоматически при первом подключении.

### Вариант 1. Локальный PostgreSQL

1. Установите PostgreSQL 13+ и создайте базу:

```sql
CREATE USER modbot WITH PASSWORD 'strong_password';
CREATE DATABASE modbot OWNER modbot;
```

2. Установите драйвер:

```bash
python -m pip install -r requirements-postgres.txt
```

3. Пропишите в `.env`:

```dotenv
DATABASE_URL=postgresql://modbot:strong_password@localhost:5432/modbot
```

`DB_PATH` при этом игнорируется. Пароль с символами `@ : / ? #` нужно
URL-кодировать (`@` → `%40` и т.п.).

4. Запустите бота:

```bash
python bot.py
```

В логе будет `PostgreSQL подключена`, а в базе — все таблицы.

### Вариант 2. Docker Compose

```bash
# пароль для контейнера БД
echo "POSTGRES_PASSWORD=strong_password" >> .env
docker compose -f docker-compose.postgres.yml up -d --build
docker compose -f docker-compose.postgres.yml logs -f bot
```

`docker-compose.postgres.yml` поднимает `postgres:16-alpine`, ждёт healthcheck,
собирает образ бота с `asyncpg` и передаёт `DATABASE_URL` внутри сети Docker.
Данные БД — в томе `pgdata`.

### Перенос данных из SQLite

Остановите бота, создайте **пустую** базу PostgreSQL и выполните:

```bash
python scripts/migrate_sqlite_to_postgres.py \
  --sqlite data/bot.db \
  --postgres postgresql://modbot:strong_password@localhost:5432/modbot
```

Скрипт сохраняет `id`, порядок таблиц и сбрасывает последовательности (`setval`).
Целевая база должна быть пустой — иначе скрипт остановится, чтобы не смешать данные.

Альтернатива — `pgloader`:

```bash
docker run --rm --network host dimitri/pgloader:latest \
  pgloader sqlite:///data/bot.db postgresql://modbot:strong_password@localhost:5432/modbot
```

После переноса проверьте бота на тестовом сервере и сделайте бэкап.

### Бэкапы и обслуживание

```bash
# дамп
docker exec discord-mod-bot-db pg_dump -U modbot modbot > backup.sql

# восстановление в пустую базу
cat backup.sql | docker exec -i discord-mod-bot-db psql -U modbot -d modbot
```

Полезные запросы:

```sql
SELECT COUNT(*) FROM warnings WHERE active = 1;
SELECT guild_id, COUNT(*) FROM members GROUP BY guild_id;
SELECT id, prize, ends_at FROM giveaways WHERE ended = 0;
```

### Особенности реализации

- один `asyncpg`-connection на процесс, запросы сериализуются `asyncio.Lock`;
- время хранится как `TEXT` ISO 8601 UTC — одинаково для SQLite и PostgreSQL;
- флаги (`active`, `ended`) — `INTEGER` 0/1, а не `BOOLEAN`, чтобы код был общим;
- SQL пишется с `?`, слой сам превращает их в `$1..$N`;
- `INSERT OR IGNORE` превращается в `ON CONFLICT DO NOTHING`;
- схема PostgreSQL — `core/schema_postgres.sql`, версия в таблице `meta`;
- при использовании PgBouncer нужен **session mode**: asyncpg использует
  prepared statements, transaction pooling может ломать их.

## Структура проекта

```
.
├── bot.py
├── config.py
├── requirements.txt
├── requirements-postgres.txt
├── requirements-dashboard.txt
├── dashboard.py           # отдельный сервис панели
├── Dockerfile.dashboard
├── docker-compose.dashboard.yml
├── .env.example
├── dashboard/
│   ├── app.py             # FastAPI-приложение
│   ├── auth.py            # Discord OAuth, сессия, CSRF
│   ├── config.py          # настройки панели
│   ├── discord_api.py     # чтение серверов/каналов/ролей
│   ├── schema.py          # поля настроек
│   ├── templates/
│   └── static/
├── cogs/
│   ├── logs.py            # логи
│   ├── autorole.py        # автoроли
│   ├── selfroles.py       # роли по кнопкам
│   ├── automod.py         # фильтр слов
│   ├── warnings.py        # предупреждения
│   ├── levels.py          # уровни
│   ├── tickets.py         # тикеты
│   ├── tempactions.py     # временные наказания
│   ├── security.py        # антирейд + антиспам
│   ├── captcha.py         # капча
│   ├── appeals.py         # апелляции
│   ├── stats.py           # статистика в каналах
│   ├── giveaways.py       # розыгрыши
│   ├── shop.py            # магазин лояльности
│   ├── access.py          # ограничения команд
│   ├── settings.py
│   └── utility.py
├── core/
│   ├── database.py        # SQLite/PostgreSQL
│   ├── schema.sql         # схема SQLite
│   ├── schema_postgres.sql# схема PostgreSQL
│   ├── repositories.py    # SQL и dataclass-модели
│   ├── settings.py
│   ├── textfilter.py
│   ├── leveling.py
│   ├── timeparse.py
│   ├── ratelimit.py
│   ├── giveaways.py
│   ├── warning_service.py
│   ├── moderation.py
│   ├── embeds.py
│   ├── discord_utils.py
│   ├── env.py
│   ├── errors.py
│   ├── logging_setup.py
│   └── utils.py
├── scripts/
│   ├── check_commands.py  # загрузка всех когов и проверка дерева команд
│   ├── dump_dashboard_commands.py # обновление списка команд для дашборда
│   └── migrate_sqlite_to_postgres.py
├── tests/                 # 113 тестов
├── Dockerfile
├── docker-compose.yml
├── docker-compose.postgres.yml
├── start.bat
└── start.sh
```

## База данных

Таблицы: `guild_settings`, `warnings`, `members`, `level_rewards`, `banned_words`,
`autoroles`, `selfrole_panels`, `selfrole_options`, `ticket_panels`, `tickets`,
`temp_actions`, `appeals`, `giveaways`, `giveaway_entries`, `stats_channels`,
`shop_items`, `shop_purchases`, `captcha_pending`, `meta`.

SQLite-файл по умолчанию — `data/bot.db` (WAL). Резервная копия — копия файла
при остановленном боте. Для PostgreSQL — `pg_dump`, см. выше.

## Тесты

```bash
python -m unittest discover -s tests -v
python scripts/check_commands.py
```

113 тестов: формулы уровней, множители XP, фильтр слов, парсинг времени, репозитории всех новых
модулей, диалект PostgreSQL, скользящее окно, выбор победителей, страницы и формы веб-панели.
Отдельный тест загружает все коги и проверяет дерево команд.
Живой Discord API не тестируется — нужен токен и сервер.

## Docker (SQLite)

```bash
cp .env.example .env
# заполните DISCORD_TOKEN
docker compose up -d --build
docker compose logs -f
```

## Systemd

```ini
[Unit]
Description=Discord Moderation Bot
After=network-online.target

[Service]
Type=simple
User=discordbot
WorkingDirectory=/opt/discord-mod-bot
ExecStart=/opt/discord-mod-bot/.venv/bin/python bot.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

## Частые проблемы

| Симптом | Решение |
|---|---|
| Команды не появились | Укажите `GUILD_ID`, перезапустите; проверьте `applications.commands` |
| «Приложение не отвечает» | Нет прав в канале или роль бота ниже управляемой |
| Бот не выдаёт роли | `Manage Roles`, роль бота выше выдаваемой |
| Тикеты не создаются | Нужно право **`Manage Channels`** |
| Капча не приходит | Закрыты ЛС; участник может написать боту первым |
| Статистика не обновляется | Право `Manage Channels` на голосовом канале; Discord лимит 2 правки/10 мин |
| `asyncpg` не найден | `pip install -r requirements-postgres.txt` |
| `password authentication failed` | Проверьте логин/пароль в `DATABASE_URL`, права пользователя |
| PG: `relation does not exist` | Бот не подключился к нужной базе; проверьте DSN и `search_path` |
| `PrivilegedIntentsRequired` | Включите Server Members и Message Content |
| Автомод молчит | `/automod status`, `/automod test`, исключения, `Manage Messages` |

## Что осталось из roadmap

- полноценная i18n (сейчас тексты на русском);
- regex-правила автомодерации и антиспам по упоминаниям/ссылкам;
- формы тикетов (несколько типов обращений);
- апелляции банов для участников вне сервера;
- шардинг и пул соединений при очень больших нагрузках.

## Лицензия

MIT — см. [LICENSE](LICENSE).

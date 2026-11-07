-- Схема БД бота. Все запросы идемпотентны (IF NOT EXISTS),
-- поэтому файл можно безопасно выполнять при каждом запуске.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Настройки сервера в формате ключ -> JSON-значение.
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id   INTEGER NOT NULL,
    key        TEXT    NOT NULL,
    value      TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    PRIMARY KEY (guild_id, key)
);

-- Автоматически выдаваемые роли при входе.
CREATE TABLE IF NOT EXISTS autoroles (
    guild_id   INTEGER NOT NULL,
    role_id    INTEGER NOT NULL,
    kind       TEXT    NOT NULL DEFAULT 'human', -- human | bot
    added_by   INTEGER,
    created_at TEXT    NOT NULL,
    PRIMARY KEY (guild_id, role_id, kind)
);

-- Панели ролей-кнопок.
CREATE TABLE IF NOT EXISTS selfrole_panels (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    color       INTEGER NOT NULL DEFAULT 5793266,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_selfrole_panels_guild
    ON selfrole_panels (guild_id);

CREATE TABLE IF NOT EXISTS selfrole_options (
    panel_id INTEGER NOT NULL REFERENCES selfrole_panels (id) ON DELETE CASCADE,
    role_id  INTEGER NOT NULL,
    label    TEXT,
    emoji    TEXT,
    style    TEXT    NOT NULL DEFAULT 'secondary',
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (panel_id, role_id)
);

-- Эксклюзивные группы: выдача одной роли снимает остальные роли группы.
CREATE TABLE IF NOT EXISTS selfrole_exclusive (
    panel_id   INTEGER NOT NULL REFERENCES selfrole_panels (id) ON DELETE CASCADE,
    role_id    INTEGER NOT NULL,
    group_name TEXT    NOT NULL,
    PRIMARY KEY (panel_id, role_id)
);

-- Пул запрещённых слов.
CREATE TABLE IF NOT EXISTS banned_words (
    guild_id   INTEGER NOT NULL,
    word       TEXT    NOT NULL,
    added_by   INTEGER,
    created_at TEXT    NOT NULL,
    PRIMARY KEY (guild_id, word)
);

-- Предупреждения (история хранится целиком, active = 0 у снятых).
CREATE TABLE IF NOT EXISTS warnings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    moderator_id INTEGER,
    reason       TEXT    NOT NULL,
    source       TEXT    NOT NULL DEFAULT 'manual', -- manual | automod
    created_at   TEXT    NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_warnings_user
    ON warnings (guild_id, user_id, active);

-- Уровни, опыт и лояльность.
CREATE TABLE IF NOT EXISTS members (
    guild_id      INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    xp            INTEGER NOT NULL DEFAULT 0,
    level         INTEGER NOT NULL DEFAULT 0,
    messages      INTEGER NOT NULL DEFAULT 0,
    loyalty       INTEGER NOT NULL DEFAULT 0,
    last_xp_at    TEXT,
    last_daily_at TEXT,
    daily_streak  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_members_xp
    ON members (guild_id, xp DESC);

CREATE INDEX IF NOT EXISTS idx_members_loyalty
    ON members (guild_id, loyalty DESC);

-- Награды за уровни (роль за достижение уровня).
CREATE TABLE IF NOT EXISTS level_rewards (
    guild_id INTEGER NOT NULL,
    level    INTEGER NOT NULL,
    role_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, level)
);

-- ===========================================================================
-- Тикеты
-- ===========================================================================

CREATE TABLE IF NOT EXISTS ticket_panels (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    message_id      INTEGER,
    title           TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    category_id     INTEGER,
    support_role_id INTEGER,
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ticket_panels_guild
    ON ticket_panels (guild_id);

CREATE TABLE IF NOT EXISTS tickets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    panel_id   INTEGER REFERENCES ticket_panels (id) ON DELETE SET NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    user_id    INTEGER NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'open', -- open | closed
    claimed_by INTEGER,
    created_at TEXT    NOT NULL,
    closed_at  TEXT,
    closed_by  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_tickets_user
    ON tickets (guild_id, user_id, status);
CREATE INDEX IF NOT EXISTS idx_tickets_channel
    ON tickets (channel_id);

-- ===========================================================================
-- Временные наказания
-- ===========================================================================

CREATE TABLE IF NOT EXISTS temp_actions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    moderator_id INTEGER,
    action       TEXT    NOT NULL, -- ban | timeout
    reason       TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    expires_at   TEXT    NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_temp_actions_due
    ON temp_actions (active, expires_at);

-- ===========================================================================
-- Апелляции предупреждений
-- ===========================================================================

CREATE TABLE IF NOT EXISTS appeals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    warning_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    reason      TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending', -- pending | approved | denied
    channel_id  INTEGER,
    message_id  INTEGER,
    created_at  TEXT    NOT NULL,
    reviewed_by INTEGER,
    reviewed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_appeals_pending
    ON appeals (guild_id, status);

-- ===========================================================================
-- Розыгрыши
-- ===========================================================================

CREATE TABLE IF NOT EXISTS giveaways (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id         INTEGER NOT NULL,
    channel_id       INTEGER NOT NULL,
    message_id       INTEGER,
    prize            TEXT    NOT NULL,
    winners_count    INTEGER NOT NULL DEFAULT 1,
    host_id          INTEGER,
    required_role_id INTEGER,
    ends_at          TEXT    NOT NULL,
    ended            INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_giveaways_due
    ON giveaways (ended, ends_at);

CREATE TABLE IF NOT EXISTS giveaway_entries (
    giveaway_id INTEGER NOT NULL REFERENCES giveaways (id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL,
    created_at  TEXT    NOT NULL,
    PRIMARY KEY (giveaway_id, user_id)
);

-- ===========================================================================
-- Статистика в голосовых каналах
-- ===========================================================================

CREATE TABLE IF NOT EXISTS stats_channels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    kind       TEXT    NOT NULL, -- members | humans | bots | boosts
    template   TEXT    NOT NULL,
    UNIQUE (guild_id, channel_id)
);

-- ===========================================================================
-- Магазин лояльности
-- ===========================================================================

CREATE TABLE IF NOT EXISTS shop_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    role_id     INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    price       INTEGER NOT NULL,
    stock       INTEGER, -- NULL = без ограничений
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shop_items_guild
    ON shop_items (guild_id);

CREATE TABLE IF NOT EXISTS shop_purchases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    item_id    INTEGER NOT NULL,
    price      INTEGER NOT NULL,
    created_at TEXT    NOT NULL
);

-- ===========================================================================
-- Капча
-- ===========================================================================

CREATE TABLE IF NOT EXISTS captcha_pending (
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    code       TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    expires_at TEXT    NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

-- ===========================================================================
-- Ограничение доступа к командам по ролям и пользователям
-- ===========================================================================

CREATE TABLE IF NOT EXISTS command_access (
    guild_id    INTEGER NOT NULL,
    command     TEXT    NOT NULL,
    allow_roles TEXT    NOT NULL DEFAULT '[]',
    allow_users TEXT    NOT NULL DEFAULT '[]',
    deny_roles  TEXT    NOT NULL DEFAULT '[]',
    deny_users  TEXT    NOT NULL DEFAULT '[]',
    enabled     INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT    NOT NULL,
    PRIMARY KEY (guild_id, command)
);

CREATE INDEX IF NOT EXISTS idx_command_access_guild
    ON command_access (guild_id);


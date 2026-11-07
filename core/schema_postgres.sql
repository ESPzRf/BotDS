-- Схема PostgreSQL. Идентична SQLite по логике, отличается типами:
-- BIGSERIAL для автоинкрементных id, BIGINT для snowflake-идентификаторов.
-- Все временные метки хранятся как TEXT (ISO 8601 UTC), чтобы код был общим
-- для SQLite и PostgreSQL.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id   BIGINT NOT NULL,
    key        TEXT   NOT NULL,
    value      TEXT   NOT NULL,
    updated_at TEXT   NOT NULL,
    PRIMARY KEY (guild_id, key)
);

CREATE TABLE IF NOT EXISTS autoroles (
    guild_id   BIGINT  NOT NULL,
    role_id    BIGINT  NOT NULL,
    kind       TEXT    NOT NULL DEFAULT 'human',
    added_by   BIGINT,
    created_at TEXT    NOT NULL,
    PRIMARY KEY (guild_id, role_id, kind)
);

CREATE TABLE IF NOT EXISTS selfrole_panels (
    id          BIGSERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    channel_id  BIGINT NOT NULL,
    message_id  BIGINT,
    title       TEXT   NOT NULL,
    description TEXT   NOT NULL DEFAULT '',
    color       INTEGER NOT NULL DEFAULT 5793266,
    created_at  TEXT   NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_selfrole_panels_guild
    ON selfrole_panels (guild_id);

CREATE TABLE IF NOT EXISTS selfrole_options (
    panel_id BIGINT  NOT NULL REFERENCES selfrole_panels (id) ON DELETE CASCADE,
    role_id  BIGINT  NOT NULL,
    label    TEXT,
    emoji    TEXT,
    style    TEXT    NOT NULL DEFAULT 'secondary',
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (panel_id, role_id)
);

CREATE TABLE IF NOT EXISTS selfrole_exclusive (
    panel_id   BIGINT NOT NULL REFERENCES selfrole_panels (id) ON DELETE CASCADE,
    role_id    BIGINT NOT NULL,
    group_name TEXT   NOT NULL,
    PRIMARY KEY (panel_id, role_id)
);

CREATE TABLE IF NOT EXISTS banned_words (
    guild_id   BIGINT NOT NULL,
    word       TEXT   NOT NULL,
    added_by   BIGINT,
    created_at TEXT   NOT NULL,
    PRIMARY KEY (guild_id, word)
);

CREATE TABLE IF NOT EXISTS warnings (
    id           BIGSERIAL PRIMARY KEY,
    guild_id     BIGINT NOT NULL,
    user_id      BIGINT NOT NULL,
    moderator_id BIGINT,
    reason       TEXT   NOT NULL,
    source       TEXT   NOT NULL DEFAULT 'manual',
    created_at   TEXT   NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_warnings_user
    ON warnings (guild_id, user_id, active);

CREATE TABLE IF NOT EXISTS members (
    guild_id      BIGINT  NOT NULL,
    user_id       BIGINT  NOT NULL,
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

CREATE TABLE IF NOT EXISTS level_rewards (
    guild_id BIGINT  NOT NULL,
    level    INTEGER NOT NULL,
    role_id  BIGINT  NOT NULL,
    PRIMARY KEY (guild_id, level)
);

-- ---------------------------------------------------------------------------
-- Тикеты
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ticket_panels (
    id              BIGSERIAL PRIMARY KEY,
    guild_id        BIGINT NOT NULL,
    channel_id      BIGINT NOT NULL,
    message_id      BIGINT,
    title           TEXT   NOT NULL,
    description     TEXT   NOT NULL DEFAULT '',
    category_id     BIGINT,
    support_role_id BIGINT,
    created_at      TEXT   NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ticket_panels_guild
    ON ticket_panels (guild_id);

CREATE TABLE IF NOT EXISTS tickets (
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    panel_id   BIGINT REFERENCES ticket_panels (id) ON DELETE SET NULL,
    channel_id BIGINT NOT NULL,
    message_id BIGINT,
    user_id    BIGINT NOT NULL,
    status     TEXT   NOT NULL DEFAULT 'open',
    claimed_by BIGINT,
    created_at TEXT   NOT NULL,
    closed_at  TEXT,
    closed_by  BIGINT
);

CREATE INDEX IF NOT EXISTS idx_tickets_user
    ON tickets (guild_id, user_id, status);
CREATE INDEX IF NOT EXISTS idx_tickets_channel
    ON tickets (channel_id);

-- ---------------------------------------------------------------------------
-- Временные наказания
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS temp_actions (
    id           BIGSERIAL PRIMARY KEY,
    guild_id     BIGINT  NOT NULL,
    user_id      BIGINT  NOT NULL,
    moderator_id BIGINT,
    action       TEXT    NOT NULL,
    reason       TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    expires_at   TEXT    NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_temp_actions_due
    ON temp_actions (active, expires_at);

-- ---------------------------------------------------------------------------
-- Апелляции
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS appeals (
    id          BIGSERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    warning_id  BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    reason      TEXT   NOT NULL,
    status      TEXT   NOT NULL DEFAULT 'pending',
    channel_id  BIGINT,
    message_id  BIGINT,
    created_at  TEXT   NOT NULL,
    reviewed_by BIGINT,
    reviewed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_appeals_pending
    ON appeals (guild_id, status);

-- ---------------------------------------------------------------------------
-- Розыгрыши
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS giveaways (
    id               BIGSERIAL PRIMARY KEY,
    guild_id         BIGINT  NOT NULL,
    channel_id       BIGINT  NOT NULL,
    message_id       BIGINT,
    prize            TEXT    NOT NULL,
    winners_count    INTEGER NOT NULL DEFAULT 1,
    host_id          BIGINT,
    required_role_id BIGINT,
    ends_at          TEXT    NOT NULL,
    ended            INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_giveaways_due
    ON giveaways (ended, ends_at);

CREATE TABLE IF NOT EXISTS giveaway_entries (
    giveaway_id BIGINT NOT NULL REFERENCES giveaways (id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL,
    created_at  TEXT   NOT NULL,
    PRIMARY KEY (giveaway_id, user_id)
);

-- ---------------------------------------------------------------------------
-- Статистика
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stats_channels (
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    kind       TEXT   NOT NULL,
    template   TEXT   NOT NULL,
    UNIQUE (guild_id, channel_id)
);

-- ---------------------------------------------------------------------------
-- Магазин лояльности
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS shop_items (
    id          BIGSERIAL PRIMARY KEY,
    guild_id    BIGINT  NOT NULL,
    role_id     BIGINT  NOT NULL,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    price       INTEGER NOT NULL,
    stock       INTEGER,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shop_items_guild
    ON shop_items (guild_id);

CREATE TABLE IF NOT EXISTS shop_purchases (
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT  NOT NULL,
    user_id    BIGINT  NOT NULL,
    item_id    BIGINT  NOT NULL,
    price      INTEGER NOT NULL,
    created_at TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- Капча
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS captcha_pending (
    guild_id   BIGINT NOT NULL,
    user_id    BIGINT NOT NULL,
    code       TEXT   NOT NULL,
    created_at TEXT   NOT NULL,
    expires_at TEXT   NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

-- ---------------------------------------------------------------------------
-- Ограничение доступа к командам
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS command_access (
    guild_id    BIGINT  NOT NULL,
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

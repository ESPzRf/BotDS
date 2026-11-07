"""Настройки серверов: значения по умолчанию + кэширующее хранилище."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from typing import Any

from core.database import Database


# Централизованные значения по умолчанию. Всё, что не задано на сервере,
# берётся отсюда, поэтому команды настройки могут менять только отличия.
DEFAULTS: dict[str, Any] = {
    # --- общее ------------------------------------------------------------
    "locale": "ru",
    # --- логи -------------------------------------------------------------
    "log_channel": None,          # ID канала общего лога
    "modlog_channel": None,       # ID канала модерации (None => общий лог)
    "log_messages": True,
    "log_members": True,
    "log_roles": True,
    "log_channels": True,
    "log_voice": False,
    "log_invites": False,
    "log_moderation": True,
    # --- автoроли ---------------------------------------------------------
    "autorole_enabled": True,
    # --- автомод ----------------------------------------------------------
    "automod_enabled": False,
    "automod_action": "delete",   # delete | warn | timeout
    "automod_timeout": 600,       # секунды
    "automod_notify": True,
    "automod_ignored_channels": [],
    "automod_ignored_roles": [],
    # --- предупреждения ---------------------------------------------------
    "warnings_dm": True,
    "warn_escalations": [
        {"count": 3, "action": "timeout", "duration": 3600},
        {"count": 5, "action": "kick"},
    ],
    # --- уровни / лояльность ----------------------------------------------
    "levels_enabled": True,
    "levels_xp_min": 15,
    "levels_xp_max": 25,
    "levels_cooldown": 60,
    "levels_channel": None,       # None => награждение в том же канале
    "levels_announce": True,
    "levels_message": "🎉 {user}, достигнут **{level}** уровень!",
    "levels_ignored_channels": [],
    "levels_xp_multiplier": 1.0,      # глобальный множитель опыта
    "levels_loyalty_multiplier": 1.0, # глобальный множитель лояльности
    "levels_booster_multiplier": 1.0, # бонус бустерам сервера
    "levels_event_multiplier": 1.0,   # временное событие
    "levels_event_until": None,       # ISO-время окончания события
    "levels_role_boosts": {},         # {"role_id": multiplier}
    "loyalty_per_message": 1,
    "loyalty_daily_base": 10,
    "loyalty_daily_step": 2,
    "loyalty_daily_cap": 50,
    # --- тикеты -----------------------------------------------------------
    "ticket_log_channel": None,   # None => канал модерации/общий лог
    "ticket_max_open": 1,         # сколько открытых тикетов может иметь участник
    # --- апелляции --------------------------------------------------------
    "appeals_channel": None,      # None => канал модерации
    # --- антирейд / антиспам ----------------------------------------------
    "antiraid_enabled": False,
    "antiraid_joins": 8,          # сколько входов за окно считаются рейдом
    "antiraid_window": 60,        # окно в секундах
    "antiraid_min_age_days": 7,   # минимальный возраст аккаунта (0 = выкл)
    "antiraid_action": "kick",    # kick | ban | timeout
    "antiraid_timeout": 3600,     # секунды для action=timeout
    "antiraid_duration": 300,     # сколько длится режим рейда
    "raid_mode_until": None,      # ISO-время ручного/авто локдауна
    "antispam_enabled": False,
    "antispam_count": 5,          # сообщений за окно
    "antispam_window": 10,        # окно в секундах
    "antispam_action": "timeout", # delete | timeout
    "antispam_timeout": 600,      # секунды
    "antispam_ignored_channels": [],
    # --- капча ------------------------------------------------------------
    "captcha_enabled": False,
    "captcha_role": None,          # роль «проверен»
    "captcha_unverified_role": None,
    "captcha_timeout": 600,        # секунды до кика
    "captcha_kick": True,
    # --- статистика в каналах --------------------------------------------
    "stats_enabled": True,
    # --- розыгрыши --------------------------------------------------------
    "giveaway_emoji": "🎉",
}

DEFAULT_WARN_ESCALATIONS: list[dict[str, Any]] = copy.deepcopy(DEFAULTS["warn_escalations"])


class GuildSettingsStore:
    """Чтение/запись настроек с кэшем в памяти.

    Значения хранятся как JSON, поэтому поддерживаются строки, числа,
    булевы значения, списки и словари. Кэш сбрасывается при записи,
    а также методом invalidate() (например, после ручного сброса).
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self._cache: dict[tuple[int, str], Any] = {}

    # ------------------------------------------------------------------ read

    async def get(self, guild_id: int, key: str, default: Any = None) -> Any:
        cache_key = (guild_id, key)
        if cache_key in self._cache:
            return self._cache[cache_key]

        row = await self.db.fetchone(
            "SELECT value FROM guild_settings WHERE guild_id = ? AND key = ?",
            (guild_id, key),
        )
        if row is not None:
            try:
                value = json.loads(row["value"])
            except (TypeError, ValueError):
                value = row["value"]
        elif key in DEFAULTS:
            value = copy.deepcopy(DEFAULTS[key])
        else:
            value = default

        self._cache[cache_key] = value
        return value

    async def get_int(self, guild_id: int, key: str, default: int = 0) -> int:
        value = await self.get(guild_id, key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    async def get_bool(self, guild_id: int, key: str, default: bool = False) -> bool:
        value = await self.get(guild_id, key, default)
        return bool(value)

    async def get_list(self, guild_id: int, key: str, default: list[Any] | None = None) -> list[Any]:
        value = await self.get(guild_id, key, default or [])
        return list(value) if isinstance(value, (list, tuple)) else []

    async def all(self, guild_id: int) -> dict[str, Any]:
        result = copy.deepcopy(DEFAULTS)
        rows = await self.db.fetchall(
            "SELECT key, value FROM guild_settings WHERE guild_id = ?",
            (guild_id,),
        )
        for row in rows:
            try:
                value = json.loads(row["value"])
            except (TypeError, ValueError):
                value = row["value"]
            result[row["key"]] = value
            self._cache[(guild_id, row["key"])] = value
        return result

    # ----------------------------------------------------------------- write

    async def set(self, guild_id: int, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        await self.db.execute(
            """
            INSERT INTO guild_settings (guild_id, key, value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (guild_id, key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (guild_id, key, payload, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        self._cache[(guild_id, key)] = copy.deepcopy(value)

    async def delete_all(self, guild_id: int) -> None:
        await self.db.execute("DELETE FROM guild_settings WHERE guild_id = ?", (guild_id,))
        self.invalidate(guild_id)

    def invalidate(self, guild_id: int) -> None:
        for cache_key in [k for k in self._cache if k[0] == guild_id]:
            self._cache.pop(cache_key, None)

    def invalidate_key(self, guild_id: int, key: str) -> None:
        self._cache.pop((guild_id, key), None)

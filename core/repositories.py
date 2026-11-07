"""Репозитории: весь SQL собран в одном месте.

Репозитории не знают про discord, поэтому их легко тестировать.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.command_access import LEGACY_COMMANDS, LEGACY_GROUPS, CommandRule, normalize_command
from core.database import Database

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# ---------------------------------------------------------------------------
# Модели
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Warning:
    id: int
    guild_id: int
    user_id: int
    moderator_id: int | None
    reason: str
    source: str
    created_at: datetime
    active: bool = True


@dataclass(slots=True)
class Autorole:
    role_id: int
    kind: str


@dataclass(slots=True)
class SelfRolePanel:
    id: int
    guild_id: int
    channel_id: int
    message_id: int | None
    title: str
    description: str
    color: int


@dataclass(slots=True)
class SelfRoleOption:
    panel_id: int
    role_id: int
    label: str | None
    emoji: str | None
    style: str
    position: int


@dataclass(slots=True)
class MemberStats:
    guild_id: int
    user_id: int
    xp: int = 0
    level: int = 0
    messages: int = 0
    loyalty: int = 0
    last_xp_at: datetime | None = None
    last_daily_at: datetime | None = None
    daily_streak: int = 0

    @classmethod
    def empty(cls, guild_id: int, user_id: int) -> "MemberStats":
        return cls(guild_id=guild_id, user_id=user_id)


# ---------------------------------------------------------------------------
# Настройки сервера
# ---------------------------------------------------------------------------


class WarningRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int | None,
        reason: str,
        source: str = "manual",
    ) -> int:
        return await self.db.insert(
            """
            INSERT INTO warnings (guild_id, user_id, moderator_id, reason, source, created_at, active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (guild_id, user_id, moderator_id, reason, source, to_iso(utcnow())),
        )

    async def get(self, warning_id: int) -> Warning | None:
        row = await self.db.fetchone("SELECT * FROM warnings WHERE id = ?", (warning_id,))
        return _warning_from_row(row)

    async def active_for(self, guild_id: int, user_id: int) -> list[Warning]:
        rows = await self.db.fetchall(
            """
            SELECT * FROM warnings
            WHERE guild_id = ? AND user_id = ? AND active = 1
            ORDER BY id ASC
            """,
            (guild_id, user_id),
        )
        return [w for w in (_warning_from_row(row) for row in rows) if w is not None]

    async def count_active(self, guild_id: int, user_id: int) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM warnings WHERE guild_id = ? AND user_id = ? AND active = 1",
            (guild_id, user_id),
        )
        return int(row["c"]) if row else 0


    async def deactivate(self, warning_id: int, guild_id: int | None = None) -> bool:
        if guild_id is None:
            changed = await self.db.execute(
                "UPDATE warnings SET active = 0 WHERE id = ? AND active = 1",
                (warning_id,),
            )
        else:
            changed = await self.db.execute(
                "UPDATE warnings SET active = 0 WHERE id = ? AND guild_id = ? AND active = 1",
                (warning_id, guild_id),
            )
        return changed > 0

    async def clear(self, guild_id: int, user_id: int) -> int:
        return await self.db.execute(
            "UPDATE warnings SET active = 0 WHERE guild_id = ? AND user_id = ? AND active = 1",
            (guild_id, user_id),
        )

    async def recent(self, guild_id: int, *, limit: int = 20, user_id: int | None = None) -> list[Warning]:
        if user_id is None:
            rows = await self.db.fetchall(
                "SELECT * FROM warnings WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
                (guild_id, limit),
            )
        else:
            rows = await self.db.fetchall(
                "SELECT * FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?",
                (guild_id, user_id, limit),
            )
        return [w for w in (_warning_from_row(row) for row in rows) if w is not None]


def _warning_from_row(row: sqlite3.Row | None) -> Warning | None:
    if row is None:
        return None
    return Warning(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        user_id=int(row["user_id"]),
        moderator_id=int(row["moderator_id"]) if row["moderator_id"] is not None else None,
        reason=str(row["reason"]),
        source=str(row["source"]),
        created_at=from_iso(row["created_at"]) or utcnow(),
        active=bool(row["active"]),
    )


# ---------------------------------------------------------------------------


class AutoroleRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, guild_id: int, role_id: int, kind: str = "human", added_by: int | None = None) -> bool:
        changed = await self.db.execute(
            """
            INSERT OR IGNORE INTO autoroles (guild_id, role_id, kind, added_by, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (guild_id, role_id, kind, added_by, to_iso(utcnow())),
        )
        return changed > 0

    async def remove(self, guild_id: int, role_id: int) -> int:
        return await self.db.execute(
            "DELETE FROM autoroles WHERE guild_id = ? AND role_id = ?",
            (guild_id, role_id),
        )

    async def list(self, guild_id: int, kind: str | None = None) -> list[Autorole]:
        if kind is None:
            rows = await self.db.fetchall(
                "SELECT role_id, kind FROM autoroles WHERE guild_id = ? ORDER BY kind, role_id",
                (guild_id,),
            )
        else:
            rows = await self.db.fetchall(
                "SELECT role_id, kind FROM autoroles WHERE guild_id = ? AND kind = ? ORDER BY role_id",
                (guild_id, kind),
            )
        return [Autorole(role_id=int(row["role_id"]), kind=str(row["kind"])) for row in rows]


# ---------------------------------------------------------------------------


class SelfRoleRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create_panel(
        self,
        guild_id: int,
        channel_id: int,
        title: str,
        description: str = "",
        color: int = 0x5865F2,
    ) -> int:
        return await self.db.insert(
            """
            INSERT INTO selfrole_panels (guild_id, channel_id, message_id, title, description, color, created_at)
            VALUES (?, ?, NULL, ?, ?, ?, ?)
            """,
            (guild_id, channel_id, title, description, color, to_iso(utcnow())),
        )

    async def set_message_id(self, panel_id: int, message_id: int | None) -> None:
        await self.db.execute(
            "UPDATE selfrole_panels SET message_id = ? WHERE id = ?",
            (message_id, panel_id),
        )

    async def get_panel(self, panel_id: int) -> SelfRolePanel | None:
        row = await self.db.fetchone("SELECT * FROM selfrole_panels WHERE id = ?", (panel_id,))
        return _panel_from_row(row)

    async def panels(self, guild_id: int) -> list[SelfRolePanel]:
        rows = await self.db.fetchall(
            "SELECT * FROM selfrole_panels WHERE guild_id = ? ORDER BY id ASC",
            (guild_id,),
        )
        return [p for p in (_panel_from_row(row) for row in rows) if p is not None]

    async def all_panels(self) -> list[SelfRolePanel]:
        rows = await self.db.fetchall("SELECT * FROM selfrole_panels ORDER BY id ASC")
        return [p for p in (_panel_from_row(row) for row in rows) if p is not None]

    async def delete_panel(self, panel_id: int) -> bool:
        changed = await self.db.execute("DELETE FROM selfrole_panels WHERE id = ?", (panel_id,))
        return changed > 0

    async def update_panel(
        self,
        panel_id: int,
        *,
        title: str | None = None,
        description: str | None = None,
        color: int | None = None,
    ) -> None:
        updates: list[str] = []
        params: list[Any] = []
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if color is not None:
            updates.append("color = ?")
            params.append(color)
        if not updates:
            return
        params.append(panel_id)
        await self.db.execute(
            f"UPDATE selfrole_panels SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )

    async def add_option(
        self,
        panel_id: int,
        role_id: int,
        *,
        label: str | None = None,
        emoji: str | None = None,
        style: str = "secondary",
    ) -> None:
        row = await self.db.fetchone(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next FROM selfrole_options WHERE panel_id = ?",
            (panel_id,),
        )
        position = int(row["next"]) if row else 0
        await self.db.execute(
            """
            INSERT INTO selfrole_options (panel_id, role_id, label, emoji, style, position)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (panel_id, role_id) DO UPDATE SET
                label = excluded.label,
                emoji = excluded.emoji,
                style = excluded.style
            """,
            (panel_id, role_id, label, emoji, style, position),
        )

    async def remove_option(self, panel_id: int, role_id: int) -> bool:
        await self.db.execute(
            "DELETE FROM selfrole_exclusive WHERE panel_id = ? AND role_id = ?",
            (panel_id, role_id),
        )
        changed = await self.db.execute(
            "DELETE FROM selfrole_options WHERE panel_id = ? AND role_id = ?",
            (panel_id, role_id),
        )
        return changed > 0

    async def options(self, panel_id: int) -> list[SelfRoleOption]:
        rows = await self.db.fetchall(
            "SELECT * FROM selfrole_options WHERE panel_id = ? ORDER BY position ASC, role_id ASC",
            (panel_id,),
        )
        return [
            SelfRoleOption(
                panel_id=int(row["panel_id"]),
                role_id=int(row["role_id"]),
                label=row["label"],
                emoji=row["emoji"],
                style=str(row["style"]),
                position=int(row["position"]),
            )
            for row in rows
        ]

    async def set_group(self, panel_id: int, role_id: int, group_name: str) -> None:
        await self.db.execute(
            """
            INSERT INTO selfrole_exclusive (panel_id, role_id, group_name)
            VALUES (?, ?, ?)
            ON CONFLICT (panel_id, role_id) DO UPDATE SET group_name = excluded.group_name
            """,
            (panel_id, role_id, group_name.strip()[:32]),
        )

    async def clear_group(self, panel_id: int, role_id: int) -> bool:
        changed = await self.db.execute(
            "DELETE FROM selfrole_exclusive WHERE panel_id = ? AND role_id = ?",
            (panel_id, role_id),
        )
        return changed > 0

    async def groups(self, panel_id: int) -> dict[int, str]:
        rows = await self.db.fetchall(
            "SELECT role_id, group_name FROM selfrole_exclusive WHERE panel_id = ?",
            (panel_id,),
        )
        return {int(row["role_id"]): str(row["group_name"]) for row in rows}


def _panel_from_row(row: sqlite3.Row | None) -> SelfRolePanel | None:
    if row is None:
        return None
    return SelfRolePanel(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        channel_id=int(row["channel_id"]),
        message_id=int(row["message_id"]) if row["message_id"] is not None else None,
        title=str(row["title"]),
        description=str(row["description"]),
        color=int(row["color"]),
    )


# ---------------------------------------------------------------------------


class WordRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, guild_id: int, word: str, added_by: int | None = None) -> bool:
        changed = await self.db.execute(
            """
            INSERT OR IGNORE INTO banned_words (guild_id, word, added_by, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (guild_id, word, added_by, to_iso(utcnow())),
        )
        return changed > 0

    async def remove(self, guild_id: int, word: str) -> bool:
        changed = await self.db.execute(
            "DELETE FROM banned_words WHERE guild_id = ? AND word = ?",
            (guild_id, word),
        )
        return changed > 0

    async def list(self, guild_id: int) -> list[str]:
        rows = await self.db.fetchall(
            "SELECT word FROM banned_words WHERE guild_id = ? ORDER BY LOWER(word)",
            (guild_id,),
        )
        return [str(row["word"]) for row in rows]

    async def clear(self, guild_id: int) -> int:
        return await self.db.execute("DELETE FROM banned_words WHERE guild_id = ?", (guild_id,))


# ---------------------------------------------------------------------------


class MemberRepo:
    _METRICS = {"xp", "loyalty", "messages"}

    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, guild_id: int, user_id: int) -> MemberStats | None:
        row = await self.db.fetchone(
            "SELECT * FROM members WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return _member_from_row(row)

    async def ensure(self, guild_id: int, user_id: int) -> MemberStats:
        stats = await self.get(guild_id, user_id)
        if stats is not None:
            return stats
        await self.db.execute(
            "INSERT OR IGNORE INTO members (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )
        return await self.get(guild_id, user_id) or MemberStats.empty(guild_id, user_id)

    async def save(self, stats: MemberStats) -> None:
        await self.db.execute(
            """
            INSERT INTO members (
                guild_id, user_id, xp, level, messages, loyalty,
                last_xp_at, last_daily_at, daily_streak
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET
                xp = excluded.xp,
                level = excluded.level,
                messages = excluded.messages,
                loyalty = excluded.loyalty,
                last_xp_at = excluded.last_xp_at,
                last_daily_at = excluded.last_daily_at,
                daily_streak = excluded.daily_streak
            """,
            (
                stats.guild_id,
                stats.user_id,
                stats.xp,
                stats.level,
                stats.messages,
                stats.loyalty,
                to_iso(stats.last_xp_at),
                to_iso(stats.last_daily_at),
                stats.daily_streak,
            ),
        )

    async def leaderboard(self, guild_id: int, metric: str = "xp", limit: int = 10) -> list[MemberStats]:
        metric = self._check_metric(metric)
        rows = await self.db.fetchall(
            f"SELECT * FROM members WHERE guild_id = ? ORDER BY {metric} DESC, user_id ASC LIMIT ?",
            (guild_id, limit),
        )
        return [s for s in (_member_from_row(row) for row in rows) if s is not None]

    async def rank_position(self, guild_id: int, user_id: int, metric: str = "xp") -> int | None:
        metric = self._check_metric(metric)
        row = await self.db.fetchone(
            f"SELECT {metric} AS value FROM members WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        if row is None:
            return None
        better = await self.db.fetchone(
            f"SELECT COUNT(*) AS c FROM members WHERE guild_id = ? AND {metric} > ?",
            (guild_id, row["value"]),
        )
        return int(better["c"]) + 1 if better else None

    async def count(self, guild_id: int) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM members WHERE guild_id = ?",
            (guild_id,),
        )
        return int(row["c"]) if row else 0

    @classmethod
    def _check_metric(cls, metric: str) -> str:
        if metric not in cls._METRICS:
            raise ValueError(f"Неизвестная метрика: {metric}")
        return metric


def _member_from_row(row: sqlite3.Row | None) -> MemberStats | None:
    if row is None:
        return None
    return MemberStats(
        guild_id=int(row["guild_id"]),
        user_id=int(row["user_id"]),
        xp=int(row["xp"]),
        level=int(row["level"]),
        messages=int(row["messages"]),
        loyalty=int(row["loyalty"]),
        last_xp_at=from_iso(row["last_xp_at"]),
        last_daily_at=from_iso(row["last_daily_at"]),
        daily_streak=int(row["daily_streak"]),
    )


# ---------------------------------------------------------------------------


class LevelRewardRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, guild_id: int, level: int, role_id: int) -> None:
        await self.db.execute(
            """
            INSERT INTO level_rewards (guild_id, level, role_id) VALUES (?, ?, ?)
            ON CONFLICT (guild_id, level) DO UPDATE SET role_id = excluded.role_id
            """,
            (guild_id, level, role_id),
        )

    async def remove(self, guild_id: int, level: int) -> bool:
        changed = await self.db.execute(
            "DELETE FROM level_rewards WHERE guild_id = ? AND level = ?",
            (guild_id, level),
        )
        return changed > 0

    async def list(self, guild_id: int) -> dict[int, int]:
        rows = await self.db.fetchall(
            "SELECT level, role_id FROM level_rewards WHERE guild_id = ? ORDER BY level ASC",
            (guild_id,),
        )
        return {int(row["level"]): int(row["role_id"]) for row in rows}


# ---------------------------------------------------------------------------
# Тикеты
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TicketPanel:
    id: int
    guild_id: int
    channel_id: int
    message_id: int | None
    title: str
    description: str
    category_id: int | None
    support_role_id: int | None
    created_at: datetime


@dataclass(slots=True)
class Ticket:
    id: int
    guild_id: int
    panel_id: int | None
    channel_id: int
    message_id: int | None
    user_id: int
    status: str
    claimed_by: int | None
    created_at: datetime
    closed_at: datetime | None
    closed_by: int | None


class TicketRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create_panel(
        self,
        guild_id: int,
        channel_id: int,
        title: str,
        description: str = "",
        category_id: int | None = None,
        support_role_id: int | None = None,
    ) -> int:
        return await self.db.insert(
            """
            INSERT INTO ticket_panels
                (guild_id, channel_id, message_id, title, description, category_id, support_role_id, created_at)
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (guild_id, channel_id, title, description, category_id, support_role_id, to_iso(utcnow())),
        )

    async def set_panel_message(self, panel_id: int, message_id: int | None) -> None:
        await self.db.execute(
            "UPDATE ticket_panels SET message_id = ? WHERE id = ?",
            (message_id, panel_id),
        )

    async def get_panel(self, panel_id: int) -> TicketPanel | None:
        row = await self.db.fetchone("SELECT * FROM ticket_panels WHERE id = ?", (panel_id,))
        return _ticket_panel_from_row(row)

    async def panels(self, guild_id: int) -> list[TicketPanel]:
        rows = await self.db.fetchall(
            "SELECT * FROM ticket_panels WHERE guild_id = ? ORDER BY id ASC",
            (guild_id,),
        )
        return [p for p in (_ticket_panel_from_row(row) for row in rows) if p is not None]

    async def all_panels(self) -> list[TicketPanel]:
        rows = await self.db.fetchall("SELECT * FROM ticket_panels ORDER BY id ASC")
        return [p for p in (_ticket_panel_from_row(row) for row in rows) if p is not None]

    async def delete_panel(self, panel_id: int) -> bool:
        changed = await self.db.execute("DELETE FROM ticket_panels WHERE id = ?", (panel_id,))
        return changed > 0

    async def create_ticket(
        self,
        guild_id: int,
        panel_id: int | None,
        channel_id: int,
        user_id: int,
    ) -> int:
        return await self.db.insert(
            """
            INSERT INTO tickets (guild_id, panel_id, channel_id, message_id, user_id, status, claimed_by, created_at)
            VALUES (?, ?, ?, NULL, ?, 'open', NULL, ?)
            """,
            (guild_id, panel_id, channel_id, user_id, to_iso(utcnow())),
        )

    async def set_ticket_message(self, ticket_id: int, message_id: int) -> None:
        await self.db.execute(
            "UPDATE tickets SET message_id = ? WHERE id = ?",
            (message_id, ticket_id),
        )

    async def get_ticket(self, ticket_id: int) -> Ticket | None:
        row = await self.db.fetchone("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        return _ticket_from_row(row)

    async def ticket_by_channel(self, channel_id: int) -> Ticket | None:
        row = await self.db.fetchone(
            "SELECT * FROM tickets WHERE channel_id = ? ORDER BY id DESC LIMIT 1",
            (channel_id,),
        )
        return _ticket_from_row(row)

    async def open_ticket_for(self, guild_id: int, user_id: int) -> Ticket | None:
        row = await self.db.fetchone(
            """
            SELECT * FROM tickets
            WHERE guild_id = ? AND user_id = ? AND status = 'open'
            ORDER BY id ASC LIMIT 1
            """,
            (guild_id, user_id),
        )
        return _ticket_from_row(row)

    async def open_tickets(self, guild_id: int) -> list[Ticket]:
        rows = await self.db.fetchall(
            "SELECT * FROM tickets WHERE guild_id = ? AND status = 'open' ORDER BY id ASC",
            (guild_id,),
        )
        return [t for t in (_ticket_from_row(row) for row in rows) if t is not None]

    async def all_open_tickets(self) -> list[Ticket]:
        rows = await self.db.fetchall("SELECT * FROM tickets WHERE status = 'open' ORDER BY id ASC")
        return [t for t in (_ticket_from_row(row) for row in rows) if t is not None]

    async def close_ticket(self, ticket_id: int, closed_by: int | None) -> bool:
        changed = await self.db.execute(
            """
            UPDATE tickets SET status = 'closed', closed_at = ?, closed_by = ?
            WHERE id = ? AND status = 'open'
            """,
            (to_iso(utcnow()), closed_by, ticket_id),
        )
        return changed > 0

    async def claim_ticket(self, ticket_id: int, user_id: int) -> None:
        await self.db.execute(
            "UPDATE tickets SET claimed_by = ? WHERE id = ? AND claimed_by IS NULL",
            (user_id, ticket_id),
        )


def _ticket_panel_from_row(row: Any | None) -> TicketPanel | None:
    if row is None:
        return None
    return TicketPanel(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        channel_id=int(row["channel_id"]),
        message_id=int(row["message_id"]) if row["message_id"] is not None else None,
        title=str(row["title"]),
        description=str(row["description"]),
        category_id=int(row["category_id"]) if row["category_id"] is not None else None,
        support_role_id=int(row["support_role_id"]) if row["support_role_id"] is not None else None,
        created_at=from_iso(row["created_at"]) or utcnow(),
    )


def _ticket_from_row(row: Any | None) -> Ticket | None:
    if row is None:
        return None
    return Ticket(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        panel_id=int(row["panel_id"]) if row["panel_id"] is not None else None,
        channel_id=int(row["channel_id"]),
        message_id=int(row["message_id"]) if row["message_id"] is not None else None,
        user_id=int(row["user_id"]),
        status=str(row["status"]),
        claimed_by=int(row["claimed_by"]) if row["claimed_by"] is not None else None,
        created_at=from_iso(row["created_at"]) or utcnow(),
        closed_at=from_iso(row["closed_at"]),
        closed_by=int(row["closed_by"]) if row["closed_by"] is not None else None,
    )


# ---------------------------------------------------------------------------
# Временные наказания
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TempAction:
    id: int
    guild_id: int
    user_id: int
    moderator_id: int | None
    action: str
    reason: str
    created_at: datetime
    expires_at: datetime
    active: bool


class TempActionRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int | None,
        action: str,
        reason: str,
        expires_at: datetime,
    ) -> int:
        return await self.db.insert(
            """
            INSERT INTO temp_actions
                (guild_id, user_id, moderator_id, action, reason, created_at, expires_at, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                guild_id,
                user_id,
                moderator_id,
                action,
                reason,
                to_iso(utcnow()),
                to_iso(expires_at),
            ),
        )

    async def get(self, action_id: int) -> TempAction | None:
        row = await self.db.fetchone("SELECT * FROM temp_actions WHERE id = ?", (action_id,))
        return _temp_action_from_row(row)

    async def due(self, now_iso: str) -> list[TempAction]:
        rows = await self.db.fetchall(
            """
            SELECT * FROM temp_actions
            WHERE active = 1 AND expires_at <= ?
            ORDER BY expires_at ASC
            """,
            (now_iso,),
        )
        return [a for a in (_temp_action_from_row(row) for row in rows) if a is not None]

    async def active_for_guild(self, guild_id: int) -> list[TempAction]:
        rows = await self.db.fetchall(
            """
            SELECT * FROM temp_actions
            WHERE guild_id = ? AND active = 1
            ORDER BY expires_at ASC
            """,
            (guild_id,),
        )
        return [a for a in (_temp_action_from_row(row) for row in rows) if a is not None]

    async def deactivate(self, action_id: int) -> bool:
        changed = await self.db.execute(
            "UPDATE temp_actions SET active = 0 WHERE id = ? AND active = 1",
            (action_id,),
        )
        return changed > 0


def _temp_action_from_row(row: Any | None) -> TempAction | None:
    if row is None:
        return None
    return TempAction(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        user_id=int(row["user_id"]),
        moderator_id=int(row["moderator_id"]) if row["moderator_id"] is not None else None,
        action=str(row["action"]),
        reason=str(row["reason"]),
        created_at=from_iso(row["created_at"]) or utcnow(),
        expires_at=from_iso(row["expires_at"]) or utcnow(),
        active=bool(row["active"]),
    )


# ---------------------------------------------------------------------------
# Апелляции
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Appeal:
    id: int
    guild_id: int
    warning_id: int
    user_id: int
    reason: str
    status: str
    channel_id: int | None
    message_id: int | None
    created_at: datetime
    reviewed_by: int | None
    reviewed_at: datetime | None


class AppealRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, guild_id: int, warning_id: int, user_id: int, reason: str) -> int:
        return await self.db.insert(
            """
            INSERT INTO appeals (guild_id, warning_id, user_id, reason, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (guild_id, warning_id, user_id, reason, to_iso(utcnow())),
        )

    async def get(self, appeal_id: int) -> Appeal | None:
        row = await self.db.fetchone("SELECT * FROM appeals WHERE id = ?", (appeal_id,))
        return _appeal_from_row(row)

    async def by_message(self, message_id: int) -> Appeal | None:
        row = await self.db.fetchone(
            "SELECT * FROM appeals WHERE message_id = ? ORDER BY id DESC LIMIT 1",
            (message_id,),
        )
        return _appeal_from_row(row)

    async def pending(self, guild_id: int) -> list[Appeal]:
        rows = await self.db.fetchall(
            "SELECT * FROM appeals WHERE guild_id = ? AND status = 'pending' ORDER BY id ASC",
            (guild_id,),
        )
        return [a for a in (_appeal_from_row(row) for row in rows) if a is not None]

    async def all_pending(self) -> list[Appeal]:
        rows = await self.db.fetchall("SELECT * FROM appeals WHERE status = 'pending' ORDER BY id ASC")
        return [a for a in (_appeal_from_row(row) for row in rows) if a is not None]

    async def pending_for_warning(self, warning_id: int) -> Appeal | None:
        row = await self.db.fetchone(
            "SELECT * FROM appeals WHERE warning_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (warning_id,),
        )
        return _appeal_from_row(row)

    async def set_message(self, appeal_id: int, channel_id: int, message_id: int) -> None:
        await self.db.execute(
            "UPDATE appeals SET channel_id = ?, message_id = ? WHERE id = ?",
            (channel_id, message_id, appeal_id),
        )

    async def set_status(self, appeal_id: int, status: str, reviewed_by: int | None) -> bool:
        changed = await self.db.execute(
            """
            UPDATE appeals SET status = ?, reviewed_by = ?, reviewed_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (status, reviewed_by, to_iso(utcnow()), appeal_id),
        )
        return changed > 0


def _appeal_from_row(row: Any | None) -> Appeal | None:
    if row is None:
        return None
    return Appeal(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        warning_id=int(row["warning_id"]),
        user_id=int(row["user_id"]),
        reason=str(row["reason"]),
        status=str(row["status"]),
        channel_id=int(row["channel_id"]) if row["channel_id"] is not None else None,
        message_id=int(row["message_id"]) if row["message_id"] is not None else None,
        created_at=from_iso(row["created_at"]) or utcnow(),
        reviewed_by=int(row["reviewed_by"]) if row["reviewed_by"] is not None else None,
        reviewed_at=from_iso(row["reviewed_at"]),
    )


# ---------------------------------------------------------------------------
# Розыгрыши
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Giveaway:
    id: int
    guild_id: int
    channel_id: int
    message_id: int | None
    prize: str
    winners_count: int
    host_id: int | None
    required_role_id: int | None
    ends_at: datetime
    ended: bool
    created_at: datetime


class GiveawayRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        guild_id: int,
        channel_id: int,
        prize: str,
        winners_count: int,
        host_id: int | None,
        required_role_id: int | None,
        ends_at: datetime,
    ) -> int:
        return await self.db.insert(
            """
            INSERT INTO giveaways
                (guild_id, channel_id, message_id, prize, winners_count, host_id,
                 required_role_id, ends_at, ended, created_at)
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                guild_id,
                channel_id,
                prize,
                winners_count,
                host_id,
                required_role_id,
                to_iso(ends_at),
                to_iso(utcnow()),
            ),
        )

    async def set_message(self, giveaway_id: int, message_id: int) -> None:
        await self.db.execute(
            "UPDATE giveaways SET message_id = ? WHERE id = ?",
            (message_id, giveaway_id),
        )

    async def get(self, giveaway_id: int) -> Giveaway | None:
        row = await self.db.fetchone("SELECT * FROM giveaways WHERE id = ?", (giveaway_id,))
        return _giveaway_from_row(row)

    async def active(self, guild_id: int) -> list[Giveaway]:
        rows = await self.db.fetchall(
            "SELECT * FROM giveaways WHERE guild_id = ? AND ended = 0 ORDER BY id ASC",
            (guild_id,),
        )
        return [g for g in (_giveaway_from_row(row) for row in rows) if g is not None]

    async def all_active(self) -> list[Giveaway]:
        rows = await self.db.fetchall("SELECT * FROM giveaways WHERE ended = 0 ORDER BY id ASC")
        return [g for g in (_giveaway_from_row(row) for row in rows) if g is not None]

    async def due(self, now_iso: str) -> list[Giveaway]:
        rows = await self.db.fetchall(
            "SELECT * FROM giveaways WHERE ended = 0 AND ends_at <= ? ORDER BY ends_at ASC",
            (now_iso,),
        )
        return [g for g in (_giveaway_from_row(row) for row in rows) if g is not None]

    async def finish(self, giveaway_id: int) -> bool:
        changed = await self.db.execute(
            "UPDATE giveaways SET ended = 1 WHERE id = ? AND ended = 0",
            (giveaway_id,),
        )
        return changed > 0

    async def add_entry(self, giveaway_id: int, user_id: int) -> bool:
        changed = await self.db.execute(
            """
            INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id, created_at)
            VALUES (?, ?, ?)
            """,
            (giveaway_id, user_id, to_iso(utcnow())),
        )
        return changed > 0

    async def remove_entry(self, giveaway_id: int, user_id: int) -> bool:
        changed = await self.db.execute(
            "DELETE FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?",
            (giveaway_id, user_id),
        )
        return changed > 0

    async def has_entry(self, giveaway_id: int, user_id: int) -> bool:
        row = await self.db.fetchone(
            "SELECT 1 AS ok FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?",
            (giveaway_id, user_id),
        )
        return row is not None

    async def entries(self, giveaway_id: int) -> list[int]:
        rows = await self.db.fetchall(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id = ? ORDER BY created_at ASC",
            (giveaway_id,),
        )
        return [int(row["user_id"]) for row in rows]

    async def entry_count(self, giveaway_id: int) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM giveaway_entries WHERE giveaway_id = ?",
            (giveaway_id,),
        )
        return int(row["c"]) if row else 0


def _giveaway_from_row(row: Any | None) -> Giveaway | None:
    if row is None:
        return None
    return Giveaway(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        channel_id=int(row["channel_id"]),
        message_id=int(row["message_id"]) if row["message_id"] is not None else None,
        prize=str(row["prize"]),
        winners_count=int(row["winners_count"]),
        host_id=int(row["host_id"]) if row["host_id"] is not None else None,
        required_role_id=int(row["required_role_id"]) if row["required_role_id"] is not None else None,
        ends_at=from_iso(row["ends_at"]) or utcnow(),
        ended=bool(row["ended"]),
        created_at=from_iso(row["created_at"]) or utcnow(),
    )


# ---------------------------------------------------------------------------
# Статистика в каналах
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StatsChannel:
    id: int
    guild_id: int
    channel_id: int
    kind: str
    template: str


class StatsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def upsert(
        self,
        guild_id: int,
        channel_id: int,
        kind: str,
        template: str,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO stats_channels (guild_id, channel_id, kind, template)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (guild_id, channel_id) DO UPDATE SET
                kind = excluded.kind,
                template = excluded.template
            """,
            (guild_id, channel_id, kind, template),
        )

    async def remove(self, guild_id: int, channel_id: int) -> bool:
        changed = await self.db.execute(
            "DELETE FROM stats_channels WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        )
        return changed > 0

    async def list(self, guild_id: int) -> list[StatsChannel]:
        rows = await self.db.fetchall(
            "SELECT * FROM stats_channels WHERE guild_id = ? ORDER BY id ASC",
            (guild_id,),
        )
        return [
            StatsChannel(
                id=int(row["id"]),
                guild_id=int(row["guild_id"]),
                channel_id=int(row["channel_id"]),
                kind=str(row["kind"]),
                template=str(row["template"]),
            )
            for row in rows
        ]

    async def all(self) -> list[StatsChannel]:
        rows = await self.db.fetchall("SELECT * FROM stats_channels ORDER BY id ASC")
        return [
            StatsChannel(
                id=int(row["id"]),
                guild_id=int(row["guild_id"]),
                channel_id=int(row["channel_id"]),
                kind=str(row["kind"]),
                template=str(row["template"]),
            )
            for row in rows
        ]


# ---------------------------------------------------------------------------
# Магазин лояльности
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ShopItem:
    id: int
    guild_id: int
    role_id: int
    name: str
    description: str
    price: int
    stock: int | None
    created_at: datetime


class ShopRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(
        self,
        guild_id: int,
        role_id: int,
        name: str,
        price: int,
        description: str = "",
        stock: int | None = None,
    ) -> int:
        return await self.db.insert(
            """
            INSERT INTO shop_items (guild_id, role_id, name, description, price, stock, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, role_id, name, description, price, stock, to_iso(utcnow())),
        )

    async def get(self, item_id: int) -> ShopItem | None:
        row = await self.db.fetchone("SELECT * FROM shop_items WHERE id = ?", (item_id,))
        return _shop_item_from_row(row)

    async def list(self, guild_id: int) -> list[ShopItem]:
        rows = await self.db.fetchall(
            "SELECT * FROM shop_items WHERE guild_id = ? ORDER BY price ASC, id ASC",
            (guild_id,),
        )
        return [i for i in (_shop_item_from_row(row) for row in rows) if i is not None]

    async def remove(self, guild_id: int, item_id: int) -> bool:
        changed = await self.db.execute(
            "DELETE FROM shop_items WHERE guild_id = ? AND id = ?",
            (guild_id, item_id),
        )
        return changed > 0

    async def decrement_stock(self, item_id: int) -> bool:
        changed = await self.db.execute(
            """
            UPDATE shop_items SET stock = stock - 1
            WHERE id = ? AND stock IS NOT NULL AND stock > 0
            """,
            (item_id,),
        )
        return changed > 0

    async def record_purchase(self, guild_id: int, user_id: int, item_id: int, price: int) -> None:
        await self.db.execute(
            """
            INSERT INTO shop_purchases (guild_id, user_id, item_id, price, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, item_id, price, to_iso(utcnow())),
        )


def _shop_item_from_row(row: Any | None) -> ShopItem | None:
    if row is None:
        return None
    return ShopItem(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        role_id=int(row["role_id"]),
        name=str(row["name"]),
        description=str(row["description"]),
        price=int(row["price"]),
        stock=int(row["stock"]) if row["stock"] is not None else None,
        created_at=from_iso(row["created_at"]) or utcnow(),
    )


# ---------------------------------------------------------------------------
# Капча
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CaptchaPending:
    guild_id: int
    user_id: int
    code: str
    created_at: datetime
    expires_at: datetime


class CaptchaRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def upsert(self, guild_id: int, user_id: int, code: str, expires_at: datetime) -> None:
        await self.db.execute(
            """
            INSERT INTO captcha_pending (guild_id, user_id, code, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET
                code = excluded.code,
                created_at = excluded.created_at,
                expires_at = excluded.expires_at
            """,
            (guild_id, user_id, code, to_iso(utcnow()), to_iso(expires_at)),
        )

    async def get(self, guild_id: int, user_id: int) -> CaptchaPending | None:
        row = await self.db.fetchone(
            "SELECT * FROM captcha_pending WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return _captcha_from_row(row)

    async def delete(self, guild_id: int, user_id: int) -> None:
        await self.db.execute(
            "DELETE FROM captcha_pending WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )

    async def due(self, now_iso: str) -> list[CaptchaPending]:
        rows = await self.db.fetchall(
            "SELECT * FROM captcha_pending WHERE expires_at <= ? ORDER BY expires_at ASC",
            (now_iso,),
        )
        return [c for c in (_captcha_from_row(row) for row in rows) if c is not None]

    async def all(self) -> list[CaptchaPending]:
        rows = await self.db.fetchall("SELECT * FROM captcha_pending ORDER BY expires_at ASC")
        return [c for c in (_captcha_from_row(row) for row in rows) if c is not None]


def _captcha_from_row(row: Any | None) -> CaptchaPending | None:
    if row is None:
        return None
    return CaptchaPending(
        guild_id=int(row["guild_id"]),
        user_id=int(row["user_id"]),
        code=str(row["code"]),
        created_at=from_iso(row["created_at"]) or utcnow(),
        expires_at=from_iso(row["expires_at"]) or utcnow(),
    )


# ---------------------------------------------------------------------------
# Доступ к командам
# ---------------------------------------------------------------------------


class CommandAccessRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, guild_id: int, command: str) -> CommandRule | None:
        row = await self.db.fetchone(
            "SELECT * FROM command_access WHERE guild_id = ? AND command = ?",
            (guild_id, normalize_command(command)),
        )
        return _command_rule_from_row(row)

    async def rules(self, guild_id: int) -> dict[str, CommandRule]:
        rows = await self.db.fetchall(
            "SELECT * FROM command_access WHERE guild_id = ? ORDER BY command ASC",
            (guild_id,),
        )
        result: dict[str, CommandRule] = {}
        for row in rows:
            rule = _command_rule_from_row(row)
            if rule is not None:
                result[rule.command] = rule
        return result

    async def save(self, rule: CommandRule) -> None:
        command = normalize_command(rule.command)
        await self.db.execute(
            """
            INSERT INTO command_access
                (guild_id, command, allow_roles, allow_users, deny_roles, deny_users, enabled, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (guild_id, command) DO UPDATE SET
                allow_roles = excluded.allow_roles,
                allow_users = excluded.allow_users,
                deny_roles = excluded.deny_roles,
                deny_users = excluded.deny_users,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                rule.guild_id,
                command,
                json.dumps(sorted(int(value) for value in rule.allow_roles)),
                json.dumps(sorted(int(value) for value in rule.allow_users)),
                json.dumps(sorted(int(value) for value in rule.deny_roles)),
                json.dumps(sorted(int(value) for value in rule.deny_users)),
                1 if rule.enabled else 0,
                to_iso(utcnow()),
            ),
        )

    async def delete(self, guild_id: int, command: str) -> bool:
        changed = await self.db.execute(
            "DELETE FROM command_access WHERE guild_id = ? AND command = ?",
            (guild_id, normalize_command(command)),
        )
        return changed > 0

    async def delete_all(self, guild_id: int) -> int:
        return await self.db.execute("DELETE FROM command_access WHERE guild_id = ?", (guild_id,))

    async def migrate_legacy(self) -> None:
        """Переносит правила временной версии с группами на прежние имена команд."""

        for old, new in LEGACY_COMMANDS.items():
            rows = await self.db.fetchall(
                "SELECT guild_id FROM command_access WHERE command = ?",
                (old,),
            )
            for row in rows:
                guild_id = int(row["guild_id"])
                if await self.get(guild_id, new) is not None:
                    await self.delete(guild_id, old)
                else:
                    await self.db.execute(
                        "UPDATE command_access SET command = ? WHERE guild_id = ? AND command = ?",
                        (new, guild_id, old),
                    )

        for group, targets in LEGACY_GROUPS.items():
            rows = await self.db.fetchall(
                "SELECT guild_id FROM command_access WHERE command = ?",
                (group,),
            )
            for row in rows:
                guild_id = int(row["guild_id"])
                rule = await self.get(guild_id, group)
                if rule is not None:
                    for target in targets:
                        if await self.get(guild_id, target) is None:
                            await self.save(
                                CommandRule(
                                    guild_id=guild_id,
                                    command=target,
                                    allow_roles=set(rule.allow_roles),
                                    allow_users=set(rule.allow_users),
                                    deny_roles=set(rule.deny_roles),
                                    deny_users=set(rule.deny_users),
                                    enabled=rule.enabled,
                                )
                            )
                await self.delete(guild_id, group)


def _command_rule_from_row(row: Any | None) -> CommandRule | None:
    if row is None:
        return None

    def int_set(value: Any) -> set[int]:
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
            return {int(item) for item in parsed or []}
        except (TypeError, ValueError):
            return set()

    return CommandRule(
        guild_id=int(row["guild_id"]),
        command=str(row["command"]),
        allow_roles=int_set(row["allow_roles"]),
        allow_users=int_set(row["allow_users"]),
        deny_roles=int_set(row["deny_roles"]),
        deny_users=int_set(row["deny_users"]),
        enabled=bool(row["enabled"]),
    )


# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Repositories:
    warnings: WarningRepo
    autoroles: AutoroleRepo
    selfroles: SelfRoleRepo
    words: WordRepo
    members: MemberRepo
    level_rewards: LevelRewardRepo
    tickets: TicketRepo
    temp_actions: TempActionRepo
    appeals: AppealRepo
    giveaways: GiveawayRepo
    stats: StatsRepo
    shop: ShopRepo
    captcha: CaptchaRepo
    access: CommandAccessRepo


def build_repositories(db: Database) -> Repositories:
    return Repositories(
        warnings=WarningRepo(db),
        autoroles=AutoroleRepo(db),
        selfroles=SelfRoleRepo(db),
        words=WordRepo(db),
        members=MemberRepo(db),
        level_rewards=LevelRewardRepo(db),
        tickets=TicketRepo(db),
        temp_actions=TempActionRepo(db),
        appeals=AppealRepo(db),
        giveaways=GiveawayRepo(db),
        stats=StatsRepo(db),
        shop=ShopRepo(db),
        captcha=CaptchaRepo(db),
        access=CommandAccessRepo(db),
    )

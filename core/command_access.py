"""Правила доступа к командам по ролям и ID пользователей.

Правила хранятся по «пути» команды: `levelconfig` действует на всю группу,
`levelconfig add-xp` — только на конкретную подкоманду. Проверка идёт по всем
совпавшим уровням (родитель + сама команда), поэтому ограничения складываются.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


@dataclass(slots=True)
class CommandRule:
    guild_id: int
    command: str
    allow_roles: set[int] = field(default_factory=set)
    allow_users: set[int] = field(default_factory=set)
    deny_roles: set[int] = field(default_factory=set)
    deny_users: set[int] = field(default_factory=set)
    enabled: bool = True

    def is_empty(self) -> bool:
        return not (
            self.allow_roles
            or self.allow_users
            or self.deny_roles
            or self.deny_users
            or not self.enabled
        )


def normalize_command(value: str) -> str:
    text = str(value).strip().lower().lstrip("/")
    return " ".join(part for part in text.split() if part)


def command_chain(command: str) -> list[str]:
    """Возвращает путь от корня до команды: levelconfig -> levelconfig add-xp."""

    parts = normalize_command(command).split()
    return [" ".join(parts[: index + 1]) for index in range(len(parts))]


def command_path_from_data(data: Mapping[str, Any] | None) -> str:
    """Собирает `levelconfig add-xp` из payload interaction.data.

    discord.py вызывает глобальный interaction_check до того, как выставит
    interaction.command, поэтому путь берём из сырых данных.
    """

    if not isinstance(data, Mapping):
        return ""
    name = data.get("name")
    if not isinstance(name, str) or not name:
        return ""

    parts = [name]
    options = data.get("options")
    while isinstance(options, list):
        subgroup = next(
            (
                option
                for option in options
                if isinstance(option, Mapping) and option.get("type") in {1, 2}
            ),
            None,
        )
        if subgroup is None:
            break
        sub_name = subgroup.get("name")
        if not isinstance(sub_name, str) or not sub_name:
            break
        parts.append(sub_name)
        options = subgroup.get("options")

    return normalize_command(" ".join(parts))


def evaluate_access(
    rules: Mapping[str, CommandRule],
    command: str,
    *,
    user_id: int,
    role_ids: Iterable[int],
    is_admin: bool = False,
    is_owner: bool = False,
    bypass_admins: bool = True,
) -> tuple[bool, str | None]:
    """Проверяет доступ. Возвращает (разрешено, причина_отказа)."""

    if is_owner or (bypass_admins and is_admin):
        return True, None

    roles = {int(role_id) for role_id in role_ids}
    matched = [rules[part] for part in command_chain(command) if part in rules]
    if not matched:
        return True, None

    for rule in matched:
        if not rule.enabled:
            return False, "disabled"
        if int(user_id) in rule.deny_users or roles & rule.deny_roles:
            return False, "denied"
        if rule.allow_roles or rule.allow_users:
            allowed = int(user_id) in rule.allow_users or bool(roles & rule.allow_roles)
            if not allowed:
                return False, "not_allowed"

    return True, None


REASON_MESSAGES = {
    "disabled": "Эта команда отключена на сервере.",
    "denied": "Вам запрещён доступ к этой команде.",
    "not_allowed": "Команда доступна только определённым ролям или пользователям.",
}

# Пути, которые временно существовали после группировки, -> старые имена.
# Нужно, чтобы правила, созданные в той версии, продолжили работать.
LEGACY_COMMANDS = {
    "mod warn": "warn",
    "mod warnings": "warnings",
    "mod unwarn": "unwarn",
    "mod clearwarnings": "clearwarnings",
    "levels rank": "rank",
    "levels leaderboard": "leaderboard",
    "levels daily": "daily",
    "shop buy": "buy",
    "shop balance": "balance",
    "appeals submit": "appeal",
    "utility help": "help",
    "utility ping": "ping",
    "utility botinfo": "botinfo",
}

# Группы, которые существовали только в версии с группировкой.
LEGACY_GROUPS = {
    "mod": ("warn", "warnings", "unwarn", "clearwarnings"),
    "levels": ("rank", "leaderboard", "daily"),
    "utility": ("help", "ping", "botinfo"),
}

# Готовые списки команд для быстрой выдачи доступа одной ролью.
COMMAND_PRESETS: dict[str, tuple[str, ...]] = {
    "player": (
        "rank",
        "leaderboard",
        "daily",
        "shop list",
        "buy",
        "balance",
        "appeal",
        "help",
        "ping",
        "botinfo",
    ),
    "moderator": (
        "warn",
        "warnings",
        "unwarn",
        "clearwarnings",
        "tempactions ban",
        "tempactions mute",
        "tempactions list",
        "tempactions cancel",
        "ticket close",
        "ticket add",
        "ticket remove",
        "appeals list",
    ),
    "admin": (
        "logs",
        "autorole",
        "selfrole",
        "automod",
        "warnconfig",
        "levelconfig",
        "ticket setup",
        "ticket delete",
        "ticket log",
        "ticket panels",
        "antiraid",
        "antispam",
        "captcha",
        "stats",
        "giveaway",
        "shop add",
        "shop remove",
        "settings",
    ),
}

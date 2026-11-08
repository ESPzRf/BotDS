"""Статический каталог команд для выпадающих списков дашборда."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PATH = Path(__file__).with_name("commands.json")


def load_commands() -> list[dict[str, Any]]:
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("name")]


COMMANDS: list[dict[str, Any]] = load_commands()
COMMAND_NAMES: set[str] = {str(item["name"]) for item in COMMANDS}
COMMANDS_BY_GROUP: dict[str, list[dict[str, Any]]] = {}
for _item in COMMANDS:
    COMMANDS_BY_GROUP.setdefault(str(_item.get("group") or _item["name"]), []).append(_item)

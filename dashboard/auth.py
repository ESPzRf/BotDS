"""Сессии, CSRF и проверка прав Discord для дашборда."""

from __future__ import annotations

import secrets
from typing import Any
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse

ADMINISTRATOR = 0x8
MANAGE_GUILD = 0x20


def session_user(request: Request) -> dict[str, Any] | None:
    user = request.session.get("user")
    return user if isinstance(user, dict) else None


def login_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)


def has_guild_access(user: dict[str, Any], guild_id: int) -> bool:
    for guild in user.get("guilds", []):
        if int(guild.get("id", 0)) == guild_id:
            return bool(guild.get("allowed", True))
    return False


def can_manage_guild(permissions: int | str | None) -> bool:
    try:
        bits = int(permissions or 0)
    except (TypeError, ValueError):
        return False
    return bool(bits & ADMINISTRATOR or bits & MANAGE_GUILD)


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf"] = token
    return token


def verify_csrf(request: Request, provided: str | None) -> bool:
    expected = request.session.get("csrf")
    return bool(
        isinstance(expected, str)
        and isinstance(provided, str)
        and secrets.compare_digest(expected, provided)
    )


def flash(request: Request, message: str, kind: str = "success") -> None:
    items = request.session.setdefault("flashes", [])
    if not isinstance(items, list):
        items = []
        request.session["flashes"] = items
    items.append({"message": message, "kind": kind})


def pop_flashes(request: Request) -> list[dict[str, str]]:
    items = request.session.pop("flashes", [])
    return items if isinstance(items, list) else []

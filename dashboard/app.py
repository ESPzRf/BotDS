"""FastAPI-приложение веб-дашборда.

Запуск отдельного сервиса:
    python dashboard.py
или:
    uvicorn dashboard.app:create_app --factory --port 8080
"""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from core.command_access import COMMAND_PRESETS, CommandRule, normalize_command
from core.database import Database
from core.repositories import Repositories, build_repositories
from core.settings import GuildSettingsStore
from dashboard.auth import (
    can_manage_guild,
    csrf_token,
    flash,
    has_guild_access,
    login_redirect,
    pop_flashes,
    session_user,
    verify_csrf,
)
from dashboard.config import DashboardConfig
from dashboard.commands import COMMANDS_BY_GROUP, COMMAND_NAMES
from dashboard.discord_api import DiscordAPI, DiscordAPIError
from dashboard.schema import SECTION_BY_SLUG, SECTIONS, apply_section, dangerous_role_label

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _channel_label(channel: dict[str, Any]) -> str:
    kind = int(channel.get("type", 0))
    if kind == 4:
        return f"📁 {channel.get('name', '')}"
    if kind == 2:
        return f"🔊 {channel.get('name', '')}"
    return f"# {channel.get('name', '')}"


templates.env.filters["channel_label"] = _channel_label


def _role_color(role: dict[str, Any]) -> str:
    color = int(role.get("color", 0))
    return f"#{color:06x}" if color else "#99aab5"


templates.env.filters["role_color"] = _role_color


def _guild_icon(guild: dict[str, Any], size: int = 128) -> str:
    icon = guild.get("icon")
    if not icon:
        return ""
    return f"https://cdn.discordapp.com/icons/{guild.get('id')}/{icon}.png?size={size}"


templates.env.filters["guild_icon"] = _guild_icon

TOGGLE_SETTINGS = {
    "autorole_enabled": "autorole_enabled",
    "automod_enabled": "automod_enabled",
    "levels_enabled": "levels_enabled",
    "antiraid_enabled": "antiraid_enabled",
    "antispam_enabled": "antispam_enabled",
    "captcha_enabled": "captcha_enabled",
    "stats_enabled": "stats_enabled",
}


def _selectable_channels(channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in channels if int(item.get("type", 0)) in {0, 5}]


def _stat_channels(channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in channels if int(item.get("type", 0)) in {0, 2, 5, 13}]


def _selectable_roles(roles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [
        role
        for role in roles
        if not role.get("managed")
        and role.get("name") != "@everyone"
        and not int(role.get("id", 0)) == 0
    ]
    return sorted(filtered, key=lambda item: int(item.get("position", 0)), reverse=True)


def _member_permissions(
    guild: dict[str, Any],
    roles: list[dict[str, Any]],
    member: dict[str, Any],
) -> int:
    if int(guild.get("owner_id", 0)) == int(member["user"]["id"]):
        return 1 << 3
    role_ids = {int(item) for item in member.get("roles", [])}
    bits = 0
    for role in roles:
        if role.get("name") == "@everyone" or int(role.get("id", 0)) in role_ids:
            try:
                bits |= int(str(role.get("permissions", 0)))
            except (TypeError, ValueError):
                continue
    return bits


def create_app(config: DashboardConfig | None = None) -> FastAPI:
    cfg = config or DashboardConfig.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(cfg.db_target)
        await db.connect()
        app.state.db = db
        app.state.repos = build_repositories(db)
        app.state.settings = GuildSettingsStore(db)
        app.state.api = DiscordAPI(cfg.bot_token)
        try:
            yield
        finally:
            await app.state.api.close()
            await db.close()

    app = FastAPI(title="Discord Moderation Dashboard", lifespan=lifespan)
    app.state.config = cfg
    app.add_middleware(
        SessionMiddleware,
        secret_key=cfg.secret,
        same_site="lax",
        https_only=cfg.base_url.startswith("https://"),
        max_age=60 * 60 * 12,
    )
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    def render(
        request: Request,
        template: str,
        status_code: int = 200,
        **context: Any,
    ) -> HTMLResponse:
        context.setdefault("user", session_user(request))
        context.setdefault("flashes", pop_flashes(request))
        context.setdefault("csrf_token", csrf_token(request))
        context.setdefault("config", cfg)
        return templates.TemplateResponse(request, template, context, status_code=status_code)

    def error_page(request: Request, message: str, status: int = 400) -> HTMLResponse:
        return render(request, "error.html", status_code=status, message=message)

    async def load_guild(
        request: Request, guild_id: int
    ) -> tuple[dict[str, Any] | None, Response | None]:
        user = session_user(request)
        if user is None:
            return None, login_redirect(request)
        if not has_guild_access(user, guild_id):
            return None, error_page(request, "У вас нет доступа к этому серверу.", 403)
        try:
            guild = await request.app.state.api.guild(guild_id)
            channels = await request.app.state.api.guild_channels(guild_id)
            roles = await request.app.state.api.guild_roles(guild_id)
            user_id = int(user.get("id", 0))
            member = None
            permissions = (1 << 3) | (1 << 5)
            if not (cfg.dev_mode and user_id == cfg.dev_user_id):
                member = await request.app.state.api.guild_member(guild_id, user_id)
                permissions = _member_permissions(guild, roles, member)
                if not permissions & ((1 << 3) | (1 << 5)):
                    return None, error_page(
                        request,
                        "У вас больше нет права «Управлять сервером» на этом сервере.",
                        403,
                    )
        except DiscordAPIError as exc:
            if exc.status == 404:
                return None, error_page(
                    request,
                    "Вы больше не участник этого сервера или бот не имеет к нему доступа.",
                    403,
                )
            return None, error_page(
                request,
                f"Не удалось получить данные сервера из Discord: {exc}",
                502,
            )
        except Exception as exc:  # noqa: BLE001 - сеть/таймаут Discord
            log.exception("Ошибка при загрузке сервера %s", guild_id)
            return None, error_page(
                request,
                f"Ошибка подключения к Discord: {exc}",
                502,
            )
        return (
            {
                "guild": guild,
                "channels": _selectable_channels(channels),
                "stat_channels": _stat_channels(channels),
                "roles": _selectable_roles(roles),
                "all_channels": channels,
                "member": member,
                "permissions": permissions,
            },
            None,
        )

    def guild_context(
        request: Request,
        data: dict[str, Any],
        active: str,
        **extra: Any,
    ) -> dict[str, Any]:
        context = {
            "guild": data["guild"],
            "channels": data["channels"],
            "stat_channels": data["stat_channels"],
            "roles": data["roles"],
            "role_map": {int(role["id"]): role for role in data["roles"]},
            "channel_map": {int(channel["id"]): channel for channel in data["channels"]},
            "stat_channel_map": {int(channel["id"]): channel for channel in data["stat_channels"]},
            "active": active,
            "section_links": SECTIONS,
            "permissions": data.get("permissions", 0),
            "guilds": [
                item for item in (session_user(request) or {}).get("guilds", [])
                if item.get("allowed")
            ],
        }
        context.update(extra)
        return context

    # ------------------------------------------------------------------ auth

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Response:
        if session_user(request) is None:
            return RedirectResponse("/login", status_code=303)
        return RedirectResponse("/guilds", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login(request: Request) -> Response:
        if session_user(request) is not None:
            return RedirectResponse("/guilds", status_code=303)
        return render(
            request,
            "login.html",
            oauth_configured=cfg.oauth_configured,
            dev_mode=cfg.dev_mode,
            next=request.query_params.get("next", "/guilds"),
        )

    @app.get("/auth/discord")
    async def auth_discord(request: Request) -> Response:
        if not cfg.oauth_configured:
            return error_page(request, "OAuth не настроен: задайте DISCORD_CLIENT_ID и DISCORD_CLIENT_SECRET.", 503)
        state = secrets.token_urlsafe(24)
        request.session["oauth_state"] = state
        params = urlencode(
            {
                "client_id": cfg.client_id,
                "redirect_uri": cfg.redirect_uri,
                "response_type": "code",
                "scope": "identify guilds",
                "state": state,
                "prompt": "consent",
            }
        )
        return RedirectResponse(f"https://discord.com/oauth2/authorize?{params}", status_code=303)

    @app.get("/auth/callback")
    async def auth_callback(request: Request) -> Response:
        if "error" in request.query_params:
            return error_page(request, "Вход через Discord отменён.", 400)
        state = request.query_params.get("state")
        expected = request.session.pop("oauth_state", None)
        if not state or state != expected:
            return error_page(request, "Неверный OAuth state. Попробуйте войти снова.", 400)
        code = request.query_params.get("code")
        if not code:
            return error_page(request, "Discord не вернул код авторизации.", 400)
        try:
            token = await request.app.state.api.exchange_code(
                code, cfg.client_id, cfg.client_secret, cfg.redirect_uri
            )
            access_token = token["access_token"]
            me = await request.app.state.api.oauth_me(access_token)
            guilds = await request.app.state.api.oauth_guilds(access_token)
        except (DiscordAPIError, KeyError) as exc:
            log.warning("OAuth ошибка: %s", exc)
            return error_page(request, "Не удалось войти через Discord. Попробуйте ещё раз.", 502)
        except Exception as exc:  # noqa: BLE001 - сеть/таймаут
            log.exception("OAuth ошибка подключения")
            return error_page(request, f"Ошибка подключения к Discord: {exc}", 502)

        allowed = []
        for item in guilds:
            if can_manage_guild(item.get("permissions")):
                allowed.append(
                    {
                        "id": str(item["id"]),
                        "name": item.get("name", "Сервер"),
                        "icon": item.get("icon"),
                        "allowed": True,
                    }
                )
        request.session["user"] = {
            "id": str(me["id"]),
            "username": me.get("global_name") or me.get("username") or "Пользователь",
            "avatar": me.get("avatar"),
            "guilds": allowed,
        }
        next_url = request.query_params.get("next", "/guilds")
        if not next_url.startswith("/"):
            next_url = "/guilds"
        return RedirectResponse(next_url, status_code=303)

    @app.get("/auth/dev")
    async def auth_dev(request: Request) -> Response:
        if not cfg.dev_mode or not cfg.dev_guild_id:
            return error_page(request, "Dev-вход выключен.", 403)
        try:
            guild = await request.app.state.api.guild(cfg.dev_guild_id)
            name = guild.get("name", "Dev Guild")
        except DiscordAPIError:
            name = "Dev Guild"
        request.session["user"] = {
            "id": str(cfg.dev_user_id or 0),
            "username": "Dev Administrator",
            "avatar": None,
            "guilds": [
                {
                    "id": str(cfg.dev_guild_id),
                    "name": name,
                    "icon": None,
                    "allowed": True,
                }
            ],
        }
        return RedirectResponse("/guilds", status_code=303)

    @app.get("/logout")
    async def logout(request: Request) -> Response:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/guilds", response_class=HTMLResponse)
    async def guilds_page(request: Request) -> Response:
        user = session_user(request)
        if user is None:
            return login_redirect(request)
        guilds = [g for g in user.get("guilds", []) if g.get("allowed")]
        return render(request, "guilds.html", guilds=guilds)

    # -------------------------------------------------------------- overview

    @app.get("/g/{guild_id}", response_class=HTMLResponse)
    async def overview(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        repos: Repositories = request.app.state.repos
        db: Database = request.app.state.db
        counts = {
            "autoroles": len(await repos.autoroles.list(guild_id)),
            "words": len(await repos.words.list(guild_id)),
            "panels": len(await repos.selfroles.panels(guild_id)),
            "stats": len(await repos.stats.list(guild_id)),
            "giveaways": len(await repos.giveaways.active(guild_id)),
            "shop": len(await repos.shop.list(guild_id)),
        }
        row = await db.fetchone(
            "SELECT COUNT(*) AS n FROM warnings WHERE guild_id = ? AND active = 1",
            (guild_id,),
        )
        counts["warnings"] = int(row["n"]) if row is not None else 0
        row = await db.fetchone(
            "SELECT COUNT(*) AS n FROM tickets WHERE guild_id = ? AND status = 'open'",
            (guild_id,),
        )
        counts["tickets"] = int(row["n"]) if row is not None else 0
        settings = await request.app.state.settings.all(guild_id)
        return render(
            request,
            "overview.html",
            **guild_context(request, data, "overview", counts=counts, settings=settings),
        )

    # --------------------------------------------------------------- settings

    @app.post("/g/{guild_id}/toggle/{key}", response_class=HTMLResponse)
    async def toggle_setting(request: Request, guild_id: int, key: str) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        setting_key = TOGGLE_SETTINGS.get(key)
        if setting_key is None:
            return error_page(request, "Неизвестный переключатель.", 404)
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        enabled = str(form.get("value", "0")) == "1"
        await request.app.state.settings.set(guild_id, setting_key, enabled)
        flash(request, "Модуль включён." if enabled else "Модуль выключен.")
        next_url = str(form.get("next", f"/g/{guild_id}"))
        if not next_url.startswith(f"/g/{guild_id}"):
            next_url = f"/g/{guild_id}"
        return RedirectResponse(next_url, status_code=303)

    @app.get("/g/{guild_id}/settings", response_class=HTMLResponse)
    async def settings_page(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        slug = request.query_params.get("section", SECTIONS[0].slug)
        section = SECTION_BY_SLUG.get(slug, SECTIONS[0])
        values = await request.app.state.settings.all(guild_id)
        return render(
            request,
            "settings.html",
            **guild_context(
                request,
                data,
                "settings",
                section=section,
                values=values,
                sections=SECTIONS,
                saved=request.query_params.get("saved") == "1",
            ),
        )

    @app.post("/g/{guild_id}/settings/{section_slug}", response_class=HTMLResponse)
    async def settings_save(request: Request, guild_id: int, section_slug: str) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        section = SECTION_BY_SLUG.get(section_slug)
        if section is None:
            return error_page(request, "Раздел настроек не найден.", 404)
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        ok, errors = await apply_section(
            request.app.state.settings,
            guild_id,
            section,
            form,
            data["channels"],
            data["roles"],
        )
        if not ok:
            values = await request.app.state.settings.all(guild_id)
            return render(
                request,
                "settings.html",
                **guild_context(
                    request,
                    data,
                    "settings",
                    section=section,
                    values=values,
                    sections=SECTIONS,
                    errors=errors,
                    saved=False,
                ),
            )
        flash(request, f"Раздел «{section.title}» сохранён.")
        return RedirectResponse(
            f"/g/{guild_id}/settings?section={section.slug}&saved=1",
            status_code=303,
        )

    # ----------------------------------------------------------------- tickets

    @app.get("/g/{guild_id}/tickets", response_class=HTMLResponse)
    async def tickets_page(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        repos: Repositories = request.app.state.repos
        panels = await repos.tickets.panels(guild_id)
        open_tickets = await repos.tickets.open_tickets(guild_id)
        can_close = bool(
            int(data.get("permissions", 0)) & ((1 << 3) | (1 << 5) | (1 << 4))
        )
        return render(
            request,
            "tickets.html",
            **guild_context(
                request,
                data,
                "tickets",
                panels=panels,
                open_tickets=open_tickets,
                can_close=can_close,
                job_id=request.query_params.get("job"),
            ),
        )

    @app.post("/g/{guild_id}/tickets/close", response_class=HTMLResponse)
    async def tickets_close(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        if not int(data.get("permissions", 0)) & ((1 << 3) | (1 << 5) | (1 << 4)):
            return error_page(request, "Нужно право «Управлять каналами».", 403)
        try:
            ticket_id = int(str(form.get("ticket_id", "")))
        except ValueError:
            flash(request, "Неверный ID тикета.", "error")
            return RedirectResponse(f"/g/{guild_id}/tickets", status_code=303)
        job_id = await request.app.state.repos.jobs.enqueue(
            guild_id,
            int((session_user(request) or {}).get("id", 0)),
            "ticket.close",
            {"ticket_id": ticket_id},
        )
        flash(request, f"Задача #{job_id} создана.")
        return RedirectResponse(f"/g/{guild_id}/tickets?job={job_id}", status_code=303)

    # --------------------------------------------------------------- giveaways

    @app.get("/g/{guild_id}/giveaways", response_class=HTMLResponse)
    async def giveaways_page(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        giveaways = await request.app.state.repos.giveaways.recent(guild_id, limit=30)
        can_manage = bool(int(data.get("permissions", 0)) & ((1 << 3) | (1 << 5)))
        return render(
            request,
            "giveaways.html",
            **guild_context(
                request,
                data,
                "giveaways",
                giveaways=giveaways,
                can_manage=can_manage,
                job_id=request.query_params.get("job"),
            ),
        )

    @app.post("/g/{guild_id}/giveaways/start", response_class=HTMLResponse)
    async def giveaways_start(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        if not int(data.get("permissions", 0)) & ((1 << 3) | (1 << 5)):
            return error_page(request, "Нужно право «Управлять сервером».", 403)
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        try:
            channel_id = int(str(form.get("channel_id", "")))
            winners = int(str(form.get("winners", "1")))
        except ValueError:
            flash(request, "Проверьте канал и количество победителей.", "error")
            return RedirectResponse(f"/g/{guild_id}/giveaways", status_code=303)
        required_role_id = form.get("required_role_id")
        payload = {
            "channel_id": channel_id,
            "prize": str(form.get("prize", "")).strip()[:200],
            "duration": str(form.get("duration", "1h")).strip(),
            "winners": winners,
            "required_role_id": int(str(required_role_id)) if required_role_id else None,
        }
        job_id = await request.app.state.repos.jobs.enqueue(
            guild_id,
            int((session_user(request) or {}).get("id", 0)),
            "giveaway.start",
            payload,
        )
        flash(request, f"Задача #{job_id} создана.")
        return RedirectResponse(f"/g/{guild_id}/giveaways?job={job_id}", status_code=303)

    @app.post("/g/{guild_id}/giveaways/action", response_class=HTMLResponse)
    async def giveaways_action(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        if not int(data.get("permissions", 0)) & ((1 << 3) | (1 << 5)):
            return error_page(request, "Нужно право «Управлять сервером».", 403)
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        action = str(form.get("action", ""))
        if action not in {"end", "reroll"}:
            flash(request, "Неизвестное действие.", "error")
            return RedirectResponse(f"/g/{guild_id}/giveaways", status_code=303)
        try:
            giveaway_id = int(str(form.get("giveaway_id", "")))
        except ValueError:
            flash(request, "Неверный ID розыгрыша.", "error")
            return RedirectResponse(f"/g/{guild_id}/giveaways", status_code=303)
        job_id = await request.app.state.repos.jobs.enqueue(
            guild_id,
            int((session_user(request) or {}).get("id", 0)),
            f"giveaway.{action}",
            {"giveaway_id": giveaway_id},
        )
        flash(request, f"Задача #{job_id} создана.")
        return RedirectResponse(f"/g/{guild_id}/giveaways?job={job_id}", status_code=303)

    # --------------------------------------------------------------- moderation

    @app.get("/g/{guild_id}/moderation", response_class=HTMLResponse)
    async def moderation_page(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        repos: Repositories = request.app.state.repos
        warnings = await repos.warnings.recent(guild_id, limit=25)
        temp_actions = await repos.temp_actions.active_for_guild(guild_id)
        appeals = await repos.appeals.pending(guild_id)
        jobs = await repos.jobs.recent(guild_id, limit=10)
        try:
            members = await request.app.state.api.guild_members(guild_id)
        except (DiscordAPIError, AttributeError):
            members = []
        members = [
            {
                "id": str(item.get("user", {}).get("id", "")),
                "name": item.get("nick")
                or item.get("user", {}).get("global_name")
                or item.get("user", {}).get("username")
                or str(item.get("user", {}).get("id", "")),
            }
            for item in members
            if item.get("user", {}).get("id")
        ]
        members.sort(key=lambda item: item["name"].casefold())
        return render(
            request,
            "moderation.html",
            **guild_context(
                request,
                data,
                "moderation",
                warnings=warnings,
                temp_actions=temp_actions,
                appeals=appeals,
                members=members,
                jobs=jobs,
                job_id=request.query_params.get("job"),
                can_warn=bool(int(data.get("permissions", 0)) & ((1 << 40) | (1 << 3))),
                can_ban=bool(int(data.get("permissions", 0)) & ((1 << 2) | (1 << 3))),
                can_kick=bool(int(data.get("permissions", 0)) & ((1 << 1) | (1 << 3))),
                can_appeal=bool(
                    int(data.get("permissions", 0))
                    & ((1 << 3) | (1 << 5) | (1 << 13) | (1 << 40))
                ),
            ),
        )

    @app.post("/g/{guild_id}/moderation/warn", response_class=HTMLResponse)
    async def moderation_warn(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        if not int(data["permissions"]) & ((1 << 40) | (1 << 3)):
            return error_page(request, "Нужно право «Тайм-аут участников».", 403)
        try:
            user_id = int(str(form.get("user_id", "")))
            reason = str(form.get("reason", "")).strip()[:900]
        except ValueError:
            flash(request, "Выберите участника.", "error")
            return RedirectResponse(f"/g/{guild_id}/moderation", status_code=303)
        job_id = await request.app.state.repos.jobs.enqueue(
            guild_id,
            int((session_user(request) or {}).get("id", 0)),
            "moderation.warn",
            {"user_id": user_id, "reason": reason},
        )
        flash(request, f"Задача на предупреждение создана (#{job_id}).")
        return RedirectResponse(f"/g/{guild_id}/moderation?job={job_id}", status_code=303)

    @app.post("/g/{guild_id}/moderation/action", response_class=HTMLResponse)
    async def moderation_action(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        action = str(form.get("action", "")).strip()
        permissions = int(data.get("permissions", 0))
        required = {
            "timeout": (1 << 40, "Тайм-аут участников"),
            "ban": (1 << 2, "Бан участников"),
            "kick": (1 << 1, "Кик участников"),
        }
        if action not in required:
            flash(request, "Неизвестное действие.", "error")
            return RedirectResponse(f"/g/{guild_id}/moderation", status_code=303)
        bit, label = required[action]
        if not permissions & (bit | (1 << 3)):
            return error_page(request, f"Нужно право «{label}».", 403)
        try:
            user_id = int(str(form.get("user_id", "")))
        except ValueError:
            flash(request, "Выберите участника.", "error")
            return RedirectResponse(f"/g/{guild_id}/moderation", status_code=303)
        payload = {
            "user_id": user_id,
            "reason": str(form.get("reason", "")).strip()[:900],
            "duration": str(form.get("duration", "10m")).strip(),
        }
        job_id = await request.app.state.repos.jobs.enqueue(
            guild_id,
            int((session_user(request) or {}).get("id", 0)),
            f"moderation.{action}",
            payload,
        )
        flash(request, f"Задача #{job_id} создана.")
        return RedirectResponse(f"/g/{guild_id}/moderation?job={job_id}", status_code=303)

    @app.post("/g/{guild_id}/moderation/unwarn", response_class=HTMLResponse)
    async def moderation_unwarn(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        if not int(data.get("permissions", 0)) & ((1 << 40) | (1 << 3)):
            return error_page(request, "Нужно право «Тайм-аут участников».", 403)
        try:
            warning_id = int(str(form.get("warning_id", "")))
        except ValueError:
            flash(request, "Неверный ID предупреждения.", "error")
            return RedirectResponse(f"/g/{guild_id}/moderation", status_code=303)
        job_id = await request.app.state.repos.jobs.enqueue(
            guild_id,
            int((session_user(request) or {}).get("id", 0)),
            "moderation.unwarn",
            {"warning_id": warning_id},
        )
        flash(request, f"Задача #{job_id} создана.")
        return RedirectResponse(f"/g/{guild_id}/moderation?job={job_id}", status_code=303)

    @app.post("/g/{guild_id}/moderation/clear", response_class=HTMLResponse)
    async def moderation_clear(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        if not int(data.get("permissions", 0)) & ((1 << 40) | (1 << 3)):
            return error_page(request, "Нужно право «Тайм-аут участников».", 403)
        try:
            user_id = int(str(form.get("user_id", "")))
        except ValueError:
            flash(request, "Выберите участника.", "error")
            return RedirectResponse(f"/g/{guild_id}/moderation", status_code=303)
        job_id = await request.app.state.repos.jobs.enqueue(
            guild_id,
            int((session_user(request) or {}).get("id", 0)),
            "moderation.clearwarnings",
            {"user_id": user_id},
        )
        flash(request, f"Задача #{job_id} создана.")
        return RedirectResponse(f"/g/{guild_id}/moderation?job={job_id}", status_code=303)

    @app.post("/g/{guild_id}/moderation/appeal", response_class=HTMLResponse)
    async def moderation_appeal(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        permissions = int(data.get("permissions", 0))
        if not permissions & ((1 << 3) | (1 << 5) | (1 << 13) | (1 << 40)):
            return error_page(request, "Нужны права модерации или управления сообщениями.", 403)
        try:
            appeal_id = int(str(form.get("appeal_id", "")))
        except ValueError:
            flash(request, "Неверный ID апелляции.", "error")
            return RedirectResponse(f"/g/{guild_id}/moderation", status_code=303)
        approve = str(form.get("decision", "")) == "approve"
        job_id = await request.app.state.repos.jobs.enqueue(
            guild_id,
            int((session_user(request) or {}).get("id", 0)),
            "appeal.review",
            {"appeal_id": appeal_id, "approve": approve},
        )
        flash(request, f"Задача #{job_id} создана.")
        return RedirectResponse(f"/g/{guild_id}/moderation?job={job_id}", status_code=303)

    @app.get("/g/{guild_id}/api/jobs/{job_id}")
    async def job_api(request: Request, guild_id: int, job_id: int) -> Response:
        user = session_user(request)
        if user is None:
            return login_redirect(request)
        if not has_guild_access(user, guild_id):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        job = await request.app.state.repos.jobs.get(job_id)
        if job is None or job.guild_id != guild_id:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(
            {
                "id": job.id,
                "status": job.status,
                "error": job.error,
                "result": job.result,
            }
        )

    # --------------------------------------------------------------- autorole

    @app.get("/g/{guild_id}/autorole", response_class=HTMLResponse)
    async def autorole_page(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        current = await request.app.state.repos.autoroles.list(guild_id)
        enabled = await request.app.state.settings.get_bool(guild_id, "autorole_enabled", True)
        dangerous = {int(role["id"]) for role in data["roles"] if dangerous_role_label(role)}
        return render(
            request,
            "autorole.html",
            **guild_context(
                request,
                data,
                "autorole",
                current=current,
                enabled=enabled,
                human_ids={entry.role_id for entry in current if entry.kind == "human"},
                bot_ids={entry.role_id for entry in current if entry.kind == "bot"},
                dangerous_role_ids=dangerous,
                job_id=request.query_params.get("job"),
            ),
        )

    @app.post("/g/{guild_id}/autorole/apply", response_class=HTMLResponse)
    async def autorole_apply(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        if not int(data.get("permissions", 0)) & ((1 << 3) | (1 << 5) | (1 << 28)):
            return error_page(request, "Нужно право «Управлять ролями».", 403)
        job_id = await request.app.state.repos.jobs.enqueue(
            guild_id,
            int((session_user(request) or {}).get("id", 0)),
            "autorole.apply",
            {},
        )
        flash(request, f"Задача #{job_id} создана.")
        return RedirectResponse(f"/g/{guild_id}/autorole?job={job_id}", status_code=303)

    @app.post("/g/{guild_id}/autorole", response_class=HTMLResponse)
    async def autorole_save(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        action = str(form.get("action", "")).strip()
        repo = request.app.state.repos.autoroles
        try:
            if action == "save":
                role_map = {int(item["id"]): item for item in data["roles"]}
                selected: dict[str, list[int]] = {}
                for kind, field in (("human", "roles"), ("bot", "bot_roles")):
                    ids: list[int] = []
                    for raw in form.getlist(field):
                        try:
                            role_id = int(str(raw))
                        except ValueError as exc:
                            raise ValueError("Некорректная роль.") from exc
                        role = role_map.get(role_id)
                        if role is None:
                            raise ValueError("Одна из ролей не найдена на сервере.")
                        danger = dangerous_role_label(role)
                        if danger:
                            raise ValueError(
                                f"Роль @{role['name']} имеет опасные права ({danger})."
                            )
                        ids.append(role_id)
                    selected[kind] = ids
                actor_id = int((session_user(request) or {}).get("id", 0))
                await repo.replace_kind(guild_id, "human", selected["human"], actor_id)
                await repo.replace_kind(guild_id, "bot", selected["bot"], actor_id)
                flash(request, "Автороли сохранены.")
            elif action == "add":
                role_id = int(str(form.get("role_id", "")))
                role = next((item for item in data["roles"] if int(item["id"]) == role_id), None)
                if role is None:
                    raise ValueError("Роль не найдена на сервере.")
                danger = dangerous_role_label(role)
                if danger:
                    raise ValueError(f"Роль с опасными правами нельзя добавлять в автороли: {danger}.")
                kind = str(form.get("kind", "human"))
                if kind not in {"human", "bot"}:
                    raise ValueError("Неверный тип участника.")
                added = await repo.add(guild_id, role_id, kind, int(session_user(request)["id"]))
                flash(
                    request,
                    "Автороль добавлена." if added else "Эта автороль уже была добавлена.",
                    "success" if added else "warning",
                )
            elif action == "remove":
                role_id = int(str(form.get("role_id", "")))
                removed = await repo.remove(guild_id, role_id)
                flash(
                    request,
                    "Автороль удалена." if removed else "Автороль не найдена.",
                    "success" if removed else "warning",
                )
            elif action == "toggle":
                enabled = "enabled" in form
                await request.app.state.settings.set(guild_id, "autorole_enabled", enabled)
                flash(request, "Автовыдача включена." if enabled else "Автовыдача выключена.")
            else:
                raise ValueError("Неизвестное действие.")
        except (TypeError, ValueError) as exc:
            flash(request, str(exc), "error")
        return RedirectResponse(f"/g/{guild_id}/autorole", status_code=303)

    # --------------------------------------------------------------- automod

    @app.get("/g/{guild_id}/automod", response_class=HTMLResponse)
    async def automod_page(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        words = await request.app.state.repos.words.list(guild_id)
        enabled = await request.app.state.settings.get_bool(guild_id, "automod_enabled")
        return render(
            request,
            "automod.html",
            **guild_context(request, data, "automod", words=words, enabled=enabled),
        )

    @app.post("/g/{guild_id}/automod", response_class=HTMLResponse)
    async def automod_save(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        action = str(form.get("action", "")).strip()
        repo = request.app.state.repos.words
        user_id = int(session_user(request)["id"])
        try:
            if action == "add":
                word = str(form.get("word", "")).strip()
                if not word:
                    raise ValueError("Введите слово или фразу.")
                added = await repo.add(guild_id, word, user_id)
                flash(request, "Слово добавлено." if added else "Такое слово уже есть.", "success" if added else "warning")
            elif action == "remove":
                word = str(form.get("word", "")).strip()
                removed = await repo.remove(guild_id, word)
                flash(request, "Слово удалено." if removed else "Слово не найдено.", "success" if removed else "warning")
            elif action == "clear":
                deleted = await repo.clear(guild_id)
                flash(request, f"Удалено слов: {deleted}.")
            else:
                raise ValueError("Неизвестное действие.")
        except ValueError as exc:
            flash(request, str(exc), "error")
        return RedirectResponse(f"/g/{guild_id}/automod", status_code=303)

    # ----------------------------------------------------------------- access

    @app.get("/g/{guild_id}/access", response_class=HTMLResponse)
    async def access_page(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        rules = await request.app.state.repos.access.rules(guild_id)
        try:
            members = await request.app.state.api.guild_members(guild_id)
        except (DiscordAPIError, AttributeError):
            members = []
        members = [
            {
                "id": str(item.get("user", {}).get("id", "")),
                "name": item.get("nick")
                or item.get("user", {}).get("global_name")
                or item.get("user", {}).get("username")
                or str(item.get("user", {}).get("id", "")),
            }
            for item in members
            if item.get("user", {}).get("id")
        ]
        members.sort(key=lambda item: item["name"].casefold())
        return render(
            request,
            "access.html",
            **guild_context(
                request,
                data,
                "access",
                rules=rules,
                presets=COMMAND_PRESETS,
                preset_names=sorted(COMMAND_PRESETS),
                commands_by_group=COMMANDS_BY_GROUP,
                command_names=COMMAND_NAMES,
                members=members,
            ),
        )

    @app.post("/g/{guild_id}/access", response_class=HTMLResponse)
    async def access_save(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        action = str(form.get("action", "")).strip()
        target = normalize_command(str(form.get("target", "")))
        if not target:
            flash(request, "Укажите команду или список.", "error")
            return RedirectResponse(f"/g/{guild_id}/access", status_code=303)
        if target == "access" or target.startswith("access "):
            flash(request, "Нельзя ограничивать саму команду /access.", "error")
            return RedirectResponse(f"/g/{guild_id}/access", status_code=303)
        targets = list(COMMAND_PRESETS.get(target, ())) or [target]
        repo = request.app.state.repos.access
        changed = 0
        try:
            role_id = form.get("role_id")
            user_id = form.get("user_id")
            role_id = int(str(role_id)) if role_id else None
            user_id = int(str(user_id)) if user_id else None
            known_roles = {int(role["id"]) for role in data["roles"]}
            if role_id is not None and role_id not in known_roles:
                raise ValueError("Роль не найдена на сервере.")
            for command in targets:
                rule = await repo.get(guild_id, command) or CommandRule(guild_id=guild_id, command=command)
                if action == "allow-role" and role_id:
                    rule.allow_roles.add(role_id)
                    rule.deny_roles.discard(role_id)
                elif action == "deny-role" and role_id:
                    rule.deny_roles.add(role_id)
                    rule.allow_roles.discard(role_id)
                elif action == "allow-user" and user_id:
                    rule.allow_users.add(user_id)
                    rule.deny_users.discard(user_id)
                elif action == "deny-user" and user_id:
                    rule.deny_users.add(user_id)
                    rule.allow_users.discard(user_id)
                elif action == "disable":
                    rule.enabled = False
                elif action == "enable":
                    rule.enabled = True
                elif action == "reset":
                    if await repo.delete(guild_id, command):
                        changed += 1
                    continue
                else:
                    raise ValueError("Неверное действие или не указана роль/пользователь.")
                if rule.is_empty():
                    await repo.delete(guild_id, command)
                else:
                    await repo.save(rule)
                changed += 1
        except (TypeError, ValueError) as exc:
            flash(request, str(exc), "error")
            return RedirectResponse(f"/g/{guild_id}/access", status_code=303)
        # Инвалидируем кэш бота не можем напрямую — у него TTL; панель пишет в БД.
        flash(request, f"Обновлено правил: {changed}.")
        return RedirectResponse(f"/g/{guild_id}/access", status_code=303)

    # --------------------------------------------------------------- selfrole

    @app.get("/g/{guild_id}/selfrole", response_class=HTMLResponse)
    async def selfrole_page(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        panels = await request.app.state.repos.selfroles.panels(guild_id)
        panel_data = []
        for panel in panels:
            options = await request.app.state.repos.selfroles.options(panel.id)
            groups = await request.app.state.repos.selfroles.groups(panel.id)
            panel_data.append({"panel": panel, "options": options, "groups": groups})
        dangerous = {int(role["id"]) for role in data["roles"] if dangerous_role_label(role)}
        return render(
            request,
            "selfrole.html",
            **guild_context(
                request,
                data,
                "selfrole",
                panels=panel_data,
                dangerous_role_ids=dangerous,
            ),
        )

    @app.post("/g/{guild_id}/selfrole", response_class=HTMLResponse)
    async def selfrole_save(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        action = str(form.get("action", "create")).strip()
        repo = request.app.state.repos.selfroles
        try:
            if action == "delete":
                panel_id = int(str(form.get("panel_id", "")))
                panel = await repo.get_panel(panel_id)
                if panel is None or panel.guild_id != guild_id:
                    raise ValueError("Панель не найдена.")
                if panel.message_id:
                    try:
                        await request.app.state.api.delete_message(panel.channel_id, panel.message_id)
                    except DiscordAPIError as exc:
                        if exc.status != 404:
                            raise ValueError("Не удалось удалить сообщение панели в Discord.") from exc
                    except AttributeError:
                        pass
                await repo.delete_panel(panel_id)
                flash(request, "Панель удалена.")
                return RedirectResponse(f"/g/{guild_id}/selfrole", status_code=303)

            if action != "create":
                raise ValueError("Неизвестное действие.")

            channel_id = int(str(form.get("channel_id", "")))
            if channel_id not in {int(item["id"]) for item in data["channels"]}:
                raise ValueError("Канал не найден на сервере.")
            title = str(form.get("title", "")).strip()[:256]
            if not title:
                raise ValueError("Введите заголовок панели.")
            description = str(form.get("description", "")).strip()[:2000] or "Выберите роли кнопками ниже."
            raw_color = str(form.get("color", "")).strip().lstrip("#")
            try:
                color = int(raw_color, 16) & 0xFFFFFF if raw_color else 0x5865F2
            except ValueError as exc:
                raise ValueError("Цвет должен быть в формате #5865F2.") from exc
            role_ids = [int(value) for value in form.getlist("roles") if str(value)]
            if not role_ids:
                raise ValueError("Выберите хотя бы одну роль.")
            if len(role_ids) > 25:
                raise ValueError("Можно выбрать не более 25 ролей.")
            role_map = {int(role["id"]): role for role in data["roles"]}
            selected: list[dict[str, Any]] = []
            for role_id in role_ids:
                role = role_map.get(role_id)
                if role is None:
                    raise ValueError("Одна из ролей не найдена на сервере.")
                danger = dangerous_role_label(role)
                if danger:
                    raise ValueError(f"Роль @{role['name']} имеет опасные права ({danger}).")
                selected.append(role)

            panel_id = await repo.create_panel(guild_id, channel_id, title, description, color)
            for role in selected:
                await repo.add_option(
                    panel_id,
                    int(role["id"]),
                    label=str(role.get("name", "Роль"))[:80],
                    style="secondary",
                )
            if str(form.get("exclusive", "")) == "on":
                for role in selected:
                    await repo.set_group(panel_id, int(role["id"]), "default")

            role_lines = [f"<@&{role['id']}>" for role in selected]
            embed = {
                "title": title,
                "description": description,
                "color": color,
                "fields": [
                    {
                        "name": "Доступные роли",
                        "value": "\n".join(role_lines)[:1024],
                        "inline": False,
                    }
                ],
                "footer": {"text": f"Панель #{panel_id} • нажмите кнопку, чтобы получить или снять роль"},
            }
            components = []
            row: list[dict[str, Any]] = []
            for role in selected:
                row.append(
                    {
                        "type": 2,
                        "style": 2,
                        "label": str(role.get("name", "Роль"))[:80],
                        "custom_id": f"selfrole:{panel_id}:{role['id']}",
                    }
                )
                if len(row) == 5:
                    components.append({"type": 1, "components": row})
                    row = []
            if row:
                components.append({"type": 1, "components": row})

            try:
                message = await request.app.state.api.create_message(
                    channel_id,
                    {
                        "embeds": [embed],
                        "components": components,
                        "allowed_mentions": {"parse": []},
                    },
                )
            except Exception as exc:  # noqa: BLE001 - не оставляем панель без сообщения
                await repo.delete_panel(panel_id)
                raise ValueError(f"Не удалось отправить панель в Discord: {exc}") from exc

            await repo.set_message_id(panel_id, int(message["id"]))
            flash(request, f"Панель #{panel_id} создана.")
        except (TypeError, ValueError) as exc:
            flash(request, str(exc), "error")
        return RedirectResponse(f"/g/{guild_id}/selfrole", status_code=303)

    # ------------------------------------------------------------------ stats

    @app.get("/g/{guild_id}/stats", response_class=HTMLResponse)
    async def stats_page(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        channels = await request.app.state.repos.stats.list(guild_id)
        live = {
            "members": int(data["guild"].get("member_count") or 0),
            "boosts": int(data["guild"].get("premium_subscription_count") or 0),
        }
        return render(
            request,
            "stats.html",
            **guild_context(request, data, "stats", stats_channels=channels, live=live, job_id=request.query_params.get("job")),
        )

    @app.post("/g/{guild_id}/stats/refresh", response_class=HTMLResponse)
    async def stats_refresh(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        if not int(data.get("permissions", 0)) & ((1 << 3) | (1 << 5)):
            return error_page(request, "Нужно право «Управлять сервером».", 403)
        job_id = await request.app.state.repos.jobs.enqueue(
            guild_id,
            int((session_user(request) or {}).get("id", 0)),
            "stats.refresh",
            {},
        )
        flash(request, f"Задача #{job_id} создана.")
        return RedirectResponse(f"/g/{guild_id}/stats?job={job_id}", status_code=303)

    @app.get("/g/{guild_id}/api/stats")
    async def stats_api(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        guild = data["guild"]
        return JSONResponse(
            {
                "members": int(guild.get("member_count") or 0),
                "boosts": int(guild.get("premium_subscription_count") or 0),
            }
        )

    @app.post("/g/{guild_id}/stats", response_class=HTMLResponse)
    async def stats_save(request: Request, guild_id: int) -> Response:
        data, response = await load_guild(request, guild_id)
        if response is not None:
            return response
        assert data is not None
        form = await request.form()
        if not verify_csrf(request, form.get("csrf")):
            return error_page(request, "CSRF-токен неверен. Обновите страницу.", 403)
        action = str(form.get("action", "")).strip()
        repo = request.app.state.repos.stats
        try:
            if action == "add":
                channel_id = int(str(form.get("channel_id", "")))
                if channel_id not in {int(item["id"]) for item in data["stat_channels"]}:
                    raise ValueError("Канал не найден на сервере.")
                kind = str(form.get("kind", "members"))
                if kind not in {"members", "humans", "bots", "boosts"}:
                    raise ValueError("Неверный тип статистики.")
                template = str(form.get("template", "Участники: {count}")).strip() or "Участники: {count}"
                if "{count}" not in template:
                    raise ValueError("Шаблон должен содержать {count}.")
                await repo.upsert(guild_id, channel_id, kind, template)
                flash(request, "Канал статистики сохранён.")
            elif action == "remove":
                channel_id = int(str(form.get("channel_id", "")))
                removed = await repo.remove(guild_id, channel_id)
                flash(request, "Канал удалён." if removed else "Канал не найден.", "success" if removed else "warning")
            else:
                raise ValueError("Неизвестное действие.")
        except (TypeError, ValueError) as exc:
            flash(request, str(exc), "error")
        return RedirectResponse(f"/g/{guild_id}/stats", status_code=303)

    # ----------------------------------------------------------------- health

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(DiscordAPIError)
    async def discord_error(request: Request, exc: DiscordAPIError) -> Response:
        return error_page(request, f"Ошибка Discord API: {exc}", 502)

    return app


app: FastAPI | None
try:
    app = create_app()
except Exception as exc:  # noqa: BLE001 - без конфигурации приложение запускает dashboard.py
    log.debug("Dashboard app не создан при импорте: %s", exc)
    app = None

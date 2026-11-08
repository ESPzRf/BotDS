"""Минимальный клиент Discord REST для дашборда.

Токен бота используется только для чтения серверов, каналов и ролей.
OAuth-токен пользователя — только для входа и списка его серверов.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

API = "https://discord.com/api/v10"


class DiscordAPIError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class DiscordAPI:
    def __init__(self, bot_token: str) -> None:
        self._bot_token = bot_token
        self._client = httpx.AsyncClient(timeout=20.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        **kwargs: Any,
    ) -> Any:
        headers = kwargs.pop("headers", {})
        use_token = token or self._bot_token
        if token:
            headers["Authorization"] = f"Bearer {use_token}"
        else:
            headers["Authorization"] = f"Bot {use_token}"
        url = path if path.startswith("http") else f"{API}{path}"

        for attempt in range(2):
            response = await self._client.request(method, url, headers=headers, **kwargs)
            if response.status_code == 429 and attempt == 0:
                try:
                    retry = float(response.json().get("retry_after", 1))
                except (ValueError, TypeError):
                    retry = 1.0
                await asyncio.sleep(min(retry, 3.0))
                continue
            break

        if response.status_code >= 400:
            try:
                payload = response.json()
                message = payload.get("message", response.text)
            except ValueError:
                message = response.text
            raise DiscordAPIError(response.status_code, str(message))
        if response.status_code == 204:
            return None
        return response.json()

    # ------------------------------------------------------------- bot routes

    async def guild(self, guild_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/guilds/{guild_id}", params={"with_counts": "true"})

    async def guild_channels(self, guild_id: int) -> list[dict[str, Any]]:
        return await self._request("GET", f"/guilds/{guild_id}/channels")

    async def guild_roles(self, guild_id: int) -> list[dict[str, Any]]:
        return await self._request("GET", f"/guilds/{guild_id}/roles")

    async def guild_member(self, guild_id: int, user_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/guilds/{guild_id}/members/{user_id}")

    async def bot_guilds(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/users/@me/guilds")

    # ----------------------------------------------------------- oauth routes

    async def exchange_code(
        self,
        code: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        response = await self._client.post(
            f"{API}/oauth2/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code >= 400:
            raise DiscordAPIError(response.status_code, response.text)
        return response.json()

    async def oauth_me(self, access_token: str) -> dict[str, Any]:
        return await self._request("GET", "/users/@me", token=access_token)

    async def oauth_guilds(self, access_token: str) -> list[dict[str, Any]]:
        return await self._request("GET", "/users/@me/guilds", token=access_token)

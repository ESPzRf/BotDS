"""Тесты веб-дашборда: настройки, шаблоны и страницы."""
from __future__ import annotations

import unittest
import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
    category=Warning,
)

try:
    from fastapi.testclient import TestClient

    from dashboard.app import create_app, templates
    from dashboard.config import DashboardConfig
    from dashboard.schema import SECTIONS, all_keys

    HAS_DASHBOARD = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_DASHBOARD = False


class FakeAPI:
    async def guild(self, guild_id: int):
        return {"id": str(guild_id), "name": "Test Guild", "member_count": 12}

    async def guild_channels(self, guild_id: int):
        return [
            {"id": "10", "name": "general", "type": 0},
            {"id": "11", "name": "logs", "type": 0},
        ]

    async def guild_roles(self, guild_id: int):
        return [
            {"id": "1", "name": "@everyone", "color": 0, "position": 0, "managed": False},
            {"id": "20", "name": "Moderator", "color": 3447003, "position": 5, "managed": False},
        ]

    async def close(self):
        pass


@unittest.skipUnless(HAS_DASHBOARD, "зависимости дашборда не установлены")
class DashboardSchemaTests(unittest.TestCase):
    def test_all_settings_are_present(self) -> None:
        from core.settings import DEFAULTS

        # autorole_enabled и raid_mode_until настраиваются отдельными страницами.
        expected = set(DEFAULTS) - {"autorole_enabled", "raid_mode_until"}
        self.assertEqual(set(all_keys()), expected)

    def test_templates_compile(self) -> None:
        names = [
            "base.html",
            "login.html",
            "guilds.html",
            "overview.html",
            "settings.html",
            "autorole.html",
            "moderation.html",
            "automod.html",
            "access.html",
            "selfrole.html",
            "stats.html",
            "error.html",
        ]
        for name in names:
            templates.env.get_template(name)

    def test_memory_database_is_not_resolved_as_file(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(
            os.environ,
            {
                "DISCORD_TOKEN": "test",
                "DB_PATH": ":memory:",
                "DATABASE_URL": "",
                "DASHBOARD_SECRET": "test",
                "DASHBOARD_DEV_MODE": "0",
            },
            clear=False,
        ):
            config = DashboardConfig.load()
        self.assertEqual(config.db_target, ":memory:")


@unittest.skipUnless(HAS_DASHBOARD, "зависимости дашборда не установлены")
class DashboardAppTests(unittest.TestCase):
    def setUp(self) -> None:
        config = DashboardConfig(
            bot_token="test",
            client_id="",
            client_secret="",
            secret="test-secret",
            base_url="http://testserver",
            host="127.0.0.1",
            port=8080,
            db_target=":memory:",
            dev_mode=True,
            dev_guild_id=1,
            dev_user_id=100,
        )
        self.app = create_app(config)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.app.state.api = FakeAPI()
        response = self.client.get("/auth/dev", follow_redirects=False)
        assert response.status_code == 303

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)

    def test_pages_render(self) -> None:
        for path in (
            "/guilds",
            "/g/1",
            "/g/1/settings",
            "/g/1/settings?section=levels",
            "/g/1/settings?section=security",
            "/g/1/autorole",
            "/g/1/moderation",
            "/g/1/automod",
            "/g/1/access",
            "/g/1/selfrole",
            "/g/1/stats",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text[:300])

    def test_csrf_is_required(self) -> None:
        response = self.client.post(
            "/g/1/settings/logs",
            data={"log_messages": "on"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 403)

    def test_settings_save(self) -> None:
        import re

        page = self.client.get("/g/1/settings?section=logs")
        token = re.search(r'name="csrf" value="([^"]+)"', page.text)
        self.assertIsNotNone(token)
        response = self.client.post(
            "/g/1/settings/logs",
            data={
                "csrf": token.group(1),
                "log_channel": "10",
                "log_messages": "on",
                "log_members": "on",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Сохранено", response.text)
        self.assertIn('value="10" selected', response.text)

    def test_quick_toggle(self) -> None:
        import re

        page = self.client.get("/g/1")
        token = re.search(r'name="csrf" value="([^"]+)"', page.text)
        self.assertIsNotNone(token)
        response = self.client.post(
            "/g/1/toggle/automod_enabled",
            data={"csrf": token.group(1), "value": "1", "next": "/g/1"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        page = self.client.get("/g/1/settings?section=automod")
        self.assertIn("Включено", page.text)

    def test_autorole_add(self) -> None:
        page = self.client.get("/g/1/autorole")
        self.assertIn("name=\"csrf\"", page.text)
        import re

        token = re.search(r'name="csrf" value="([^"]+)"', page.text)
        self.assertIsNotNone(token)
        response = self.client.post(
            "/g/1/autorole",
            data={"csrf": token.group(1), "action": "add", "role_id": "20", "kind": "human"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        page = self.client.get("/g/1/autorole")
        self.assertIn("Moderator", page.text)


if __name__ == "__main__":
    unittest.main()

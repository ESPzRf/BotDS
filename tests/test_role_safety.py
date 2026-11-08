"""Проверки опасных ролей для selfrole/autorole/shop."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from core.discord_utils import dangerous_role_permissions, role_assign_problem


class FakePermissions:
    def __init__(self, **values: bool) -> None:
        for name in (
            "administrator",
            "manage_guild",
            "manage_roles",
            "manage_channels",
            "kick_members",
            "ban_members",
            "moderate_members",
            "manage_webhooks",
            "mention_everyone",
        ):
            setattr(self, name, values.get(name, False))


class FakeRole:
    def __init__(self, permissions: FakePermissions, rank: int = 5) -> None:
        self.permissions = permissions
        self.managed = False
        self.name = "Test"
        self.rank = rank

    def is_default(self) -> bool:
        return False

    def __ge__(self, other: "FakeRole") -> bool:
        return self.rank >= other.rank


class RoleSafetyTests(unittest.TestCase):
    def test_administrator_role_is_rejected(self) -> None:
        guild = SimpleNamespace(
            me=SimpleNamespace(
                guild_permissions=SimpleNamespace(manage_roles=True),
                top_role=FakeRole(FakePermissions(), 100),
            )
        )
        role = FakeRole(FakePermissions(administrator=True))
        self.assertIn("опасные права", role_assign_problem(guild, role) or "")
        self.assertIn("администратор", dangerous_role_permissions(role) or "")

    def test_normal_role_is_allowed(self) -> None:
        guild = SimpleNamespace(
            me=SimpleNamespace(
                guild_permissions=SimpleNamespace(manage_roles=True),
                top_role=FakeRole(FakePermissions(), 100),
            )
        )
        role = FakeRole(FakePermissions())
        self.assertIsNone(dangerous_role_permissions(role))
        self.assertIsNone(role_assign_problem(guild, role))


if __name__ == "__main__":
    unittest.main()

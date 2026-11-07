"""Тесты правил доступа к командам."""
from __future__ import annotations

import unittest

from core.command_access import (
    CommandRule,
    command_chain,
    command_path_from_data,
    evaluate_access,
    normalize_command,
)


class CommandChainTests(unittest.TestCase):
    def test_normalize(self) -> None:
        self.assertEqual(normalize_command("  /LevelConfig   add-xp "), "levelconfig add-xp")

    def test_chain(self) -> None:
        self.assertEqual(
            command_chain("levelconfig add-xp"),
            ["levelconfig", "levelconfig add-xp"],
        )
        self.assertEqual(command_chain("warn"), ["warn"])

    def test_path_from_data(self) -> None:
        self.assertEqual(
            command_path_from_data(
                {
                    "name": "levelconfig",
                    "options": [
                        {"name": "add-xp", "type": 1, "options": [{"name": "member", "type": 6}]}
                    ],
                }
            ),
            "levelconfig add-xp",
        )
        self.assertEqual(command_path_from_data({"name": "rank"}), "rank")
        self.assertEqual(
            command_path_from_data(
                {
                    "name": "warnconfig",
                    "options": [
                        {"name": "escalation", "type": 1, "options": [{"name": "count", "type": 4}]}
                    ],
                }
            ),
            "warnconfig escalation",
        )


class EvaluateAccessTests(unittest.TestCase):
    def test_no_rule_allows(self) -> None:
        self.assertEqual(
            evaluate_access({}, "warn", user_id=1, role_ids={10}),
            (True, None),
        )

    def test_allow_role(self) -> None:
        rule = CommandRule(1, "levelconfig", allow_roles={10})
        rules = {"levelconfig": rule}
        self.assertEqual(
            evaluate_access(rules, "levelconfig add-xp", user_id=1, role_ids={10}),
            (True, None),
        )
        allowed, reason = evaluate_access(
            rules, "levelconfig add-xp", user_id=1, role_ids={11}
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "not_allowed")

    def test_allow_user(self) -> None:
        rule = CommandRule(1, "warn", allow_users={42})
        rules = {"warn": rule}
        self.assertTrue(evaluate_access(rules, "warn", user_id=42, role_ids=set())[0])
        self.assertFalse(evaluate_access(rules, "warn", user_id=43, role_ids={99})[0])

    def test_deny_has_priority(self) -> None:
        rule = CommandRule(1, "warn", allow_roles={10}, deny_users={42})
        rules = {"warn": rule}
        allowed, reason = evaluate_access(rules, "warn", user_id=42, role_ids={10})
        self.assertFalse(allowed)
        self.assertEqual(reason, "denied")

    def test_disabled(self) -> None:
        rule = CommandRule(1, "warn", enabled=False)
        allowed, reason = evaluate_access({"warn": rule}, "warn", user_id=1, role_ids=set())
        self.assertFalse(allowed)
        self.assertEqual(reason, "disabled")

    def test_parent_and_child_combine(self) -> None:
        rules = {
            "levelconfig": CommandRule(1, "levelconfig", allow_roles={10}),
            "levelconfig add-xp": CommandRule(1, "levelconfig add-xp", deny_users={42}),
        }
        self.assertTrue(
            evaluate_access(rules, "levelconfig add-xp", user_id=1, role_ids={10})[0]
        )
        allowed, reason = evaluate_access(
            rules, "levelconfig add-xp", user_id=42, role_ids={10}
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "denied")

    def test_admin_and_owner_bypass(self) -> None:
        rules = {"warn": CommandRule(1, "warn", enabled=False)}
        self.assertTrue(
            evaluate_access(rules, "warn", user_id=1, role_ids=set(), is_admin=True)[0]
        )
        self.assertTrue(
            evaluate_access(rules, "warn", user_id=1, role_ids=set(), is_owner=True)[0]
        )


if __name__ == "__main__":
    unittest.main()

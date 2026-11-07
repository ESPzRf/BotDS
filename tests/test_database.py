"""Тесты слоя БД и репозиториев."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from core.command_access import CommandRule
from core.database import Database
from core.repositories import build_repositories, to_iso
from core.settings import GuildSettingsStore


class DatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        # in-memory БД не создаёт файлов и не конфликтует с песочницей Windows.
        self.db = Database(":memory:")
        await self.db.connect()
        self.repos = build_repositories(self.db)
        self.settings = GuildSettingsStore(self.db)

    async def asyncTearDown(self) -> None:
        await self.db.close()


class SettingsTests(DatabaseTestCase):
    async def test_defaults_and_set(self) -> None:
        self.assertIsNone(await self.settings.get(1, "log_channel"))
        self.assertFalse(await self.settings.get_bool(1, "automod_enabled"))
        self.assertEqual(await self.settings.get(1, "locale"), "ru")
        self.assertEqual(await self.settings.get(1, "levels_xp_multiplier"), 1.0)
        self.assertEqual(await self.settings.get(1, "levels_role_boosts"), {})

        await self.settings.set(1, "automod_enabled", True)
        self.assertTrue(await self.settings.get_bool(1, "automod_enabled"))
        self.assertTrue(await self.settings.get(1, "automod_enabled"))

    async def test_values_are_json(self) -> None:
        await self.settings.set(1, "levels_ignored_channels", [10, 20, 30])
        self.assertEqual(await self.settings.get_list(1, "levels_ignored_channels"), [10, 20, 30])

        rules = [{"count": 3, "action": "timeout", "duration": 3600}]
        await self.settings.set(1, "warn_escalations", rules)
        self.assertEqual(await self.settings.get(1, "warn_escalations"), rules)

    async def test_isolation_between_guilds(self) -> None:
        await self.settings.set(1, "automod_enabled", True)
        self.assertFalse(await self.settings.get_bool(2, "automod_enabled"))

    async def test_delete_all_restores_defaults(self) -> None:
        await self.settings.set(1, "automod_enabled", True)
        await self.settings.delete_all(1)
        self.assertFalse(await self.settings.get_bool(1, "automod_enabled"))

    async def test_all_returns_defaults_merged(self) -> None:
        await self.settings.set(1, "automod_enabled", True)
        values = await self.settings.all(1)
        self.assertTrue(values["automod_enabled"])
        self.assertIn("levels_xp_min", values)

    async def test_persisted_between_instances(self) -> None:
        await self.settings.set(1, "levels_xp_min", 40)
        other = GuildSettingsStore(self.db)
        self.assertEqual(await other.get_int(1, "levels_xp_min"), 40)


class WarningTests(DatabaseTestCase):
    async def test_add_count_and_get(self) -> None:
        warning_id = await self.repos.warnings.add(1, 100, 200, "спам", "manual")
        warning = await self.repos.warnings.get(warning_id)

        self.assertIsNotNone(warning)
        assert warning is not None
        self.assertEqual(warning.reason, "спам")
        self.assertEqual(warning.moderator_id, 200)
        self.assertTrue(warning.active)
        self.assertEqual(await self.repos.warnings.count_active(1, 100), 1)

    async def test_deactivate_and_clear(self) -> None:
        first = await self.repos.warnings.add(1, 100, 200, "раз")
        second = await self.repos.warnings.add(1, 100, 200, "два")

        self.assertTrue(await self.repos.warnings.deactivate(first, 1))
        self.assertFalse(await self.repos.warnings.deactivate(first, 1))
        self.assertEqual(await self.repos.warnings.count_active(1, 100), 1)

        self.assertEqual(await self.repos.warnings.clear(1, 100), 1)
        self.assertEqual(await self.repos.warnings.count_active(1, 100), 0)

        self.assertIsNotNone(await self.repos.warnings.get(second))
        history = await self.repos.warnings.active_for(1, 100)
        self.assertEqual(history, [])

    async def test_guild_isolation(self) -> None:
        await self.repos.warnings.add(1, 100, 200, "спам")
        self.assertEqual(await self.repos.warnings.count_active(2, 100), 0)

    async def test_recent(self) -> None:
        for index in range(3):
            await self.repos.warnings.add(1, 100, 200, f"причина {index}")
        recent = await self.repos.warnings.recent(1, limit=10)
        self.assertEqual(len(recent), 3)
        self.assertEqual(recent[0].reason, "причина 2")


class AutoroleTests(DatabaseTestCase):
    async def test_add_list_remove(self) -> None:
        self.assertTrue(await self.repos.autoroles.add(1, 10, "human"))
        self.assertFalse(await self.repos.autoroles.add(1, 10, "human"))
        self.assertTrue(await self.repos.autoroles.add(1, 20, "bot"))

        human = await self.repos.autoroles.list(1, "human")
        self.assertEqual([entry.role_id for entry in human], [10])

        every = await self.repos.autoroles.list(1)
        self.assertEqual(len(every), 2)

        self.assertEqual(await self.repos.autoroles.remove(1, 10), 1)
        self.assertEqual(await self.repos.autoroles.list(1, "human"), [])


class SelfRoleTests(DatabaseTestCase):
    async def test_panel_and_options(self) -> None:
        panel_id = await self.repos.selfroles.create_panel(1, 500, "Роли", "Выберите", 0x5865F2)
        panel = await self.repos.selfroles.get_panel(panel_id)
        self.assertIsNotNone(panel)
        assert panel is not None
        self.assertIsNone(panel.message_id)

        await self.repos.selfroles.set_message_id(panel_id, 777)
        await self.repos.selfroles.add_option(panel_id, 50, label="Gamer", emoji="🎮", style="primary")
        await self.repos.selfroles.add_option(panel_id, 51, label="Music", style="success")

        options = await self.repos.selfroles.options(panel_id)
        self.assertEqual([option.role_id for option in options], [50, 51])
        self.assertEqual(options[0].label, "Gamer")
        self.assertEqual(options[0].style, "primary")

        updated = await self.repos.selfroles.get_panel(panel_id)
        assert updated is not None
        self.assertEqual(updated.message_id, 777)

    async def test_option_upsert(self) -> None:
        panel_id = await self.repos.selfroles.create_panel(1, 500, "Роли")
        await self.repos.selfroles.add_option(panel_id, 50, label="A", style="secondary")
        await self.repos.selfroles.add_option(panel_id, 50, label="B", style="danger")

        options = await self.repos.selfroles.options(panel_id)
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0].label, "B")
        self.assertEqual(options[0].style, "danger")

    async def test_remove_option_and_cascade(self) -> None:
        panel_id = await self.repos.selfroles.create_panel(1, 500, "Роли")
        await self.repos.selfroles.add_option(panel_id, 50)
        self.assertTrue(await self.repos.selfroles.remove_option(panel_id, 50))
        self.assertFalse(await self.repos.selfroles.remove_option(panel_id, 50))

        await self.repos.selfroles.add_option(panel_id, 51)
        await self.repos.selfroles.delete_panel(panel_id)
        self.assertIsNone(await self.repos.selfroles.get_panel(panel_id))
        self.assertEqual(await self.repos.selfroles.options(panel_id), [])

    async def test_update_panel(self) -> None:
        panel_id = await self.repos.selfroles.create_panel(1, 500, "Старое", "Описание")
        await self.repos.selfroles.update_panel(panel_id, title="Новое", color=0xFF0000)
        panel = await self.repos.selfroles.get_panel(panel_id)
        assert panel is not None
        self.assertEqual(panel.title, "Новое")
        self.assertEqual(panel.description, "Описание")
        self.assertEqual(panel.color, 0xFF0000)

    async def test_exclusive_groups(self) -> None:
        panel_id = await self.repos.selfroles.create_panel(1, 500, "Роли")
        await self.repos.selfroles.add_option(panel_id, 50)
        await self.repos.selfroles.add_option(panel_id, 51)

        await self.repos.selfroles.set_group(panel_id, 50, "Цвет")
        await self.repos.selfroles.set_group(panel_id, 51, "Цвет")
        self.assertEqual(await self.repos.selfroles.groups(panel_id), {50: "Цвет", 51: "Цвет"})

        await self.repos.selfroles.set_group(panel_id, 50, "Форма")
        self.assertEqual(
            await self.repos.selfroles.groups(panel_id),
            {50: "Форма", 51: "Цвет"},
        )

        self.assertTrue(await self.repos.selfroles.clear_group(panel_id, 50))
        self.assertFalse(await self.repos.selfroles.clear_group(panel_id, 50))
        self.assertEqual(await self.repos.selfroles.groups(panel_id), {51: "Цвет"})

        self.assertTrue(await self.repos.selfroles.remove_option(panel_id, 51))
        self.assertEqual(await self.repos.selfroles.groups(panel_id), {})


class WordTests(DatabaseTestCase):
    async def test_words_crud(self) -> None:
        self.assertTrue(await self.repos.words.add(1, "дурак", 10))
        self.assertFalse(await self.repos.words.add(1, "дурак", 10))
        self.assertEqual(await self.repos.words.list(1), ["дурак"])

        self.assertTrue(await self.repos.words.remove(1, "дурак"))
        self.assertFalse(await self.repos.words.remove(1, "дурак"))

        await self.repos.words.add(1, "один")
        await self.repos.words.add(1, "два")
        self.assertEqual(await self.repos.words.clear(1), 2)
        self.assertEqual(await self.repos.words.list(1), [])


class MemberTests(DatabaseTestCase):
    async def test_ensure_and_save(self) -> None:
        stats = await self.repos.members.ensure(1, 42)
        self.assertEqual((stats.xp, stats.level, stats.loyalty), (0, 0, 0))

        stats.xp = 300
        stats.level = 2
        stats.messages = 5
        stats.loyalty = 7
        stats.daily_streak = 3
        await self.repos.members.save(stats)

        loaded = await self.repos.members.get(1, 42)
        assert loaded is not None
        self.assertEqual((loaded.xp, loaded.level, loaded.messages), (300, 2, 5))
        self.assertEqual(loaded.loyalty, 7)
        self.assertEqual(loaded.daily_streak, 3)

    async def test_leaderboard_and_position(self) -> None:
        for user_id, xp in ((1, 100), (2, 500), (3, 250)):
            stats = await self.repos.members.ensure(10, user_id)
            stats.xp = xp
            await self.repos.members.save(stats)

        board = await self.repos.members.leaderboard(10, "xp", 10)
        self.assertEqual([item.user_id for item in board], [2, 3, 1])

        self.assertEqual(await self.repos.members.rank_position(10, 2, "xp"), 1)
        self.assertEqual(await self.repos.members.rank_position(10, 1, "xp"), 3)
        self.assertIsNone(await self.repos.members.rank_position(10, 999, "xp"))
        self.assertEqual(await self.repos.members.count(10), 3)

    async def test_invalid_metric(self) -> None:
        with self.assertRaises(ValueError):
            await self.repos.members.leaderboard(1, "password", 10)


class LevelRewardTests(DatabaseTestCase):
    async def test_rewards(self) -> None:
        await self.repos.level_rewards.add(1, 5, 100)
        await self.repos.level_rewards.add(1, 10, 200)
        await self.repos.level_rewards.add(1, 5, 150)  # перезапись

        rewards = await self.repos.level_rewards.list(1)
        self.assertEqual(rewards, {5: 150, 10: 200})

        self.assertTrue(await self.repos.level_rewards.remove(1, 5))
        self.assertFalse(await self.repos.level_rewards.remove(1, 5))
        self.assertEqual(await self.repos.level_rewards.list(1), {10: 200})


class TicketTests(DatabaseTestCase):
    async def test_panel_and_ticket_flow(self) -> None:
        panel_id = await self.repos.tickets.create_panel(1, 500, "Поддержка", "Описание", 9, 77)
        panel = await self.repos.tickets.get_panel(panel_id)
        assert panel is not None
        self.assertEqual(panel.title, "Поддержка")
        self.assertEqual(panel.support_role_id, 77)
        self.assertIsNone(panel.message_id)

        await self.repos.tickets.set_panel_message(panel_id, 900)
        panel = await self.repos.tickets.get_panel(panel_id)
        assert panel is not None
        self.assertEqual(panel.message_id, 900)

        ticket_id = await self.repos.tickets.create_ticket(1, panel_id, 600, 42)
        ticket = await self.repos.tickets.get_ticket(ticket_id)
        assert ticket is not None
        self.assertEqual((ticket.channel_id, ticket.user_id, ticket.status), (600, 42, "open"))
        self.assertIsNone(ticket.message_id)

        await self.repos.tickets.set_ticket_message(ticket_id, 901)
        await self.repos.tickets.claim_ticket(ticket_id, 99)
        ticket = await self.repos.tickets.get_ticket(ticket_id)
        assert ticket is not None
        self.assertEqual(ticket.claimed_by, 99)

        opened = await self.repos.tickets.open_ticket_for(1, 42)
        assert opened is not None
        self.assertEqual(opened.id, ticket_id)
        self.assertEqual(len(await self.repos.tickets.open_tickets(1)), 1)
        self.assertEqual(len(await self.repos.tickets.all_open_tickets()), 1)

        self.assertTrue(await self.repos.tickets.close_ticket(ticket_id, 99))
        self.assertFalse(await self.repos.tickets.close_ticket(ticket_id, 99))
        self.assertIsNone(await self.repos.tickets.open_ticket_for(1, 42))
        self.assertEqual(await self.repos.tickets.open_tickets(1), [])

    async def test_delete_panel_sets_null(self) -> None:
        panel_id = await self.repos.tickets.create_panel(1, 500, "Панель")
        ticket_id = await self.repos.tickets.create_ticket(1, panel_id, 600, 42)
        self.assertTrue(await self.repos.tickets.delete_panel(panel_id))
        ticket = await self.repos.tickets.get_ticket(ticket_id)
        assert ticket is not None
        self.assertIsNone(ticket.panel_id)


class TempActionTests(DatabaseTestCase):
    async def test_due_and_deactivate(self) -> None:
        now = datetime.now(timezone.utc)
        action_id = await self.repos.temp_actions.add(
            1, 42, 99, "ban", "причина", now + timedelta(hours=1)
        )
        action = await self.repos.temp_actions.get(action_id)
        assert action is not None
        self.assertEqual((action.action, action.user_id, action.active), ("ban", 42, True))

        self.assertEqual(await self.repos.temp_actions.due(to_iso(now)), [])
        due = await self.repos.temp_actions.due(to_iso(now + timedelta(hours=2)))
        self.assertEqual([item.id for item in due], [action_id])
        self.assertEqual(len(await self.repos.temp_actions.active_for_guild(1)), 1)

        self.assertTrue(await self.repos.temp_actions.deactivate(action_id))
        self.assertFalse(await self.repos.temp_actions.deactivate(action_id))
        self.assertEqual(await self.repos.temp_actions.active_for_guild(1), [])


class AppealTests(DatabaseTestCase):
    async def test_appeal_flow(self) -> None:
        warning_id = await self.repos.warnings.add(1, 42, 99, "спам")
        appeal_id = await self.repos.appeals.add(1, warning_id, 42, "Это было не спам")
        appeal = await self.repos.appeals.get(appeal_id)
        assert appeal is not None
        self.assertEqual(appeal.status, "pending")
        self.assertIsNone(appeal.channel_id)

        await self.repos.appeals.set_message(appeal_id, 700, 800)
        by_message = await self.repos.appeals.by_message(800)
        assert by_message is not None
        self.assertEqual(by_message.id, appeal_id)

        self.assertIsNotNone(await self.repos.appeals.pending_for_warning(warning_id))
        self.assertEqual(len(await self.repos.appeals.pending(1)), 1)

        self.assertTrue(await self.repos.appeals.set_status(appeal_id, "approved", 99))
        self.assertFalse(await self.repos.appeals.set_status(appeal_id, "denied", 99))
        self.assertEqual(await self.repos.appeals.pending(1), [])
        self.assertEqual(len(await self.repos.appeals.all_pending()), 0)

        updated = await self.repos.appeals.get(appeal_id)
        assert updated is not None
        self.assertEqual(updated.status, "approved")
        self.assertEqual(updated.reviewed_by, 99)
        self.assertIsNotNone(updated.reviewed_at)


class GiveawayTests(DatabaseTestCase):
    async def test_giveaway_entries_and_due(self) -> None:
        now = datetime.now(timezone.utc)
        giveaway_id = await self.repos.giveaways.create(
            1, 500, "Steam ключ", 2, 42, None, now + timedelta(hours=1)
        )
        giveaway = await self.repos.giveaways.get(giveaway_id)
        assert giveaway is not None
        self.assertFalse(giveaway.ended)

        self.assertTrue(await self.repos.giveaways.add_entry(giveaway_id, 10))
        self.assertFalse(await self.repos.giveaways.add_entry(giveaway_id, 10))
        self.assertTrue(await self.repos.giveaways.add_entry(giveaway_id, 11))
        self.assertTrue(await self.repos.giveaways.has_entry(giveaway_id, 10))
        self.assertEqual(await self.repos.giveaways.entries(giveaway_id), [10, 11])
        self.assertEqual(await self.repos.giveaways.entry_count(giveaway_id), 2)

        self.assertEqual(await self.repos.giveaways.due(to_iso(now)), [])
        due = await self.repos.giveaways.due(to_iso(now + timedelta(hours=2)))
        self.assertEqual([item.id for item in due], [giveaway_id])

        self.assertTrue(await self.repos.giveaways.finish(giveaway_id))
        self.assertFalse(await self.repos.giveaways.finish(giveaway_id))
        self.assertEqual(await self.repos.giveaways.active(1), [])

        self.assertTrue(await self.repos.giveaways.remove_entry(giveaway_id, 10))
        self.assertFalse(await self.repos.giveaways.remove_entry(giveaway_id, 10))


class StatsTests(DatabaseTestCase):
    async def test_upsert_and_remove(self) -> None:
        await self.repos.stats.upsert(1, 600, "members", "Участники: {count}")
        await self.repos.stats.upsert(1, 600, "humans", "Люди: {count}")

        items = await self.repos.stats.list(1)
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0].kind, items[0].template), ("humans", "Люди: {count}"))
        self.assertEqual(len(await self.repos.stats.all()), 1)

        self.assertTrue(await self.repos.stats.remove(1, 600))
        self.assertFalse(await self.repos.stats.remove(1, 600))
        self.assertEqual(await self.repos.stats.list(1), [])


class ShopTests(DatabaseTestCase):
    async def test_stock_and_purchase(self) -> None:
        item_id = await self.repos.shop.add(1, 70, "VIP", 100, "Описание", 2)
        item = await self.repos.shop.get(item_id)
        assert item is not None
        self.assertEqual((item.price, item.stock), (100, 2))

        self.assertTrue(await self.repos.shop.decrement_stock(item_id))
        self.assertTrue(await self.repos.shop.decrement_stock(item_id))
        self.assertFalse(await self.repos.shop.decrement_stock(item_id))
        item = await self.repos.shop.get(item_id)
        assert item is not None
        self.assertEqual(item.stock, 0)

        await self.repos.shop.record_purchase(1, 42, item_id, 100)

        unlimited_id = await self.repos.shop.add(1, 71, "Безлимит", 10)
        self.assertFalse(await self.repos.shop.decrement_stock(unlimited_id))

        self.assertTrue(await self.repos.shop.remove(1, item_id))
        self.assertFalse(await self.repos.shop.remove(1, item_id))


class CaptchaTests(DatabaseTestCase):
    async def test_pending(self) -> None:
        now = datetime.now(timezone.utc)
        await self.repos.captcha.upsert(1, 42, "ABCDE", now + timedelta(minutes=10))
        pending = await self.repos.captcha.get(1, 42)
        assert pending is not None
        self.assertEqual(pending.code, "ABCDE")
        self.assertEqual(await self.repos.captcha.due(to_iso(now)), [])

        await self.repos.captcha.upsert(1, 42, "FGHJK", now + timedelta(minutes=5))
        pending = await self.repos.captcha.get(1, 42)
        assert pending is not None
        self.assertEqual(pending.code, "FGHJK")

        due = await self.repos.captcha.due(to_iso(now + timedelta(minutes=10)))
        self.assertEqual(len(due), 1)
        await self.repos.captcha.delete(1, 42)
        self.assertIsNone(await self.repos.captcha.get(1, 42))


class DatabaseDialectTests(unittest.TestCase):
    def test_placeholders(self) -> None:
        self.assertEqual(
            Database.translate_sql("SELECT * FROM warnings WHERE guild_id = ? AND id = ?"),
            "SELECT * FROM warnings WHERE guild_id = $1 AND id = $2",
        )

    def test_insert_or_ignore(self) -> None:
        self.assertEqual(
            Database.translate_sql("INSERT OR IGNORE INTO banned_words (guild_id, word) VALUES (?, ?)"),
            "INSERT INTO banned_words (guild_id, word) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        )


class CommandAccessTests(DatabaseTestCase):
    async def test_rule_crud(self) -> None:
        rule = CommandRule(
            guild_id=1,
            command="levelconfig add-xp",
            allow_roles={10, 20},
            allow_users={42},
            deny_roles={30},
            enabled=True,
        )
        await self.repos.access.save(rule)
        loaded = await self.repos.access.get(1, "/levelconfig   add-xp")
        assert loaded is not None
        self.assertEqual(loaded.allow_roles, {10, 20})
        self.assertEqual(loaded.allow_users, {42})
        self.assertEqual(loaded.deny_roles, {30})
        self.assertTrue(loaded.enabled)

        rules = await self.repos.access.rules(1)
        self.assertEqual(set(rules), {"levelconfig add-xp"})

        rule.enabled = False
        await self.repos.access.save(rule)
        loaded = await self.repos.access.get(1, "levelconfig add-xp")
        assert loaded is not None
        self.assertFalse(loaded.enabled)

        self.assertTrue(await self.repos.access.delete(1, "levelconfig add-xp"))
        self.assertFalse(await self.repos.access.delete(1, "levelconfig add-xp"))
        self.assertEqual(await self.repos.access.rules(1), {})

    async def test_guild_isolation_and_clear(self) -> None:
        await self.repos.access.save(CommandRule(1, "warn", allow_roles={10}))
        await self.repos.access.save(CommandRule(2, "warn", allow_roles={20}))
        self.assertEqual(len(await self.repos.access.rules(1)), 1)
        self.assertEqual(len(await self.repos.access.rules(2)), 1)
        self.assertEqual(await self.repos.access.delete_all(1), 1)
        self.assertEqual(await self.repos.access.rules(1), {})


if __name__ == "__main__":
    unittest.main()

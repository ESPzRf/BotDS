"""Тесты чистых функций: уровни, лояльность."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from core.leveling import (
    best_role_multiplier,
    clamp_multiplier,
    combined_multiplier,
    event_active,
    level_from_total_xp,
    level_title,
    loyalty_reward,
    loyalty_tier,
    progress,
    scaled_amount,
    total_xp_for_level,
    xp_for_next_level,
)


class LevelingTests(unittest.TestCase):
    def test_curve_roundtrip(self) -> None:
        for level in range(0, 120):
            total = total_xp_for_level(level)
            self.assertEqual(level_from_total_xp(total), level, f"level={level}")
            self.assertEqual(
                level_from_total_xp(total + xp_for_next_level(level) - 1),
                level,
                f"level={level}",
            )
            if level > 0:
                self.assertEqual(level_from_total_xp(total - 1), level - 1, f"level={level}")

    def test_curve_is_monotonic(self) -> None:
        previous = -1
        for total in range(0, 5000, 37):
            level = level_from_total_xp(total)
            self.assertGreaterEqual(level, previous)
            previous = level

    def test_progress_values(self) -> None:
        zero = progress(0)
        self.assertEqual((zero.level, zero.xp_into_level, zero.xp_needed, zero.xp_to_next), (0, 0, 100, 100))
        self.assertEqual(zero.ratio, 0.0)

        almost = progress(99)
        self.assertEqual((almost.level, almost.xp_into_level, almost.xp_to_next), (0, 99, 1))

        level_one = progress(100)
        self.assertEqual((level_one.level, level_one.xp_into_level, level_one.xp_needed), (1, 0, 150))

        level_one_end = progress(249)
        self.assertEqual((level_one_end.level, level_one_end.xp_to_next), (1, 1))

        level_two = progress(250)
        self.assertEqual((level_two.level, level_two.xp_into_level), (2, 0))

    def test_negative_xp_is_zero(self) -> None:
        self.assertEqual(level_from_total_xp(-500), 0)
        self.assertEqual(progress(-10).level, 0)

    def test_level_titles(self) -> None:
        self.assertEqual(level_title(0), "Новичок")
        self.assertEqual(level_title(4), "Новичок")
        self.assertEqual(level_title(10), "Активист")
        self.assertEqual(level_title(55), "Легенда")
        self.assertEqual(level_title(1000), "Легенда")

    def test_loyalty_tiers(self) -> None:
        bronze = loyalty_tier(0)
        self.assertEqual((bronze.name, bronze.next_threshold), ("Бронза", 100))

        silver = loyalty_tier(100)
        self.assertEqual((silver.name, silver.next_threshold), ("Серебро", 500))

        diamond = loyalty_tier(5000)
        self.assertEqual(diamond.name, "Алмаз")
        self.assertIsNone(diamond.next_threshold)

    def test_loyalty_reward(self) -> None:
        self.assertEqual(loyalty_reward(1, 10, 2, 50), 10)
        self.assertEqual(loyalty_reward(2, 10, 2, 50), 12)
        self.assertEqual(loyalty_reward(100, 10, 2, 50), 50)
        self.assertEqual(loyalty_reward(0, 10, 2, 50), 10)


class XpBoostTests(unittest.TestCase):
    def test_clamp(self) -> None:
        self.assertEqual(clamp_multiplier(2.5), 2.5)
        self.assertEqual(clamp_multiplier(0), 0.0)
        self.assertEqual(clamp_multiplier(-5), 0.0)
        self.assertEqual(clamp_multiplier(50), 10.0)
        self.assertEqual(clamp_multiplier("bad"), 1.0)

    def test_combined(self) -> None:
        self.assertAlmostEqual(combined_multiplier(2, 3), 6.0)
        self.assertAlmostEqual(combined_multiplier(10, 10, 10), 100.0)
        self.assertEqual(combined_multiplier(0, 5), 0.0)
        self.assertAlmostEqual(combined_multiplier(1.5, 1.2), 1.8)

    def test_scaled_amount(self) -> None:
        self.assertEqual(scaled_amount(20, 1.5), 30)
        self.assertEqual(scaled_amount(20, 0), 0)
        self.assertEqual(scaled_amount(0, 5), 0)
        self.assertEqual(scaled_amount(1, 0.1), 1)

    def test_best_role(self) -> None:
        boosts = {"10": 2.5, "20": 1.5, "30": "bad"}
        self.assertEqual(best_role_multiplier([10, 20], boosts), 2.5)
        self.assertEqual(best_role_multiplier([20], boosts), 1.5)
        self.assertEqual(best_role_multiplier([999], boosts), 1.0)
        self.assertEqual(best_role_multiplier([10], None), 1.0)

    def test_event_active(self) -> None:
        now = datetime.now(timezone.utc)
        self.assertTrue(event_active(now + timedelta(hours=1), now))
        self.assertFalse(event_active(now - timedelta(hours=1), now))
        self.assertFalse(event_active(None, now))


if __name__ == "__main__":
    unittest.main()

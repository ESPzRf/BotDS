"""Тесты выбора победителей розыгрыша."""
from __future__ import annotations

import random
import unittest

from core.giveaways import pick_winners


class PickWinnersTests(unittest.TestCase):
    def test_empty_and_zero(self) -> None:
        self.assertEqual(pick_winners([], 3), [])
        self.assertEqual(pick_winners([1, 2], 0), [])

    def test_returns_unique_subset(self) -> None:
        entries = list(range(1, 21))
        winners = pick_winners(entries, 5, rng=random.Random(42))
        self.assertEqual(len(winners), 5)
        self.assertEqual(len(set(winners)), 5)
        self.assertTrue(set(winners).issubset(entries))

    def test_more_winners_than_entries(self) -> None:
        self.assertEqual(sorted(pick_winners([1, 2, 3], 10, rng=random.Random(1))), [1, 2, 3])

    def test_deterministic_with_seed(self) -> None:
        entries = list(range(100))
        first = pick_winners(entries, 3, rng=random.Random(7))
        second = pick_winners(entries, 3, rng=random.Random(7))
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()

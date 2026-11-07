"""Тесты скользящего окна антиспама/антирейда."""
from __future__ import annotations

import unittest

from core.ratelimit import SlidingWindow


class SlidingWindowTests(unittest.TestCase):
    def test_counts_inside_window(self) -> None:
        window = SlidingWindow()
        self.assertEqual(window.hit("user", 10, now=0), 1)
        self.assertEqual(window.hit("user", 10, now=1), 2)
        self.assertEqual(window.hit("user", 10, now=2), 3)
        self.assertEqual(window.count("user", 10, now=2), 3)

    def test_old_events_expire(self) -> None:
        window = SlidingWindow()
        window.hit("user", 10, now=0)
        window.hit("user", 10, now=1)
        self.assertEqual(window.count("user", 10, now=15), 0)
        self.assertEqual(window.hit("user", 10, now=15), 1)

    def test_keys_are_independent(self) -> None:
        window = SlidingWindow()
        window.hit("a", 10, now=0)
        window.hit("a", 10, now=1)
        self.assertEqual(window.count("b", 10, now=1), 0)
        self.assertEqual(window.count("a", 10, now=1), 2)

    def test_reset_and_cleanup(self) -> None:
        window = SlidingWindow()
        window.hit("a", 10, now=0)
        window.hit("b", 10, now=0)
        window.reset("a")
        self.assertEqual(window.count("a", 10, now=1), 0)
        window.cleanup(now=10000, max_age=10)
        self.assertEqual(len(window), 0)


if __name__ == "__main__":
    unittest.main()

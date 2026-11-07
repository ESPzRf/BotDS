"""Тесты парсинга длительностей."""
from __future__ import annotations

import unittest

from core.timeparse import format_duration, parse_duration


class ParseDurationTests(unittest.TestCase):
    def test_common_formats(self) -> None:
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("90m"), 5400)
        self.assertEqual(parse_duration("1h 30m"), 5400)
        self.assertEqual(parse_duration("30 мин"), 1800)
        self.assertEqual(parse_duration("2 дня"), 172800)
        self.assertEqual(parse_duration("1w"), 604800)
        self.assertEqual(parse_duration("45s"), 45)
        self.assertEqual(parse_duration("1ч30мин"), 5400)

    def test_invalid(self) -> None:
        for value in ["", "10", "abc", "1h garbage", "0m", "-5m", "1.5h"]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_duration(value)


class FormatDurationTests(unittest.TestCase):
    def test_format(self) -> None:
        self.assertEqual(format_duration(0), "0 сек")
        self.assertEqual(format_duration(45), "45 сек")
        self.assertEqual(format_duration(90), "1 мин 30 сек")
        self.assertEqual(format_duration(3661), "1 ч 1 мин")
        self.assertEqual(format_duration(86400 + 3600), "1 д 1 ч")


if __name__ == "__main__":
    unittest.main()

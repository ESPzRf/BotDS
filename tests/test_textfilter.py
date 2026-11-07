"""Тесты фильтра запрещённых слов."""
from __future__ import annotations

import unittest

from core.textfilter import WordMatcher, normalize


class NormalizeTests(unittest.TestCase):
    def test_lowercase(self) -> None:
        self.assertEqual(normalize("ПРИВЕТ Мир"), "привет мир")

    def test_homoglyphs_are_kept_in_display_text(self) -> None:
        # Читаемое представление не должно превращать кириллицу в латиницу.
        self.assertEqual(normalize("аss"), "аss")
        self.assertEqual(normalize("дурак"), "дурак")

    def test_invisible_characters(self) -> None:
        self.assertEqual(normalize("a\u200bss"), "ass")
        self.assertEqual(normalize("a\u00adss"), "ass")

    def test_leet(self) -> None:
        self.assertEqual(normalize("sh1t"), "shit")
        self.assertEqual(normalize("4ss"), "ass")

    def test_whitespace(self) -> None:
        self.assertEqual(normalize("  много   пробелов  "), "много пробелов")

    def test_empty(self) -> None:
        self.assertEqual(normalize(""), "")
        self.assertEqual(normalize("\u200b\u200b"), "")


class WordMatcherTests(unittest.TestCase):
    def test_word_boundaries(self) -> None:
        matcher = WordMatcher(["ass"])
        self.assertTrue(matcher.matches("ты ass!"))
        self.assertFalse(matcher.matches("class"))
        self.assertFalse(matcher.matches("passenger"))

    def test_cyrillic(self) -> None:
        matcher = WordMatcher(["дурак"])
        self.assertTrue(matcher.matches("Ты ДУРАК!"))
        self.assertFalse(matcher.matches("дураки"))
        self.assertFalse(matcher.matches("недурак"))

    def test_homoglyph_bypass(self) -> None:
        matcher = WordMatcher(["ass"])
        self.assertTrue(matcher.matches("аss"))  # кириллическая «а»
        self.assertTrue(matcher.matches("a\u200bss"))

    def test_leet_bypass(self) -> None:
        matcher = WordMatcher(["shit"])
        self.assertTrue(matcher.matches("sh1t"))

    def test_phrase(self) -> None:
        matcher = WordMatcher(["плохое слово"])
        self.assertTrue(matcher.matches("это плохое слово, да"))
        self.assertFalse(matcher.matches("плохоеслово"))

    def test_wildcard(self) -> None:
        matcher = WordMatcher(["дур*"])
        self.assertTrue(matcher.matches("дурак"))
        self.assertTrue(matcher.matches("дурость"))
        self.assertFalse(matcher.matches("дуб"))

    def test_find_returns_original(self) -> None:
        matcher = WordMatcher(["Дурак", "ass"])
        self.assertEqual(matcher.find("ну ты ДУРАК"), "Дурак")
        self.assertIsNone(matcher.find("всё хорошо"))

    def test_empty_matcher(self) -> None:
        matcher = WordMatcher([])
        self.assertFalse(matcher)
        self.assertEqual(len(matcher), 0)
        self.assertIsNone(matcher.find("любой текст"))

    def test_duplicates_are_collapsed(self) -> None:
        matcher = WordMatcher(["ass", "ASS", "аss"])
        self.assertEqual(len(matcher), 1)


if __name__ == "__main__":
    unittest.main()

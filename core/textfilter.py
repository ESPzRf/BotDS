"""Нормализация текста и поиск запрещённых слов.

Фильтр устойчив к простым обходам:
  * верхний/нижний регистр и полноширинные символы (NFKC);
  * zero-width символы внутри слова;
  * leet-замены цифрами (sh1t -> shit);
  * кириллические/латинские похожие буквы (а -> a, е -> e, ...).

Для последнего используется два представления текста: обычное (читаемое)
и «скелет», где похожие буквы заменены на латиницу. Слово ищется в обоих,
поэтому «аss» (кириллическая «а») совпадает с «ass», а русские слова
продолжают нормально отображаться в логах.

Сопоставление по умолчанию идёт по границам слова, чтобы «ass» не срабатывал
на «class». Символ '*' в слове включает режим подстроки: "дур*".
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

# Похожие буквы: кириллица -> латиница (после casefold).
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a",
        "в": "b",
        "г": "r",
        "е": "e",
        "ё": "e",
        "к": "k",
        "м": "m",
        "н": "h",
        "о": "o",
        "р": "p",
        "с": "c",
        "т": "t",
        "у": "y",
        "х": "x",
        "і": "i",
        "ј": "j",
        "ѕ": "s",
        "ԁ": "d",
        "ɡ": "g",
        "ӏ": "l",
        "ԛ": "q",
        "ԝ": "w",
        "ь": "b",
        "ъ": "b",
    }
)

_LEET = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "8": "b",
        "9": "g",
    }
)

_INVISIBLE = dict.fromkeys(
    [ord(ch) for ch in "\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad\u180e"],
    None,
)

_WHITESPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Читаемая нормализация: регистр, невидимые символы, leet, пробелы."""

    if not text:
        return ""

    text = unicodedata.normalize("NFKC", str(text)).casefold()
    text = text.translate(_INVISIBLE)
    text = text.translate(_LEET)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def skeleton(text: str) -> str:
    """Нормализация + замена похожих кириллических букв на латиницу."""

    return normalize(text).translate(_HOMOGLYPHS)


class WordMatcher:
    """Скомпилированный набор запрещённых слов для одного сервера."""

    def __init__(self, words: Iterable[str]) -> None:
        self._patterns: list[tuple[str, list[re.Pattern[str]]]] = []
        seen: set[str] = set()

        for raw in words:
            if not raw:
                continue
            term = normalize(raw)
            if not term:
                continue

            # Слова, различающиеся только похожими буквами, — дубликаты.
            key = term.translate(_HOMOGLYPHS)
            if key in seen:
                continue
            seen.add(key)

            variants = {term, key}
            patterns = [self._compile(variant) for variant in variants]
            self._patterns.append((str(raw), patterns))

    @staticmethod
    def _compile(term: str) -> re.Pattern[str]:
        if "*" in term:
            return re.compile(re.escape(term).replace(r"\*", ".*"), re.DOTALL)
        return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.DOTALL)

    def find(self, text: str) -> str | None:
        """Возвращает первое сработавшее слово (в исходном написании)."""

        if not self._patterns or not text:
            return None

        normalized = normalize(text)
        folded = normalized.translate(_HOMOGLYPHS)

        for raw, patterns in self._patterns:
            for pattern in patterns:
                if pattern.search(normalized) or pattern.search(folded):
                    return raw
        return None

    def matches(self, text: str) -> bool:
        return self.find(text) is not None

    def __len__(self) -> int:
        return len(self._patterns)

    def __bool__(self) -> bool:
        return bool(self._patterns)

"""Мелкие утилиты без зависимостей от discord."""

from __future__ import annotations

import re
from typing import Iterable, Iterator, Sequence, TypeVar

T = TypeVar("T")

_HEX_COLOR_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def truncate(text: str | None, limit: int = 1024, suffix: str = "…") -> str:
    """Обрезает текст до лимита Discord, не оставляя пустую строку."""

    if text is None:
        return ""
    value = str(text)
    if len(value) <= limit:
        return value
    if limit <= len(suffix):
        return value[:limit]
    return value[: limit - len(suffix)] + suffix


def chunk(items: Sequence[T], size: int) -> Iterator[list[T]]:
    if size <= 0:
        raise ValueError("size должен быть больше нуля")
    for index in range(0, len(items), size):
        yield list(items[index : index + size])


def parse_color(value: str) -> int:
    """'#5865F2' / '5865f2' -> int. Бросает ValueError."""

    match = _HEX_COLOR_RE.match(value.strip())
    if not match:
        raise ValueError("Цвет должен быть в формате HEX, например #5865F2")
    return int(match.group(1), 16)


def format_number(value: int | float) -> str:
    """1234567 -> '1 234 567'."""

    return f"{int(value):,}".replace(",", " ")


def plural_ru(number: int, one: str, few: str, many: str) -> str:
    """Склонение существительных: plural_ru(3, 'слово', 'слова', 'слов')."""

    number = abs(int(number))
    if 11 <= number % 100 <= 14:
        return many
    last = number % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def join_nonempty(parts: Iterable[str | None], separator: str = " ") -> str:
    return separator.join(part for part in parts if part)

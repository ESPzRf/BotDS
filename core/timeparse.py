"""Парсинг и форматирование длительностей: 1h30m, 2 дня, 90 мин."""

from __future__ import annotations

import re

_UNIT_SECONDS: dict[str, int] = {
    # секунды
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "с": 1, "сек": 1, "секунд": 1, "секунды": 1, "секунда": 1,
    # минуты
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "м": 60, "мин": 60, "минут": 60, "минуты": 60, "минута": 60,
    # часы
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "ч": 3600, "час": 3600, "часа": 3600, "часов": 3600,
    # дни
    "d": 86400, "day": 86400, "days": 86400,
    "д": 86400, "день": 86400, "дня": 86400, "дней": 86400,
    # недели
    "w": 604800, "week": 604800, "weeks": 604800,
    "н": 604800, "нед": 604800, "неделя": 604800, "недели": 604800, "недель": 604800,
}

_PART_RE = re.compile(r"(\d+)\s*([a-zA-Zа-яА-Я]+)")


def parse_duration(value: str) -> int:
    """Возвращает длительность в секундах.

    Примеры: "10m", "1h 30m", "2 дня", "1w".
    Бросает ValueError, если строка не распознана.
    """

    if value is None:
        raise ValueError("Пустая длительность")

    text = str(value).strip().lower().replace(" ", "")
    if not text:
        raise ValueError("Пустая длительность")

    total = 0
    position = 0
    for match in _PART_RE.finditer(text):
        if match.start() != position:
            raise ValueError(f"Не удалось разобрать длительность: {value}")
        position = match.end()

        amount = int(match.group(1))
        unit = match.group(2)
        if unit not in _UNIT_SECONDS:
            raise ValueError(f"Неизвестная единица времени: {unit}")
        total += amount * _UNIT_SECONDS[unit]

    if position != len(text):
        raise ValueError(f"Не удалось разобрать длительность: {value}")
    if total <= 0:
        raise ValueError("Длительность должна быть больше нуля")

    return total


def format_duration(seconds: int) -> str:
    """Компактный русский формат: 5400 -> '1 ч 30 мин'."""

    seconds = int(max(0, seconds))
    if seconds == 0:
        return "0 сек"

    parts: list[str] = []
    for unit, label in ((86400, "д"), (3600, "ч"), (60, "мин"), (1, "сек")):
        if seconds >= unit:
            amount, seconds = divmod(seconds, unit)
            parts.append(f"{amount} {label}")
            if len(parts) == 2:
                break

    return " ".join(parts)


def format_timedelta_ru(seconds: int) -> str:
    """Более человечный формат для откатов: 'через 2 ч 5 мин'."""
    return format_duration(seconds)

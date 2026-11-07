"""Математика уровней и лояльности (без зависимостей от discord)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

# Опыт для перехода с уровня L на L+1: 100 + 50*L.
# Суммарный опыт до уровня L: 25*L^2 + 75*L.
BASE_XP = 100
STEP_XP = 50

MIN_MULTIPLIER = 0.0
MAX_MULTIPLIER = 10.0
MAX_TOTAL_MULTIPLIER = 100.0


def clamp_multiplier(value: float, *, minimum: float = MIN_MULTIPLIER, maximum: float = MAX_MULTIPLIER) -> float:
    """Ограничивает множитель разумным диапазоном и защищает от NaN."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1.0
    if math.isnan(number) or math.isinf(number):
        return 1.0
    return max(minimum, min(maximum, number))


def combined_multiplier(*values: float) -> float:
    """Перемножает множители и ограничивает итог, чтобы не сломать экономику."""

    result = 1.0
    for value in values:
        result *= clamp_multiplier(value)
    return max(MIN_MULTIPLIER, min(MAX_TOTAL_MULTIPLIER, result))


def scaled_amount(base: int, multiplier: float) -> int:
    """Применяет множитель к количеству XP/лояльности."""

    base = max(0, int(base))
    if base == 0:
        return 0
    multiplier = clamp_multiplier(multiplier)
    if multiplier <= 0:
        return 0
    return max(1, int(round(base * multiplier)))


def best_role_multiplier(role_ids: Iterable[int], boosts: Mapping[str, object] | None) -> float:
    """Максимальный бонус среди ролей участника (без суммирования)."""

    if not boosts:
        return 1.0
    best = 1.0
    for role_id in role_ids:
        raw = boosts.get(str(role_id), boosts.get(role_id, 1.0))  # type: ignore[arg-type]
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > best:
            best = value
    return clamp_multiplier(best)


def event_active(until: datetime | None, now: datetime) -> bool:
    if until is None:
        return False
    if until.tzinfo is None or now.tzinfo is None:
        return until > now
    return until > now


def xp_for_next_level(level: int) -> int:
    return BASE_XP + STEP_XP * max(level, 0)


def total_xp_for_level(level: int) -> int:
    level = max(level, 0)
    return 25 * level * level + 75 * level


def level_from_total_xp(total_xp: int) -> int:
    """Обратная функция к total_xp_for_level (целочисленная, без float)."""
    total_xp = max(total_xp, 0)
    # Решение 25L^2 + 75L = XP  =>  L = (-75 + sqrt(5625 + 100*XP)) / 50
    discriminant = 5625 + 100 * total_xp
    return max(0, (math.isqrt(discriminant) - 75) // 50)


@dataclass(frozen=True, slots=True)
class LevelProgress:
    level: int
    xp_total: int
    xp_into_level: int
    xp_needed: int
    xp_to_next: int

    @property
    def ratio(self) -> float:
        if self.xp_needed <= 0:
            return 1.0
        return min(1.0, max(0.0, self.xp_into_level / self.xp_needed))


def progress(total_xp: int) -> LevelProgress:
    total_xp = max(total_xp, 0)
    level = level_from_total_xp(total_xp)
    base = total_xp_for_level(level)
    needed = xp_for_next_level(level)
    into = total_xp - base
    return LevelProgress(
        level=level,
        xp_total=total_xp,
        xp_into_level=into,
        xp_needed=needed,
        xp_to_next=max(0, needed - into),
    )


_LEVEL_TITLES: list[tuple[int, str]] = [
    (0, "Новичок"),
    (5, "Участник"),
    (10, "Активист"),
    (20, "Ветеран"),
    (30, "Проверенный"),
    (40, "Элита"),
    (55, "Легенда"),
]


def level_title(level: int) -> str:
    title = "Новичок"
    for threshold, name in _LEVEL_TITLES:
        if level >= threshold:
            title = name
        else:
            break
    return title


@dataclass(frozen=True, slots=True)
class LoyaltyTier:
    name: str
    emoji: str
    threshold: int
    next_threshold: int | None
    next_name: str | None


_LOYALTY_TIERS: list[tuple[int, str, str]] = [
    (0, "Бронза", "🥉"),
    (100, "Серебро", "🥈"),
    (500, "Золото", "🥇"),
    (1500, "Платина", "💎"),
    (5000, "Алмаз", "🏆"),
]


def loyalty_tier(points: int) -> LoyaltyTier:
    points = max(points, 0)
    current_index = 0
    for index, (threshold, _name, _emoji) in enumerate(_LOYALTY_TIERS):
        if points >= threshold:
            current_index = index
        else:
            break

    threshold, name, emoji = _LOYALTY_TIERS[current_index]
    if current_index + 1 < len(_LOYALTY_TIERS):
        next_threshold, next_name, _ = _LOYALTY_TIERS[current_index + 1]
    else:
        next_threshold, next_name = None, None

    return LoyaltyTier(
        name=name,
        emoji=emoji,
        threshold=threshold,
        next_threshold=next_threshold,
        next_name=next_name,
    )


def loyalty_reward(streak: int, base: int, step: int, cap: int) -> int:
    """Награда за /daily: растёт со стриком, но не выше cap."""
    streak = max(streak, 1)
    return max(1, min(cap, base + step * (streak - 1)))

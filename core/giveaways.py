"""Чистая логика розыгрышей."""

from __future__ import annotations

import random
from typing import Sequence


def pick_winners(
    entries: Sequence[int],
    count: int,
    *,
    rng: random.Random | None = None,
) -> list[int]:
    """Выбирает до count уникальных победителей из списка участников."""

    if not entries or count <= 0:
        return []
    generator = rng or random
    return generator.sample(list(entries), k=min(count, len(entries)))

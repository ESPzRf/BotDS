"""Скользящее окно для антиспама и антирейда (без discord)."""

from __future__ import annotations

import time
from collections import deque
from typing import Hashable


class SlidingWindow:
    """Считает события по ключу в скользящем временном окне."""

    def __init__(self) -> None:
        self._events: dict[Hashable, deque[float]] = {}

    def hit(self, key: Hashable, window: float, *, now: float | None = None) -> int:
        """Регистрирует событие и возвращает количество событий в окне."""

        timestamp = time.monotonic() if now is None else now
        bucket = self._events.setdefault(key, deque())
        while bucket and timestamp - bucket[0] > window:
            bucket.popleft()
        bucket.append(timestamp)
        return len(bucket)

    def count(self, key: Hashable, window: float, *, now: float | None = None) -> int:
        timestamp = time.monotonic() if now is None else now
        bucket = self._events.get(key)
        if not bucket:
            return 0
        while bucket and timestamp - bucket[0] > window:
            bucket.popleft()
        return len(bucket)

    def reset(self, key: Hashable) -> None:
        self._events.pop(key, None)

    def cleanup(self, *, now: float | None = None, max_age: float = 3600) -> None:
        timestamp = time.monotonic() if now is None else now
        for key in list(self._events):
            bucket = self._events[key]
            while bucket and timestamp - bucket[0] > max_age:
                bucket.popleft()
            if not bucket:
                self._events.pop(key, None)

    def __len__(self) -> int:
        return len(self._events)

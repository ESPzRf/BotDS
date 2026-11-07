"""Общие исключения."""

from __future__ import annotations


class UserError(Exception):
    """Ошибка, текст которой безопасно показать пользователю в Discord."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - тривиально
        return self.message

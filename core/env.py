"""Небольшой загрузчик .env без внешних зависимостей."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path, *, override: bool = False) -> None:
    """Читает файл вида KEY=VALUE и кладёт значения в os.environ.

    Уже существующие переменные окружения по умолчанию не перезаписываются,
    поэтому настройки можно переопределять из системы/докера.
    """

    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()

        key, separator, value = line.partition("=")
        if not separator:
            continue

        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        value = value.replace("\\n", "\n")

        if override or key not in os.environ:
            os.environ[key] = value

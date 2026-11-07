"""Мини-фреймворк локализации без внешних зависимостей.

Каталоги лежат в `locales/<lang>.json` и могут быть вложенными:
    {"web": {"title": "..."}}
Ключ для `t()` — через точку: `web.title`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_LOCALE = "ru"


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            result.update(_flatten(value, full_key))
        elif isinstance(value, (str, int, float, bool)):
            result[full_key] = str(value)
    return result


class I18n:
    def __init__(self, directory: str | Path, default: str = DEFAULT_LOCALE) -> None:
        self.directory = Path(directory)
        self.default = default
        self._catalogs: dict[str, dict[str, str]] = {}

        if self.directory.is_dir():
            for path in sorted(self.directory.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    log.exception("Не удалось загрузить каталог локализации %s", path)
                    continue
                self._catalogs[path.stem.lower()] = _flatten(data)

        if self.default not in self._catalogs and self._catalogs:
            self.default = next(iter(self._catalogs))

        if not self._catalogs:
            log.warning("Каталоги локализации не найдены в %s", self.directory)

    @property
    def languages(self) -> tuple[str, ...]:
        return tuple(sorted(self._catalogs))

    def normalize(self, locale: str | None) -> str:
        if not locale:
            return self.default
        base = str(locale).strip().lower().replace("_", "-").split("-")[0]
        return base if base in self._catalogs else self.default

    def has(self, locale: str, key: str) -> bool:
        return key in self._catalogs.get(self.normalize(locale), {})

    def raw(self, locale: str, key: str) -> str | None:
        locale = self.normalize(locale)
        if key in self._catalogs.get(locale, {}):
            return self._catalogs[locale][key]
        if key in self._catalogs.get(self.default, {}):
            return self._catalogs[self.default][key]
        return None

    def t(self, locale: str | None, key: str, **kwargs: Any) -> str:
        template = self.raw(locale or self.default, key)
        if template is None:
            log.debug("Нет перевода для ключа %s (%s)", key, locale)
            return key
        if not kwargs:
            return template
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            log.warning("Ошибка форматирования перевода %s: %r", key, template)
            return template

    def language_name(self, locale: str) -> str:
        names = {"ru": "Русский", "en": "English"}
        return names.get(self.normalize(locale), self.normalize(locale))

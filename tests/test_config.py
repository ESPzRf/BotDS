"""Тесты конфигурации (без Discord)."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from config import Config, ConfigError

DATA_DIR = Path(__file__).resolve().parent / "_data"


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._saved = {key: os.environ.get(key) for key in ("DISCORD_TOKEN", "DATABASE_URL", "DB_PATH")}
        for key in self._saved:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_missing_token(self) -> None:
        with self.assertRaises(ConfigError):
            Config.load(DATA_DIR)

    def test_postgres_url(self) -> None:
        os.environ["DISCORD_TOKEN"] = "test-token"
        os.environ["DATABASE_URL"] = "postgresql://user:pass@localhost:5432/db"
        config = Config.load(DATA_DIR)
        self.assertEqual(config.database_url, "postgresql://user:pass@localhost:5432/db")

    def test_invalid_database_url(self) -> None:
        os.environ["DISCORD_TOKEN"] = "test-token"
        os.environ["DATABASE_URL"] = "mysql://user:pass@localhost/db"
        with self.assertRaises(ConfigError):
            Config.load(DATA_DIR)

    def test_sqlite_default(self) -> None:
        os.environ["DISCORD_TOKEN"] = "test-token"
        config = Config.load(DATA_DIR)
        self.assertIsNone(config.database_url)
        self.assertEqual(config.db_path, DATA_DIR / "data" / "bot.db")


if __name__ == "__main__":
    unittest.main()

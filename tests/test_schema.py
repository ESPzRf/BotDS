"""Схемы SQLite и PostgreSQL должны содержать одинаковый набор таблиц."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "core"
TABLE_RE = re.compile(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-z_]+)", re.IGNORECASE)


def table_names(path: Path) -> set[str]:
    return set(TABLE_RE.findall(path.read_text(encoding="utf-8")))


class SchemaParityTests(unittest.TestCase):
    def test_same_tables(self) -> None:
        sqlite = table_names(CORE / "schema.sql")
        postgres = table_names(CORE / "schema_postgres.sql")
        self.assertTrue(sqlite)
        self.assertEqual(sqlite, postgres)


if __name__ == "__main__":
    unittest.main()

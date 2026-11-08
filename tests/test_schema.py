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

    def test_migration_covers_all_tables(self) -> None:
        sqlite = table_names(CORE / "schema.sql") - {"meta"}
        migration = (CORE.parent / "scripts" / "migrate_sqlite_to_postgres.py").read_text(
            encoding="utf-8"
        )
        missing = sorted(table for table in sqlite if f'"{table}"' not in migration)
        self.assertEqual(missing, [], f"Нет в migrate_sqlite_to_postgres.py: {missing}")


if __name__ == "__main__":
    unittest.main()

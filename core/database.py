"""Слой доступа к данным: SQLite (по умолчанию) и PostgreSQL.

Для SQLite используется встроенный sqlite3 + asyncio.to_thread.
Для PostgreSQL — asyncpg (устанавливается отдельно, см. requirements-postgres.txt).

Репозитории пишут SQL в общем для двух СУБД виде: `?`-плейсхолдеры,
`INSERT OR IGNORE`, TEXT-время в ISO 8601. Для PostgreSQL слой сам
переводит плейсхолдеры в `$1..$N` и `INSERT OR IGNORE` в
`INSERT ... ON CONFLICT DO NOTHING`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

SCHEMA_SQLITE = Path(__file__).with_name("schema.sql")
SCHEMA_POSTGRES = Path(__file__).with_name("schema_postgres.sql")
SCHEMA_VERSION = 2


class Database:
    """Единый асинхронный интерфейс к SQLite или PostgreSQL."""

    def __init__(self, target: str | Path) -> None:
        self.target = str(target)
        self.is_postgres = self.target.startswith(("postgres://", "postgresql://"))

        self._sqlite: sqlite3.Connection | None = None
        self._postgres: Any | None = None
        self._postgres_lock: asyncio.Lock | None = None
        self._thread_lock = threading.Lock()

    # ------------------------------------------------------------------ connect

    async def connect(self) -> None:
        if self.is_postgres:
            if self._postgres is not None:
                return
            await self._connect_postgres()
        else:
            if self._sqlite is not None:
                return
            await asyncio.to_thread(self._connect_sqlite)

    def _connect_sqlite(self) -> None:
        if self.target != ":memory:":
            Path(self.target).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(
            self.target,
            check_same_thread=False,
            timeout=30.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(SCHEMA_SQLITE.read_text(encoding="utf-8"))

        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif int(row["value"]) < SCHEMA_VERSION:
            conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )
        conn.commit()

        self._sqlite = conn
        log.info("SQLite подключена: %s", self.target)

    async def _connect_postgres(self) -> None:
        try:
            import asyncpg  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:  # pragma: no cover - зависит от окружения
            raise RuntimeError(
                "Для PostgreSQL нужен пакет asyncpg.\n"
                "Установите его: python -m pip install -r requirements-postgres.txt"
            ) from exc

        dsn = self.target
        if dsn.startswith("postgres://"):
            dsn = "postgresql://" + dsn[len("postgres://") :]

        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(SCHEMA_POSTGRES.read_text(encoding="utf-8"))
            row = await conn.fetchrow("SELECT value FROM meta WHERE key = 'schema_version'")
            if row is None:
                await conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', $1)",
                    str(SCHEMA_VERSION),
                )
            elif int(row["value"]) < SCHEMA_VERSION:
                await conn.execute(
                    "UPDATE meta SET value = $1 WHERE key = 'schema_version'",
                    str(SCHEMA_VERSION),
                )
        except Exception:
            await conn.close()
            raise

        self._postgres = conn
        self._postgres_lock = asyncio.Lock()
        log.info("PostgreSQL подключена")

    # ------------------------------------------------------------------ helpers

    @property
    def _pg(self) -> Any:
        if self._postgres is None:
            raise RuntimeError("PostgreSQL ещё не подключена")
        return self._postgres

    def _pg_guard(self) -> asyncio.Lock:
        if self._postgres_lock is None:
            raise RuntimeError("PostgreSQL ещё не подключена")
        return self._postgres_lock

    @staticmethod
    def translate_sql(sql: str) -> str:
        """Переводит общий SQL в диалект PostgreSQL."""

        ignore = "INSERT OR IGNORE" in sql.upper()
        if ignore:
            sql = re.sub(r"INSERT\s+OR\s+IGNORE", "INSERT", sql, flags=re.IGNORECASE)

        if "?" in sql:
            parts = sql.split("?")
            sql = parts[0] + "".join(
                f"${index}{part}" for index, part in enumerate(parts[1:], start=1)
            )

        if ignore and "ON CONFLICT" not in sql.upper():
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

        return sql

    @staticmethod
    def _status_count(status: str) -> int:
        parts = status.split()
        if parts and parts[-1].isdigit():
            return int(parts[-1])
        return 0

    # ------------------------------------------------------------------ write

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        if self.is_postgres:
            sql = self.translate_sql(sql)
            async with self._pg_guard():
                status = await self._pg.execute(sql, *params)
            return self._status_count(status)

        return await asyncio.to_thread(self._execute_sqlite, sql, params)

    def _execute_sqlite(self, sql: str, params: Sequence[Any]) -> int:
        with self._thread_lock:
            assert self._sqlite is not None
            cursor = self._sqlite.execute(sql, params)
            self._sqlite.commit()
            return cursor.rowcount

    async def insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        """INSERT с возвратом id (lastrowid в SQLite, RETURNING id в PostgreSQL)."""

        if self.is_postgres:
            query = self.translate_sql(sql)
            if "RETURNING" not in query.upper():
                query = query.rstrip().rstrip(";") + " RETURNING id"
            async with self._pg_guard():
                value = await self._pg.fetchval(query, *params)
            if value is None:  # pragma: no cover - INSERT OR IGNORE через insert не используется
                raise RuntimeError("INSERT не вернул id")
            return int(value)

        return await asyncio.to_thread(self._insert_sqlite, sql, params)

    def _insert_sqlite(self, sql: str, params: Sequence[Any]) -> int:
        with self._thread_lock:
            assert self._sqlite is not None
            cursor = self._sqlite.execute(sql, params)
            self._sqlite.commit()
            return int(cursor.lastrowid or 0)

    async def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> int:
        rows = [tuple(row) for row in seq]
        if not rows:
            return 0

        if self.is_postgres:
            query = self.translate_sql(sql)
            async with self._pg_guard():
                await self._pg.executemany(query, rows)
            return len(rows)

        return await asyncio.to_thread(self._executemany_sqlite, sql, rows)

    def _executemany_sqlite(self, sql: str, rows: list[Sequence[Any]]) -> int:
        with self._thread_lock:
            assert self._sqlite is not None
            cursor = self._sqlite.executemany(sql, rows)
            self._sqlite.commit()
            return cursor.rowcount

    # ------------------------------------------------------------------- read

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Any | None:
        if self.is_postgres:
            async with self._pg_guard():
                return await self._pg.fetchrow(self.translate_sql(sql), *params)
        return await asyncio.to_thread(self._fetchone_sqlite, sql, params)

    def _fetchone_sqlite(self, sql: str, params: Sequence[Any]) -> sqlite3.Row | None:
        with self._thread_lock:
            assert self._sqlite is not None
            return self._sqlite.execute(sql, params).fetchone()

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[Any]:
        if self.is_postgres:
            async with self._pg_guard():
                return list(await self._pg.fetch(self.translate_sql(sql), *params))
        return await asyncio.to_thread(self._fetchall_sqlite, sql, params)

    def _fetchall_sqlite(self, sql: str, params: Sequence[Any]) -> list[sqlite3.Row]:
        with self._thread_lock:
            assert self._sqlite is not None
            return self._sqlite.execute(sql, params).fetchall()

    # ------------------------------------------------------------------ close

    async def close(self) -> None:
        if self.is_postgres:
            if self._postgres is not None:
                await self._postgres.close()
                self._postgres = None
                self._postgres_lock = None
                log.info("PostgreSQL закрыта")
            return

        if self._sqlite is not None:
            await asyncio.to_thread(self._close_sqlite)

    def _close_sqlite(self) -> None:
        with self._thread_lock:
            if self._sqlite is not None:
                self._sqlite.close()
                self._sqlite = None
                log.info("SQLite закрыта")

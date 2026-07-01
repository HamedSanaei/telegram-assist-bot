"""Async SQLite connection wrapper built on ``aiosqlite``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import aiosqlite

from src.shared.errors import RepositoryError


class Database:
    """
    Thin async wrapper around one ``aiosqlite`` connection.

    Provides auto-commit ``execute`` helpers with dict-like rows and
    creates the database directory on connect.

    Example:
        db = Database("data/app.db")
        await db.connect()
        rows = await db.fetchall("SELECT * FROM admins")
    """

    def __init__(self, path: str | Path) -> None:
        """
        Args:
            path: SQLite database file path. Use ``":memory:"`` in tests.
        """
        self._path = str(path)
        self._conn: aiosqlite.Connection | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        """Return the live connection, raising when not connected."""
        if self._conn is None:
            raise RepositoryError("Database is not connected")
        return self._conn

    async def connect(self) -> None:
        """
        Open the connection, enable WAL mode and foreign keys.

        Raises:
            RepositoryError: When the database cannot be opened.
        """
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = await aiosqlite.connect(self._path)
        except Exception as exc:
            raise RepositoryError(f"Cannot open SQLite database: {exc}") from exc
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.commit()

    async def close(self) -> None:
        """Close the connection if open."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def execute(self, sql: str, params: Iterable[Any] | dict[str, Any] = ()) -> int:
        """
        Execute a statement and commit.

        Args:
            sql: SQL statement.
            params: Positional or named parameters.

        Returns:
            ``lastrowid`` of the executed statement (0 when not applicable).
        """
        cursor = await self.connection.execute(sql, params)
        await self.connection.commit()
        return cursor.lastrowid or 0

    async def executescript(self, script: str) -> None:
        """Execute a multi-statement SQL script and commit."""
        await self.connection.executescript(script)
        await self.connection.commit()

    async def fetchone(
        self, sql: str, params: Iterable[Any] | dict[str, Any] = ()
    ) -> aiosqlite.Row | None:
        """Execute a query, commit, and return the first row or ``None``."""
        cursor = await self.connection.execute(sql, params)
        row = await cursor.fetchone()
        await self.connection.commit()
        return row

    async def fetchall(
        self, sql: str, params: Iterable[Any] | dict[str, Any] = ()
    ) -> list[aiosqlite.Row]:
        """Execute a query, commit, and return all rows."""
        cursor = await self.connection.execute(sql, params)
        rows = await cursor.fetchall()
        await self.connection.commit()
        return list(rows)

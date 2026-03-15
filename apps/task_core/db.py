"""Database bootstrap for the task core service."""

import logging
from pathlib import Path

import aiosqlite

from config import settings

log = logging.getLogger("task_core.db")

_db: aiosqlite.Connection | None = None

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


async def get_db() -> aiosqlite.Connection:
    """Return a singleton DB connection for the current process."""
    global _db
    if _db is None:
        _db = await aiosqlite.connect(settings.db_path)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
        await _run_migrations(_db)
    return _db


async def close_db() -> None:
    """Close the shared DB connection."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def _run_migrations(db: aiosqlite.Connection) -> None:
    await db.execute(
        "CREATE TABLE IF NOT EXISTS _migrations ("
        "  name TEXT PRIMARY KEY,"
        "  applied_at TEXT DEFAULT (datetime('now'))"
        ")"
    )
    await db.commit()

    applied = {
        row[0]
        async for row in await db.execute("SELECT name FROM _migrations")
    }

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for path in migration_files:
        if path.name in applied:
            continue
        log.info("Applying migration: %s", path.name)
        sql = path.read_text(encoding="utf-8")
        await db.executescript(sql)
        await db.execute(
            "INSERT INTO _migrations (name) VALUES (?)", (path.name,)
        )
        await db.commit()
        log.info("Migration applied: %s", path.name)

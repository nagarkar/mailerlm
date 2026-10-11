"""Store: the one SQLite file in the workspace, and its migrations.

Tables are STRICT with foreign keys on for every connection. `MIGRATIONS` is a list of SQL
scripts; the database's `PRAGMA user_version` counts how many have run, older files are
upgraded on open, one transaction per script, and a file written by newer code is declined.

Decisions:
- Only `initialize` creates a database; `open_store` on a missing file is an error that names
  `mailerlm setup`. Why: a silently created empty store hides a wrong workspace pointer. (owner)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from mailerlm.campaigns import schema as campaigns_schema
from mailerlm.contacts import schema as contacts_schema
from mailerlm.errors import MailerlmError

MIGRATIONS: list[str] = [
    """
    CREATE TABLE store_info (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    ) STRICT;
    """,
    contacts_schema.MIGRATION,
    campaigns_schema.MIGRATION,
]


def _migrate(connection: sqlite3.Connection, migrations: list[str]) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > len(migrations):
        raise MailerlmError(
            f"the database is version {version} but this code knows {len(migrations)}. "
            "Update mailerlm: git pull && .venv/bin/pip install -e '.[dev]'"
        )
    for number in range(version, len(migrations)):
        connection.execute("BEGIN")
        try:
            for statement in migrations[number].split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {number + 1}")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), isolation_level=None)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(path: Path, migrations: list[str] | None = None) -> sqlite3.Connection:
    """Create the database if missing and bring it to the newest version; only `setup` calls this."""
    connection = _connect(path)
    _migrate(connection, MIGRATIONS if migrations is None else migrations)
    return connection


def open_store(path: Path, migrations: list[str] | None = None) -> sqlite3.Connection:
    if not path.exists():
        raise MailerlmError(f"no database at {path}. Run: mailerlm setup")
    connection = _connect(path)
    _migrate(connection, MIGRATIONS if migrations is None else migrations)
    return connection


def version(path: Path) -> int:
    connection = sqlite3.connect(str(path))
    try:
        return connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()

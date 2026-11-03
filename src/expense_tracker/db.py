"""SQLite connections and schema migrations."""

import os
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from importlib import resources
from pathlib import Path

from expense_tracker.backup import create_backup
from expense_tracker.errors import ExpenseError

MIGRATION_NAME = re.compile(r"^(\d{3})_.+\.sql$")
Migration = tuple[int, str]


def connect(db_path: Path) -> sqlite3.Connection:
    # Autocommit leaves every transaction explicit, including around migration scripts.
    conn = sqlite3.connect(db_path, autocommit=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def bundled_migrations() -> list[Migration]:
    migrations = []
    for entry in (resources.files("expense_tracker") / "migrations").iterdir():
        match = MIGRATION_NAME.match(entry.name)
        if match:
            migrations.append((int(match[1]), entry.read_text()))
    return sorted(migrations)


def _current_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL) STRICT"
    )
    return conn.execute("SELECT coalesce(max(version), 0) FROM schema_version").fetchone()[0]


def migrate(
    db_path: Path, backup_dir: Path, migrations: Sequence[Migration] | None = None
) -> int:
    """Bring the database up to date, backing up existing data first."""
    pending_source = bundled_migrations() if migrations is None else list(migrations)
    latest = max((version for version, _ in pending_source), default=0)

    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    is_new = not db_path.exists()
    conn = connect(db_path)
    try:
        if is_new:
            os.chmod(db_path, 0o600)
        version = _current_version(conn)
        if version > latest:
            raise ExpenseError(
                f"The database is at schema version {version}, but this code only knows "
                f"version {latest}. Update the code before using it."
            )
        if version == latest:
            return version
        if version > 0:
            backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            create_backup(db_path, backup_dir)

        with transaction(conn):
            # Re-read inside the lock, in case another client migrated first.
            applied = conn.execute("SELECT coalesce(max(version), 0) FROM schema_version").fetchone()[0]
            for number, script in sorted(pending_source):
                if number > applied:
                    conn.executescript(script)
                    conn.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                        (number, datetime.now().astimezone().isoformat()),
                    )
        return latest
    finally:
        conn.close()

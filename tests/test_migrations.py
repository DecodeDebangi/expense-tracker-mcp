import sqlite3
from pathlib import Path

import pytest

from expense_tracker.db import bundled_migrations, connect, migrate
from expense_tracker.errors import ExpenseError
from expense_tracker.storage import Storage


def expense_columns(db_path: Path) -> set[str]:
    conn = connect(db_path)
    try:
        return {row["name"] for row in conn.execute("PRAGMA table_info(expenses)")}
    finally:
        conn.close()


def test_new_database_gets_schema_and_private_permissions(tmp_path: Path) -> None:
    db = tmp_path / "new" / "expenses.db"
    assert migrate(db, tmp_path / "backups") == bundled_migrations()[-1][0]
    assert "amount_minor" in expense_columns(db)
    assert oct(db.stat().st_mode)[-3:] == "600"
    assert not (tmp_path / "backups").exists()


def test_running_again_changes_nothing(seeded: Storage) -> None:
    settings = seeded.settings
    migrate(settings.db_path, settings.local_backup_dir)
    assert seeded.list_expenses()["total"] == 14
    assert not settings.local_backup_dir.exists()


def test_new_migration_backs_up_first_and_keeps_data(seeded: Storage) -> None:
    settings = seeded.settings
    migrations = [*bundled_migrations(), (2, "ALTER TABLE expenses ADD COLUMN tag TEXT;")]
    assert migrate(settings.db_path, settings.local_backup_dir, migrations) == 2

    assert "tag" in expense_columns(settings.db_path)
    assert seeded.list_expenses()["total"] == 14
    copies = list(settings.local_backup_dir.iterdir())
    assert len(copies) == 1
    conn = sqlite3.connect(copies[0])
    try:
        assert conn.execute("SELECT count(*) FROM expenses").fetchone()[0] == 14
        assert "tag" not in {row[1] for row in conn.execute("PRAGMA table_info(expenses)")}
    finally:
        conn.close()


def test_failed_migration_rolls_back_completely(seeded: Storage) -> None:
    settings = seeded.settings
    broken = "ALTER TABLE expenses ADD COLUMN tag TEXT; THIS IS NOT SQL;"
    with pytest.raises(sqlite3.Error):
        migrate(settings.db_path, settings.local_backup_dir, [*bundled_migrations(), (2, broken)])

    assert "tag" not in expense_columns(settings.db_path)
    assert seeded.list_expenses()["total"] == 14
    assert migrate(settings.db_path, settings.local_backup_dir) == 1


def test_database_newer_than_the_code_is_refused(seeded: Storage) -> None:
    settings = seeded.settings
    migrate(settings.db_path, settings.local_backup_dir, [*bundled_migrations(), (2, "SELECT 1;")])
    with pytest.raises(ExpenseError, match="Update the code"):
        migrate(settings.db_path, settings.local_backup_dir)

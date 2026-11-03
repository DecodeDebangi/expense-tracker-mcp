import sqlite3
from pathlib import Path

import pytest

from expense_tracker.config import Settings
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
    migrations = [*bundled_migrations(), (99, "ALTER TABLE expenses ADD COLUMN tag TEXT;")]
    assert migrate(settings.db_path, settings.local_backup_dir, migrations) == 99

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
        migrate(settings.db_path, settings.local_backup_dir, [*bundled_migrations(), (99, broken)])

    assert "tag" not in expense_columns(settings.db_path)
    assert seeded.list_expenses()["total"] == 14
    assert migrate(settings.db_path, settings.local_backup_dir) == bundled_migrations()[-1][0]


def test_database_newer_than_the_code_is_refused(seeded: Storage) -> None:
    settings = seeded.settings
    migrate(settings.db_path, settings.local_backup_dir, [*bundled_migrations(), (99, "SELECT 1;")])
    with pytest.raises(ExpenseError, match="Update the code"):
        migrate(settings.db_path, settings.local_backup_dir)


def test_upgrading_from_the_first_schema_keeps_expenses_and_adds_subcategories(tmp_path: Path) -> None:
    db = tmp_path / "expenses.db"
    first_only = [m for m in bundled_migrations() if m[0] == 1]
    migrate(db, tmp_path / "backups", first_only)
    conn = connect(db)
    food = conn.execute("SELECT id FROM categories WHERE name = 'Food'").fetchone()[0]
    conn.execute(
        """INSERT INTO expenses (id, amount_minor, currency, occurred_on, merchant, category_id,
            created_at, updated_at) VALUES ('a', 45050, 'INR', '2026-05-01', 'Demo Cafe', ?, 'now', 'now')""",
        (food,),
    )
    conn.close()

    storage = Storage(Settings(db_path=db, backup_dir=tmp_path / "backups"))

    expense = storage.list_expenses()["expenses"][0]
    assert (expense["amount_minor"], expense["category"], expense["subcategory"]) == (45050, "Food", None)
    assert len(list((tmp_path / "backups").iterdir())) == 1
    categories = {c["name"]: c for c in storage.list_categories()}
    assert len(categories) == 14
    assert all(c["subcategories"] for c in categories.values())
    loans = {s["name"] for s in categories["Loans & EMIs"]["subcategories"]}
    assert {"Home Loan", "Two-wheeler Loan"} <= loans
    assert "Monthly Support" in {s["name"] for s in categories["Family Support"]["subcategories"]}

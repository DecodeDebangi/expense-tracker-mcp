import csv
from pathlib import Path

import pytest

from expense_tracker.config import Settings
from expense_tracker.storage import Storage
from expense_tracker.validation import format_amount

SEED_FILE = Path(__file__).parent / "fixtures" / "seed_expenses.csv"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(db_path=tmp_path / "data" / "expenses.db", backup_dir=tmp_path / "drive")


@pytest.fixture
def storage(settings: Settings) -> Storage:
    return Storage(settings)


@pytest.fixture
def seeded(storage: Storage) -> Storage:
    with open(SEED_FILE, newline="") as file:
        for row in csv.DictReader(file):
            storage.add_expense(
                format_amount(int(row["amount_minor"]), row["currency"]),
                row["merchant"],
                row["category"],
                row["occurred_on"],
                row["currency"],
                row["note"],
            )
    return storage

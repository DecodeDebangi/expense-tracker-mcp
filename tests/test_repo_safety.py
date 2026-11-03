"""The checks that keep real financial data out of this public repo."""

import csv
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
CHECKER = ROOT / "scripts" / "check_tracked_files.py"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def run_checker(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER)], cwd=repo, capture_output=True, text=True
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init")
    shutil.copy(ROOT / ".gitignore", tmp_path / ".gitignore")
    return tmp_path


def plant(repo: Path, path: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("private")


PRIVATE_PATHS = [
    "expenses.db",
    "expenses.db-wal",
    "data/expenses.sqlite3",
    "exports/october.csv",
    "backups/daily.bak",
    "statements/hdfc.pdf",
    ".env",
    ".env.local",
]


@pytest.mark.parametrize("path", PRIVATE_PATHS)
def test_private_files_are_ignored_by_git(repo: Path, path: str) -> None:
    plant(repo, path)
    git(repo, "add", ".")
    assert path not in git(repo, "ls-files").stdout.splitlines()


@pytest.mark.parametrize("path", [*PRIVATE_PATHS, "october.csv", "docs/bank.xlsx"])
def test_checker_catches_force_added_files(repo: Path, path: str) -> None:
    plant(repo, path)
    assert git(repo, "add", "-f", path).returncode == 0
    result = run_checker(repo)
    assert result.returncode == 1
    assert path in result.stderr


def test_checker_allows_code_and_fake_fixtures(repo: Path) -> None:
    plant(repo, "src/app.py")
    plant(repo, "tests/fixtures/seed_expenses.csv")
    git(repo, "add", "-f", "src/app.py", "tests/fixtures/seed_expenses.csv")
    assert run_checker(repo).returncode == 0


def test_seed_data_is_well_formed() -> None:
    with open(ROOT / "tests" / "fixtures" / "seed_expenses.csv", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows
    assert all(int(row["amount_minor"]) > 0 for row in rows)

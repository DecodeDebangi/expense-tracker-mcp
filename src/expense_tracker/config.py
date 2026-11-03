"""Where things live. The real database stays outside the repo; backups go in its ignored backups/ folder."""

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / "Library" / "Application Support" / "expense-tracker" / "expenses.db"
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    db_path: Path
    backup_dir: Path | None = None
    default_currency: str = "INR"

    @property
    def data_dir(self) -> Path:
        return self.db_path.parent

    @property
    def local_backup_dir(self) -> Path:
        return self.backup_dir or self.data_dir / "backups"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def backup_status_path(self) -> Path:
        return self.data_dir / "backup-status.json"


def _default_backup_dir() -> Path | None:
    if (REPO_ROOT / "pyproject.toml").is_file():
        return REPO_ROOT / "backups"
    return None


def load_settings() -> Settings:
    backup_dir = os.environ.get("EXPENSE_BACKUP_DIR")
    return Settings(
        db_path=Path(os.environ.get("EXPENSE_DB_PATH", DEFAULT_DB_PATH)).expanduser(),
        backup_dir=Path(backup_dir).expanduser() if backup_dir else _default_backup_dir(),
        default_currency=os.environ.get("EXPENSE_DEFAULT_CURRENCY", "INR"),
    )

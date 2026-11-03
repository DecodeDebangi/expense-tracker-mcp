"""Where things live. The real database and backups always stay outside the repo."""

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / "Library" / "Application Support" / "expense-tracker" / "expenses.db"


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
        return self.data_dir / "backups"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def backup_status_path(self) -> Path:
        return self.data_dir / "backup-status.json"


def load_settings() -> Settings:
    backup_dir = os.environ.get("EXPENSE_BACKUP_DIR")
    return Settings(
        db_path=Path(os.environ.get("EXPENSE_DB_PATH", DEFAULT_DB_PATH)).expanduser(),
        backup_dir=Path(backup_dir).expanduser() if backup_dir else None,
        default_currency=os.environ.get("EXPENSE_DEFAULT_CURRENCY", "INR"),
    )

"""Maintenance commands that run without Claude: backups, restoring and the daily schedule."""

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from expense_tracker import backup
from expense_tracker.config import Settings, load_settings
from expense_tracker.db import migrate
from expense_tracker.errors import ExpenseError

LABEL = "local.expense-tracker.backup"
BACKUP_HOUR = 21


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(settings: Settings, backup_dir: Path, python: str) -> dict:
    """Runs at 9pm and at login, so that a day the Mac was off still gets a backup."""
    log = str(settings.data_dir / "backup.log")
    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "expense_tracker.admin", "backup"],
        "EnvironmentVariables": {
            "EXPENSE_DB_PATH": str(settings.db_path),
            "EXPENSE_BACKUP_DIR": str(backup_dir),
        },
        "StartCalendarInterval": {"Hour": BACKUP_HOUR, "Minute": 0},
        "RunAtLoad": True,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def _launchctl(*args: str, check: bool = True) -> None:
    subprocess.run(["launchctl", *args], check=check, capture_output=True)


def install_schedule(settings: Settings, backup_dir: Path) -> None:
    if not backup_dir.parent.is_dir():
        raise ExpenseError(f"{backup_dir.parent} doesn't exist. Is the cloud drive running?")
    settings.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{LABEL}", check=False)
    with open(path, "wb") as file:
        plistlib.dump(build_plist(settings, backup_dir, sys.executable), file)
    _launchctl("bootstrap", domain, str(path))


def uninstall_schedule() -> None:
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}", check=False)
    plist_path().unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="expense-tracker-admin", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="create the database if it doesn't exist")
    run = commands.add_parser("backup", help="back up now, unless one ran recently")
    run.add_argument("--force", action="store_true", help="back up even if one ran recently")
    commands.add_parser("status", help="show whether backups are working")
    restore = commands.add_parser("restore", help="restore a database from a backup file")
    restore.add_argument("file", type=Path)
    restore.add_argument("--to", type=Path, help="where to restore; defaults to the live database")
    restore.add_argument("--force", action="store_true", help="replace an existing database")
    schedule = commands.add_parser("install-schedule", help="run the backup daily")
    schedule.add_argument("--backup-dir", type=Path, required=True)
    commands.add_parser("uninstall-schedule", help="stop the daily backup")
    args = parser.parse_args(argv)

    settings = load_settings()
    try:
        if args.command == "init":
            migrate(settings.db_path, settings.local_backup_dir)
            print(f"Database ready at {settings.db_path}.")
        elif args.command == "backup":
            outcome = backup.run_backup(settings, force=args.force)
            print({"backed_up": f"Backed up to {outcome.path}", "skipped": "Backed up recently; skipped.",
                   "no_database": "No database yet; nothing to back up."}[outcome.status])
        elif args.command == "status":
            for key, value in backup.backup_health(settings).items():
                print(f"{key}: {value}")
        elif args.command == "restore":
            safety = backup.restore_backup(
                args.file, args.to or settings.db_path, settings.local_backup_dir, args.force
            )
            print(f"Restored to {args.to or settings.db_path}.")
            if safety:
                print(f"The replaced database was saved to {safety}.")
        elif args.command == "install-schedule":
            install_schedule(settings, args.backup_dir.expanduser())
            print(f"Daily backup installed, to {args.backup_dir}.")
        elif args.command == "uninstall-schedule":
            uninstall_schedule()
            print("Daily backup removed.")
    except ExpenseError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

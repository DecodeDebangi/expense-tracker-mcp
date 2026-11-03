"""Verified database copies, their retention, and restoring from them."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from expense_tracker.config import Settings
from expense_tracker.errors import ExpenseError

BACKUP_NAME = re.compile(r"^expenses-(\d{4})(\d{2})\d{2}-\d{6}\.db$")
KEEP_RECENT = 30
KEEP_MONTHLY = 24
SKIP_IF_BACKED_UP_WITHIN = timedelta(hours=20)
STALE_AFTER = timedelta(hours=48)
DRIVE_REMINDER_AFTER = timedelta(days=7)
RCLONE_TIMEOUT_SECONDS = 600
RCLONE_FALLBACK = "/opt/homebrew/bin/rclone"


class BackupError(ExpenseError):
    pass


def _copy_database(source: Path, target: Path) -> None:
    """Copy through SQLite, which stays consistent even if the database is in use."""
    source_conn = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    target_conn = sqlite3.connect(target)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()


def _make_standalone(path: Path) -> None:
    """Copies inherit WAL mode, which would make SQLite create extra files beside them."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode = DELETE")
    finally:
        conn.close()


def verify_backup(path: Path) -> int:
    """Check a backup is a sound expenses database, and return its expense count."""
    if not path.is_file():
        raise BackupError(f"No backup file at {path}.")
    # Immutable opens leave no -wal or -shm files beside it, which matters in a synced folder.
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?immutable=1", uri=True)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError(f"{path} is damaged.")
        return conn.execute("SELECT count(*) FROM expenses").fetchone()[0]
    except sqlite3.DatabaseError as error:
        raise BackupError(f"{path} isn't a usable expenses backup: {error}") from error
    finally:
        conn.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(db_path: Path, dest_dir: Path, now: datetime | None = None) -> Path:
    """Write a verified copy into dest_dir.

    The copy is built and checked locally, because a synced folder may not support
    SQLite's file locking. Only dest_dir itself is created: a missing parent usually
    means a cloud drive isn't mounted, and that should fail rather than write elsewhere.
    """
    if not db_path.is_file():
        raise BackupError(f"No database at {db_path}.")
    try:
        dest_dir.mkdir(mode=0o700, exist_ok=True)
    except OSError as error:
        raise BackupError(f"Can't use the backup folder {dest_dir}: {error}") from error

    moment = now or datetime.now()
    final = dest_dir / f"expenses-{moment:%Y%m%d-%H%M%S}.db"
    with tempfile.TemporaryDirectory(dir=db_path.parent) as work_dir:
        local_copy = Path(work_dir) / "copy.db"
        _copy_database(db_path, local_copy)
        _make_standalone(local_copy)
        verify_backup(local_copy)
        expected_hash = _sha256(local_copy)

        partial = dest_dir / f".{final.name}.partial"
        try:
            shutil.copyfile(local_copy, partial)
            with open(partial, "rb") as file:
                os.fsync(file.fileno())
            if _sha256(partial) != expected_hash:
                raise BackupError(f"The copy written to {dest_dir} didn't match the original.")
            os.chmod(partial, 0o600)
            os.replace(partial, final)
        except OSError as error:
            raise BackupError(f"Couldn't write to {dest_dir}: {error}") from error
        finally:
            partial.unlink(missing_ok=True)
    return final


def select_prunable(
    names: Iterable[str], keep_recent: int = KEEP_RECENT, keep_monthly: int = KEEP_MONTHLY
) -> list[str]:
    """Keep the newest backups, plus the newest from each recent month.

    Anything not named like one of our backups is never selected.
    """
    newest_first = sorted((n for n in names if BACKUP_NAME.match(n)), reverse=True)
    keep = set(newest_first[:keep_recent])
    months: set[str] = set()
    for name in newest_first:
        match = BACKUP_NAME.match(name)
        month = f"{match[1]}-{match[2]}"
        if month not in months and len(months) < keep_monthly:
            months.add(month)
            keep.add(name)
    return [name for name in newest_first if name not in keep]


def prune_backups(dest_dir: Path) -> list[str]:
    removed = select_prunable(path.name for path in dest_dir.iterdir())
    for name in removed:
        (dest_dir / name).unlink()
    return removed


def restore_backup(backup: Path, target: Path, safety_dir: Path, force: bool = False) -> Path | None:
    """Restore a backup to target, first saving a copy of anything it would replace."""
    verify_backup(backup)
    if target.exists() and backup.resolve() == target.resolve():
        raise BackupError("The backup and the restore target are the same file.")
    safety_copy = None
    if target.exists():
        if not force:
            raise BackupError(f"{target} already exists. Use force to replace it.")
        safety_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        safety_copy = create_backup(target, safety_dir)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _copy_database(backup, target)
    os.chmod(target, 0o600)
    verify_backup(target)
    return safety_copy


def _read_status(settings: Settings) -> dict:
    try:
        return json.loads(settings.backup_status_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_status(settings: Settings, status: dict) -> None:
    partial = settings.backup_status_path.with_suffix(".partial")
    partial.write_text(json.dumps(status, indent=2))
    os.replace(partial, settings.backup_status_path)


def _notify_failure() -> None:
    script = 'display notification "The daily backup failed." with title "Expense Tracker"'
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


@dataclass(frozen=True)
class BackupOutcome:
    status: str
    path: Path | None = None


def run_backup(settings: Settings, force: bool = False, now: datetime | None = None) -> BackupOutcome:
    """Back up once, recording the result so that failures can't go unnoticed."""
    moment = now or datetime.now(UTC)
    if not settings.db_path.exists():
        return BackupOutcome("no_database")

    status = _read_status(settings)
    last_success = status.get("last_success")
    if last_success and not force:
        recent = moment - datetime.fromisoformat(last_success) < SKIP_IF_BACKED_UP_WITHIN
        if recent and Path(status.get("last_path", "")).is_file():
            return BackupOutcome("skipped")

    status["last_attempt"] = moment.isoformat()
    try:
        path = create_backup(settings.db_path, settings.daily_backup_dir, moment.astimezone())
        prune_backups(settings.daily_backup_dir)
    except (ExpenseError, sqlite3.Error, OSError) as error:
        status["last_error"] = str(error)
        _write_status(settings, status)
        _notify_failure()
        raise
    status.update(last_success=moment.isoformat(), last_path=str(path), last_error=None)
    _write_status(settings, status)
    return BackupOutcome("backed_up", path)


def _rclone(*args: str) -> str:
    binary = shutil.which("rclone") or RCLONE_FALLBACK
    try:
        result = subprocess.run(
            [binary, *args], capture_output=True, text=True, timeout=RCLONE_TIMEOUT_SECONDS
        )
    except FileNotFoundError:
        raise BackupError("rclone isn't installed.") from None
    except subprocess.TimeoutExpired:
        raise BackupError("rclone timed out.") from None
    if result.returncode != 0:
        raise BackupError(f"rclone failed: {result.stderr.strip()[-500:]}")
    return result.stdout


def _upload_verified(local: Path, remote: str) -> None:
    """Upload a backup, then compare Drive's checksum of it with the local file's."""
    target = f"{remote}/{local.name}"
    _rclone("copyto", str(local), target)
    uploaded = _rclone("md5sum", target).split()
    expected = hashlib.md5(local.read_bytes(), usedforsecurity=False).hexdigest()
    if not uploaded or uploaded[0] != expected:
        raise BackupError(f"The uploaded copy of {local.name} didn't match the local file.")


def _prune_drive(remote: str) -> list[str]:
    removed = select_prunable(_rclone("lsf", "--files-only", remote).splitlines())
    for name in removed:
        _rclone("deletefile", f"{remote}/{name}")
    return removed


def run_drive_backup(settings: Settings, now: datetime | None = None) -> dict:
    """Make a fresh backup and upload it to Google Drive. Only runs when asked to."""
    moment = now or datetime.now(UTC)
    status = _read_status(settings)
    status["drive_last_attempt"] = moment.isoformat()
    try:
        path = create_backup(settings.db_path, settings.daily_backup_dir, moment.astimezone())
        prune_backups(settings.daily_backup_dir)
        _upload_verified(path, settings.drive_remote)
    except (ExpenseError, sqlite3.Error, OSError) as error:
        status["drive_last_error"] = str(error)
        _write_status(settings, status)
        raise
    status.update(
        last_success=moment.isoformat(),
        last_path=str(path),
        last_error=None,
        drive_last_success=moment.isoformat(),
        drive_last_file=path.name,
        drive_last_error=None,
    )
    _write_status(settings, status)
    # The upload is already safe, so a failed clean-up must not report the backup as failed.
    try:
        removed = _prune_drive(settings.drive_remote)
        warning = None
    except BackupError as error:
        removed, warning = [], f"Old Drive backups weren't cleaned up: {error}"
    return {
        "file": path.name,
        "expenses": verify_backup(path),
        "removed_from_drive": removed,
        "warning": warning,
    }


def drive_backup_reminder(settings: Settings, now: datetime | None = None) -> str | None:
    last = _read_status(settings).get("drive_last_success")
    if not last:
        return "Nothing has been backed up to Google Drive yet. Suggest the user runs /backup-expenses."
    age = (now or datetime.now(UTC)) - datetime.fromisoformat(last)
    if age <= DRIVE_REMINDER_AFTER:
        return None
    return f"The last Google Drive backup was {age.days} days ago. Suggest the user runs /backup-expenses."


def backup_health(settings: Settings, now: datetime | None = None) -> dict:
    moment = now or datetime.now(UTC)
    status = _read_status(settings)
    last_success = status.get("last_success")
    healthy = bool(
        last_success
        and not status.get("last_error")
        and moment - datetime.fromisoformat(last_success) < STALE_AFTER
        and Path(status.get("last_path", "")).is_file()
    )
    return {
        "healthy": healthy,
        "last_success": last_success,
        "last_attempt": status.get("last_attempt"),
        "last_error": status.get("last_error"),
        "backup_folder": str(settings.daily_backup_dir),
        "drive_last_success": status.get("drive_last_success"),
        "drive_last_file": status.get("drive_last_file"),
        "drive_last_error": status.get("drive_last_error"),
        "drive_reminder": drive_backup_reminder(settings, moment),
    }

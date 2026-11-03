from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from expense_tracker import admin, backup
from expense_tracker.backup import (
    BackupError,
    backup_health,
    create_backup,
    restore_backup,
    run_backup,
    select_prunable,
    verify_backup,
)
from expense_tracker.config import Settings
from expense_tracker.storage import Storage

NOW = datetime(2026, 11, 3, 21, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup, "_notify_failure", lambda: None)


def test_backup_is_a_verified_private_copy(seeded: Storage, tmp_path: Path) -> None:
    path = create_backup(seeded.settings.db_path, tmp_path / "drive", NOW)
    assert verify_backup(path) == 14
    assert oct(path.stat().st_mode)[-3:] == "600"
    assert [p.name for p in (tmp_path / "drive").iterdir()] == [path.name]


def test_backup_includes_data_still_in_the_write_ahead_log(storage: Storage, tmp_path: Path) -> None:
    storage.add_expense("10", "Demo Cafe", "Food", "2026-05-01")
    assert verify_backup(create_backup(storage.settings.db_path, tmp_path / "drive")) == 1


def test_backup_fails_when_the_cloud_drive_is_not_mounted(seeded: Storage, tmp_path: Path) -> None:
    missing = tmp_path / "GoogleDrive-unmounted" / "My Drive" / "backups"
    with pytest.raises(BackupError):
        create_backup(seeded.settings.db_path, missing)
    assert not missing.parent.exists()


def test_restore_returns_every_expense_into_a_working_database(seeded: Storage, tmp_path: Path) -> None:
    path = create_backup(seeded.settings.db_path, tmp_path / "drive", NOW)
    target = tmp_path / "restored" / "expenses.db"
    assert restore_backup(path, target, tmp_path / "safety") is None

    restored = Storage(Settings(db_path=target))
    assert restored.summarize() == seeded.summarize()
    assert restored.add_expense("5", "Demo Cafe", "Food", "2026-06-01")["saved"] is True


def test_restore_will_not_replace_a_database_unless_forced(seeded: Storage, tmp_path: Path) -> None:
    settings = seeded.settings
    path = create_backup(settings.db_path, tmp_path / "drive", NOW)
    seeded.add_expense("5", "After Backup", "Food", "2026-06-01")

    with pytest.raises(BackupError):
        restore_backup(path, settings.db_path, tmp_path / "safety")
    assert seeded.list_expenses()["total"] == 15

    safety = restore_backup(path, settings.db_path, tmp_path / "safety", force=True)
    assert seeded.list_expenses()["total"] == 14
    assert verify_backup(safety) == 15


@pytest.mark.parametrize("content", [b"", b"not a database at all, just text" * 100])
def test_damaged_backups_are_rejected(tmp_path: Path, content: bytes) -> None:
    bad = tmp_path / "bad.db"
    bad.write_bytes(content)
    with pytest.raises(BackupError):
        verify_backup(bad)
    with pytest.raises(BackupError):
        restore_backup(bad, tmp_path / "target.db", tmp_path / "safety")
    assert not (tmp_path / "target.db").exists()


def backup_names(start: datetime, days: int) -> list[str]:
    return [f"expenses-{start - timedelta(days=d):%Y%m%d}-210000.db" for d in range(days)]


def test_retention_keeps_recent_and_monthly_backups() -> None:
    names = backup_names(datetime(2026, 11, 3), 800)
    removed = set(select_prunable(names))
    kept = [n for n in names if n not in removed]

    assert set(names[:30]) <= set(kept)
    months = {n[9:15] for n in kept}
    assert len(months) == 24
    assert "expenses-20260831-210000.db" in kept
    assert "expenses-20260830-210000.db" in removed
    assert len(kept) < 60


def test_retention_never_touches_other_files() -> None:
    assert select_prunable(["notes.db", "my-photo.jpg", "expenses-final.db"], keep_recent=0) == []


def test_run_backup_records_success_then_skips_until_due(seeded: Storage) -> None:
    settings = seeded.settings
    assert run_backup(settings, now=NOW).status == "backed_up"
    assert backup_health(settings, NOW)["healthy"] is True

    assert run_backup(settings, now=NOW + timedelta(hours=1)).status == "skipped"
    assert run_backup(settings, force=True, now=NOW + timedelta(hours=1)).status == "backed_up"
    assert run_backup(settings, now=NOW + timedelta(hours=22)).status == "backed_up"


def test_skip_is_ignored_if_the_last_backup_has_gone(seeded: Storage) -> None:
    settings = seeded.settings
    path = run_backup(settings, now=NOW).path
    path.unlink()
    assert run_backup(settings, now=NOW + timedelta(hours=1)).status == "backed_up"


def test_failure_is_recorded_and_reported(seeded: Storage, tmp_path: Path, monkeypatch) -> None:
    notified = []
    monkeypatch.setattr(backup, "_notify_failure", lambda: notified.append(True))
    settings = replace(seeded.settings, backup_dir=tmp_path / "unmounted" / "drive")

    with pytest.raises(BackupError):
        run_backup(settings, now=NOW)
    health = backup_health(settings, NOW)
    assert health["healthy"] is False
    assert health["last_error"]
    assert notified == [True]


def test_failure_never_prunes_existing_backups(seeded: Storage, monkeypatch) -> None:
    settings = seeded.settings
    existing = run_backup(settings, now=NOW).path
    monkeypatch.setattr(backup, "create_backup", lambda *a, **k: (_ for _ in ()).throw(BackupError("no")))
    pruned = []
    monkeypatch.setattr(backup, "prune_backups", lambda d: pruned.append(d))
    with pytest.raises(BackupError):
        run_backup(settings, force=True, now=NOW + timedelta(days=1))
    assert existing.exists()
    assert pruned == []


def test_backups_go_stale(seeded: Storage) -> None:
    run_backup(seeded.settings, now=NOW)
    assert backup_health(seeded.settings, NOW + timedelta(hours=47))["healthy"] is True
    assert backup_health(seeded.settings, NOW + timedelta(hours=49))["healthy"] is False


def test_daily_backup_defaults_to_a_local_folder(seeded: Storage) -> None:
    settings = replace(seeded.settings, backup_dir=None)
    path = run_backup(settings, now=NOW).path
    assert path.parent == settings.data_dir / "backups"
    assert verify_backup(path) == 14


def test_missing_database_is_not_a_failure(settings: Settings) -> None:
    assert run_backup(settings).status == "no_database"


def test_schedule_runs_daily_and_at_login_with_the_right_paths(settings: Settings) -> None:
    plist = admin.build_plist(replace(settings, backup_dir=None), "/venv/python")
    assert plist["ProgramArguments"] == ["/venv/python", "-m", "expense_tracker.admin", "backup"]
    assert plist["EnvironmentVariables"] == {"EXPENSE_DB_PATH": str(settings.db_path)}
    assert plist["RunAtLoad"] is True
    assert plist["StartCalendarInterval"] == {"Hour": 21, "Minute": 0}


def test_admin_restore_command_end_to_end(seeded: Storage, tmp_path: Path, monkeypatch) -> None:
    settings = seeded.settings
    monkeypatch.setenv("EXPENSE_DB_PATH", str(settings.db_path))
    monkeypatch.setenv("EXPENSE_BACKUP_DIR", str(settings.backup_dir))
    assert admin.main(["backup"]) == 0
    copy = next(settings.backup_dir.iterdir())

    assert admin.main(["restore", str(copy)]) == 1
    target = tmp_path / "elsewhere.db"
    assert admin.main(["restore", str(copy), "--to", str(target)]) == 0
    assert verify_backup(target) == 14

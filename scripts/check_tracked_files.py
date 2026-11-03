"""Fail if git is tracking anything that could hold real financial data."""

import fnmatch
import subprocess
import sys

BLOCKED_FILE_PATTERNS = (
    "*.db",
    "*.db-*",
    "*.sqlite",
    "*.sqlite3",
    "*.sqlite-*",
    "*.sqlite3-*",
    ".env",
    ".env.*",
)
BLOCKED_DIRECTORIES = {"exports", "backups", "statements"}
# Spreadsheets are only allowed as fake test data.
SPREADSHEET_PATTERNS = ("*.csv", "*.tsv", "*.xls", "*.xlsx")
SPREADSHEETS_ALLOWED_IN = "tests/fixtures/"


def is_blocked(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1].lower()
    if BLOCKED_DIRECTORIES.intersection(parts[:-1]):
        return True
    if any(fnmatch.fnmatch(name, pattern) for pattern in BLOCKED_FILE_PATTERNS):
        return True
    is_spreadsheet = any(fnmatch.fnmatch(name, p) for p in SPREADSHEET_PATTERNS)
    return is_spreadsheet and not path.startswith(SPREADSHEETS_ALLOWED_IN)


def main() -> int:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, text=True, check=True
    ).stdout.split("\0")
    blocked = [path for path in tracked if path and is_blocked(path)]
    if blocked:
        print("These tracked files could hold real financial data:", file=sys.stderr)
        for path in blocked:
            print(f"  {path}", file=sys.stderr)
        return 1
    print("No private data files are tracked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""All SQL lives here, so tools never depend on where the data is stored."""

import csv
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from expense_tracker import validation
from expense_tracker.config import Settings
from expense_tracker.db import connect, migrate, transaction
from expense_tracker.errors import ExpenseError

MAX_PAGE_SIZE = 500
EXPENSE_COLUMNS = """
    e.id, e.amount_minor, e.currency, e.occurred_on, e.merchant, c.name AS category, e.note,
    e.source, e.created_at, e.updated_at, e.deleted_at
"""
EXPENSE_FROM = "FROM expenses e JOIN categories c ON c.id = e.category_id"
GROUP_EXPRESSIONS = {
    "category": "c.name",
    "merchant": "e.merchant COLLATE NOCASE",
    "month": "substr(e.occurred_on, 1, 7)",
}
CSV_FIELDS = (
    "id", "occurred_on", "amount", "amount_minor", "currency", "merchant", "category",
    "note", "source", "created_at", "updated_at", "deleted_at",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _expense(row: sqlite3.Row) -> dict:
    expense = dict(row)
    expense["amount"] = validation.format_amount(row["amount_minor"], row["currency"])
    return expense


def _money(minor: int, currency: str) -> dict:
    return {
        "currency": currency,
        "total": validation.format_amount(minor, currency),
        "total_minor": minor,
    }


class Storage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        migrate(settings.db_path, settings.local_backup_dir)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.settings.db_path)
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _active_category_id(conn: sqlite3.Connection, name: str) -> int:
        row = conn.execute(
            "SELECT id, archived_at FROM categories WHERE name = ?",
            (validation.clean_category_name(name),),
        ).fetchone()
        if row is None or row["archived_at"]:
            problem = "is archived" if row else "doesn't exist"
            names = [
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM categories WHERE archived_at IS NULL ORDER BY name"
                )
            ]
            raise ExpenseError(f"The category '{name}' {problem}. Available: {', '.join(names)}.")
        return row["id"]

    @staticmethod
    def _get_expense(conn: sqlite3.Connection, expense_id: str) -> sqlite3.Row:
        row = conn.execute(
            f"SELECT {EXPENSE_COLUMNS} {EXPENSE_FROM} WHERE e.id = ?", (expense_id,)
        ).fetchone()
        if row is None:
            raise ExpenseError(f"No expense with id '{expense_id}'.")
        return row

    def add_expense(
        self,
        amount: str | int | float,
        merchant: str,
        category: str,
        occurred_on: str | None = None,
        currency: str | None = None,
        note: str | None = None,
        allow_duplicate: bool = False,
        client_request_id: str | None = None,
    ) -> dict:
        code = validation.parse_currency(currency or self.settings.default_currency)
        amount_minor = validation.parse_amount(amount, code)
        day = validation.parse_expense_date(occurred_on)
        merchant = validation.clean_merchant(merchant)
        note = validation.clean_note(note)

        with self._connection() as conn, transaction(conn):
            if client_request_id:
                existing = conn.execute(
                    f"SELECT {EXPENSE_COLUMNS} {EXPENSE_FROM} WHERE e.client_request_id = ?",
                    (client_request_id,),
                ).fetchone()
                if existing:
                    return {"status": "already_saved", "saved": False, "expense": _expense(existing)}

            category_id = self._active_category_id(conn, category)
            if not allow_duplicate:
                duplicates = conn.execute(
                    f"""SELECT {EXPENSE_COLUMNS} {EXPENSE_FROM}
                    WHERE e.deleted_at IS NULL AND e.amount_minor = ? AND e.currency = ?
                      AND e.occurred_on = ? AND e.merchant = ? COLLATE NOCASE""",
                    (amount_minor, code, day, merchant),
                ).fetchall()
                if duplicates:
                    return {
                        "status": "possible_duplicate",
                        "saved": False,
                        "message": "Not saved. A matching expense exists; confirm with the user, "
                        "then retry with allow_duplicate=true if this is a separate expense.",
                        "matches": [_expense(row) for row in duplicates],
                    }

            expense_id = str(uuid.uuid4())
            now = _now()
            conn.execute(
                """INSERT INTO expenses (id, amount_minor, currency, occurred_on, merchant,
                    category_id, note, client_request_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (expense_id, amount_minor, code, day, merchant, category_id, note,
                 client_request_id, now, now),
            )
            return {
                "status": "saved",
                "saved": True,
                "expense": _expense(self._get_expense(conn, expense_id)),
            }

    def _filters(
        self,
        start_date: str | None,
        end_date: str | None,
        category: str | None = None,
        merchant: str | None = None,
        currency: str | None = None,
        include_deleted: bool = False,
    ) -> tuple[str, list]:
        start, end = validation.parse_date_range(start_date, end_date)
        clauses, params = ["1 = 1"], []
        if not include_deleted:
            clauses.append("e.deleted_at IS NULL")
        if start:
            clauses.append("e.occurred_on >= ?")
            params.append(start)
        if end:
            clauses.append("e.occurred_on <= ?")
            params.append(end)
        if category:
            clauses.append("c.name = ?")
            params.append(validation.clean_category_name(category))
        if merchant:
            escaped = merchant.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("e.merchant LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")
        if currency:
            clauses.append("e.currency = ?")
            params.append(validation.parse_currency(currency))
        return " AND ".join(clauses), params

    def list_expenses(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        category: str | None = None,
        merchant: str | None = None,
        currency: str | None = None,
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ExpenseError(f"The limit must be between 1 and {MAX_PAGE_SIZE}.")
        if offset < 0:
            raise ExpenseError("The offset can't be negative.")
        where, params = self._filters(
            start_date, end_date, category, merchant, currency, include_deleted
        )
        with self._connection() as conn:
            total = conn.execute(
                f"SELECT count(*) {EXPENSE_FROM} WHERE {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"""SELECT {EXPENSE_COLUMNS} {EXPENSE_FROM} WHERE {where}
                ORDER BY e.occurred_on DESC, e.created_at DESC, e.id LIMIT ? OFFSET ?""",
                [*params, limit, offset],
            ).fetchall()
        return {"total": total, "limit": limit, "offset": offset, "expenses": [_expense(r) for r in rows]}

    def update_expense(
        self,
        expense_id: str,
        amount: str | int | float | None = None,
        currency: str | None = None,
        occurred_on: str | None = None,
        merchant: str | None = None,
        category: str | None = None,
        note: str | None = None,
    ) -> dict:
        changes: dict[str, object] = {}
        with self._connection() as conn, transaction(conn):
            current = self._get_expense(conn, expense_id)
            if current["deleted_at"]:
                raise ExpenseError("That expense is deleted. Restore it before changing it.")

            code = validation.parse_currency(currency) if currency else current["currency"]
            if code != current["currency"] and amount is None:
                # The stored number means something different in another currency.
                raise ExpenseError("Give the amount as well when changing the currency.")
            if amount is not None:
                changes["amount_minor"] = validation.parse_amount(amount, code)
            if currency:
                changes["currency"] = code
            if occurred_on is not None:
                changes["occurred_on"] = validation.parse_expense_date(occurred_on)
            if merchant is not None:
                changes["merchant"] = validation.clean_merchant(merchant)
            if category is not None:
                changes["category_id"] = self._active_category_id(conn, category)
            if note is not None:
                changes["note"] = validation.clean_note(note)
            if not changes:
                raise ExpenseError("Nothing to change.")

            assignments = ", ".join(f"{column} = ?" for column in changes)
            conn.execute(
                f"UPDATE expenses SET {assignments}, updated_at = ? WHERE id = ?",
                [*changes.values(), _now(), expense_id],
            )
            return _expense(self._get_expense(conn, expense_id))

    def delete_expense(self, expense_id: str) -> dict:
        with self._connection() as conn, transaction(conn):
            if self._get_expense(conn, expense_id)["deleted_at"]:
                raise ExpenseError("That expense is already deleted.")
            now = _now()
            conn.execute(
                "UPDATE expenses SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (now, now, expense_id),
            )
            return _expense(self._get_expense(conn, expense_id))

    def restore_expense(self, expense_id: str) -> dict:
        with self._connection() as conn, transaction(conn):
            if not self._get_expense(conn, expense_id)["deleted_at"]:
                raise ExpenseError("That expense isn't deleted.")
            conn.execute(
                "UPDATE expenses SET deleted_at = NULL, updated_at = ? WHERE id = ?",
                (_now(), expense_id),
            )
            return _expense(self._get_expense(conn, expense_id))

    def list_categories(self, include_archived: bool = False) -> list[dict]:
        where = "" if include_archived else "WHERE c.archived_at IS NULL"
        with self._connection() as conn:
            rows = conn.execute(
                f"""SELECT c.name, c.archived_at IS NOT NULL AS archived,
                    count(e.id) AS expense_count
                FROM categories c
                LEFT JOIN expenses e ON e.category_id = c.id AND e.deleted_at IS NULL
                {where} GROUP BY c.id ORDER BY c.name"""
            ).fetchall()
        return [
            {"name": r["name"], "archived": bool(r["archived"]), "expense_count": r["expense_count"]}
            for r in rows
        ]

    def create_category(self, name: str) -> dict:
        name = validation.clean_category_name(name)
        with self._connection() as conn, transaction(conn):
            existing = conn.execute(
                "SELECT name, archived_at FROM categories WHERE name = ?", (name,)
            ).fetchone()
            if existing:
                state = "exists but is archived; unarchive it" if existing["archived_at"] else "already exists"
                raise ExpenseError(f"The category '{existing['name']}' {state}.")
            conn.execute(
                "INSERT INTO categories (name, created_at) VALUES (?, ?)", (name, _now())
            )
        return {"name": name, "archived": False}

    @staticmethod
    def _category_row(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT id, name, archived_at FROM categories WHERE name = ?",
            (validation.clean_category_name(name),),
        ).fetchone()
        if row is None:
            raise ExpenseError(f"The category '{name}' doesn't exist.")
        return row

    def rename_category(self, name: str, new_name: str) -> dict:
        new_name = validation.clean_category_name(new_name)
        with self._connection() as conn, transaction(conn):
            row = self._category_row(conn, name)
            clash = conn.execute(
                "SELECT id FROM categories WHERE name = ? AND id != ?", (new_name, row["id"])
            ).fetchone()
            if clash:
                raise ExpenseError(f"The category '{new_name}' already exists. Merge them instead.")
            conn.execute("UPDATE categories SET name = ? WHERE id = ?", (new_name, row["id"]))
        return {"name": new_name}

    def set_category_archived(self, name: str, archived: bool) -> dict:
        with self._connection() as conn, transaction(conn):
            row = self._category_row(conn, name)
            if bool(row["archived_at"]) == archived:
                state = "archived" if archived else "active"
                raise ExpenseError(f"The category '{row['name']}' is already {state}.")
            conn.execute(
                "UPDATE categories SET archived_at = ? WHERE id = ?",
                (_now() if archived else None, row["id"]),
            )
        return {"name": row["name"], "archived": archived}

    def merge_category(self, name: str, into: str) -> dict:
        """Move every expense, including deleted ones, and archive the old category."""
        with self._connection() as conn, transaction(conn):
            source = self._category_row(conn, name)
            target_id = self._active_category_id(conn, into)
            if source["id"] == target_id:
                raise ExpenseError("A category can't be merged into itself.")
            now = _now()
            moved = conn.execute(
                "UPDATE expenses SET category_id = ?, updated_at = ? WHERE category_id = ?",
                (target_id, now, source["id"]),
            ).rowcount
            conn.execute(
                "UPDATE categories SET archived_at = coalesce(archived_at, ?) WHERE id = ?",
                (now, source["id"]),
            )
        return {"merged": source["name"], "into": into, "expenses_moved": moved}

    def summarize(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        group_by: str = "category",
        category: str | None = None,
        currency: str | None = None,
    ) -> dict:
        if group_by not in GROUP_EXPRESSIONS:
            raise ExpenseError(f"group_by must be one of: {', '.join(GROUP_EXPRESSIONS)}.")
        where, params = self._filters(start_date, end_date, category, None, currency)
        group = GROUP_EXPRESSIONS[group_by]
        order = "e.currency, grp" if group_by == "month" else "e.currency, total DESC, grp"
        with self._connection() as conn:
            totals = conn.execute(
                f"""SELECT e.currency, sum(e.amount_minor) AS total, count(*) AS n
                {EXPENSE_FROM} WHERE {where} GROUP BY e.currency ORDER BY e.currency""",
                params,
            ).fetchall()
            groups = conn.execute(
                f"""SELECT {group} AS grp, e.currency, sum(e.amount_minor) AS total, count(*) AS n
                {EXPENSE_FROM} WHERE {where} GROUP BY {group}, e.currency ORDER BY {order}""",
                params,
            ).fetchall()
        return {
            "start_date": start_date,
            "end_date": end_date,
            "group_by": group_by,
            "totals": [{**_money(r["total"], r["currency"]), "count": r["n"]} for r in totals],
            "groups": [
                {"key": r["grp"], **_money(r["total"], r["currency"]), "count": r["n"]}
                for r in groups
            ],
        }

    def export_csv(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        include_deleted: bool = False,
    ) -> dict:
        where, params = self._filters(start_date, end_date, include_deleted=include_deleted)
        with self._connection() as conn:
            rows = conn.execute(
                f"""SELECT {EXPENSE_COLUMNS} {EXPENSE_FROM} WHERE {where}
                ORDER BY e.occurred_on, e.created_at, e.id""",
                params,
            ).fetchall()

        export_dir = self.settings.export_dir
        export_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = export_dir / f"expenses-{datetime.now():%Y%m%d-%H%M%S-%f}.csv"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(_expense(row) for row in rows)
        return {"path": str(path), "rows": len(rows)}

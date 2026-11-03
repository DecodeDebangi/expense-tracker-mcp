"""The MCP tools. They validate nothing themselves and contain no SQL."""

from collections.abc import Callable
from functools import wraps
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from expense_tracker.backup import backup_health
from expense_tracker.config import load_settings
from expense_tracker.errors import ExpenseError
from expense_tracker.storage import Storage

INSTRUCTIONS = """Tracks the user's personal expenses.

Always take figures from these tools and never calculate totals yourself. Amounts in different \
currencies are never added together. Leave occurred_on out unless the user names a date, so that \
the server uses today's date. If add_expense returns possible_duplicate, nothing was saved: ask \
the user before retrying with allow_duplicate=true."""

READ_ONLY = ToolAnnotations(readOnlyHint=True)


def _readable_errors[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Show validation messages to Claude; the SDK hides other exceptions' details."""

    @wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return function(*args, **kwargs)
        except ExpenseError as error:
            raise ToolError(str(error)) from error

    return wrapper


def create_server(storage: Storage) -> MCPServer:
    server = MCPServer("expense-tracker", instructions=INSTRUCTIONS)

    @server.tool()
    @_readable_errors
    def add_expense(
        amount: str | int | float,
        merchant: str,
        category: str,
        occurred_on: str | None = None,
        currency: str | None = None,
        note: str | None = None,
        allow_duplicate: bool = False,
        client_request_id: str | None = None,
    ) -> dict[str, Any]:
        """Log an expense.

        Args:
            amount: The amount in normal units, such as 450.50 for ₹450.50.
            merchant: Who was paid.
            category: An existing category; see list_categories.
            occurred_on: YYYY-MM-DD. Leave out for today.
            currency: ISO code. Leave out for the default currency.
            note: Optional detail.
            allow_duplicate: Save even if a matching expense exists. Only set after the user confirms.
            client_request_id: Optional unique ID that makes retrying this request safe.
        """
        return storage.add_expense(
            amount, merchant, category, occurred_on, currency, note, allow_duplicate, client_request_id
        )

    @server.tool(annotations=READ_ONLY)
    @_readable_errors
    def list_expenses(
        start_date: str | None = None,
        end_date: str | None = None,
        category: str | None = None,
        merchant: str | None = None,
        currency: str | None = None,
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List expenses, newest first. Dates are YYYY-MM-DD and inclusive; merchant matches part of the name."""
        return storage.list_expenses(
            start_date, end_date, category, merchant, currency, include_deleted, limit, offset
        )

    @server.tool()
    @_readable_errors
    def update_expense(
        expense_id: str,
        amount: str | int | float | None = None,
        currency: str | None = None,
        occurred_on: str | None = None,
        merchant: str | None = None,
        category: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Change an expense. Leave out anything that stays the same; an empty note clears it."""
        return storage.update_expense(expense_id, amount, currency, occurred_on, merchant, category, note)

    @server.tool()
    @_readable_errors
    def delete_expense(expense_id: str) -> dict[str, Any]:
        """Delete an expense. It can be brought back with restore_expense."""
        return storage.delete_expense(expense_id)

    @server.tool()
    @_readable_errors
    def restore_expense(expense_id: str) -> dict[str, Any]:
        """Bring back a deleted expense."""
        return storage.restore_expense(expense_id)

    @server.tool(annotations=READ_ONLY)
    @_readable_errors
    def list_categories(include_archived: bool = False) -> dict[str, Any]:
        """List categories and how many expenses each one has."""
        return {"categories": storage.list_categories(include_archived)}

    @server.tool()
    @_readable_errors
    def manage_categories(
        action: Literal["create", "rename", "archive", "unarchive", "merge"],
        name: str,
        new_name: str | None = None,
        into: str | None = None,
    ) -> dict[str, Any]:
        """Create, rename, archive, unarchive or merge categories.

        Args:
            action: What to do.
            name: The category to act on.
            new_name: Required for rename.
            into: Required for merge: the category that receives the expenses. The old one is archived.
        """
        if action == "create":
            return storage.create_category(name)
        if action == "rename":
            if not new_name:
                raise ToolError("Renaming needs new_name.")
            return storage.rename_category(name, new_name)
        if action == "merge":
            if not into:
                raise ToolError("Merging needs into.")
            return storage.merge_category(name, into)
        return storage.set_category_archived(name, action == "archive")

    @server.tool(annotations=READ_ONLY)
    @_readable_errors
    def summarize(
        start_date: str | None = None,
        end_date: str | None = None,
        group_by: Literal["category", "merchant", "month"] = "category",
        category: str | None = None,
        currency: str | None = None,
    ) -> dict[str, Any]:
        """Exact spending totals and counts for a period, per currency. Dates are YYYY-MM-DD and inclusive."""
        return storage.summarize(start_date, end_date, group_by, category, currency)

    @server.tool()
    @_readable_errors
    def export_csv(
        start_date: str | None = None, end_date: str | None = None, include_deleted: bool = False
    ) -> dict[str, Any]:
        """Write expenses to a CSV file on this computer, and return its location."""
        return storage.export_csv(start_date, end_date, include_deleted)

    @server.tool(annotations=READ_ONLY)
    @_readable_errors
    def backup_status() -> dict[str, Any]:
        """Report whether the daily backup is working."""
        return backup_health(storage.settings)

    return server


def main() -> None:
    create_server(Storage(load_settings())).run()

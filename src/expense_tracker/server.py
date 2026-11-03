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

INSTRUCTIONS = """Tracks the user's personal expenses and income.

Always take figures from these tools and never calculate totals yourself. Amounts in different \
currencies are never added together. Leave occurred_on out unless the user names a date, so that \
the server uses today's date. Choose a subcategory when one fits, and leave it out when none does; \
see list_categories. Leave merchant out if the user doesn't name one, and never invent one. If \
add_expense or add_income returns possible_duplicate, nothing was saved: ask the user before \
retrying with allow_duplicate=true.

Log money received with add_income, as the take-home amount. For arrears or other pay covering \
past months, also set the period. If the user says an expense was paid from particular income, set \
funded_by_income_id, using the id from add_income or list_income; otherwise leave it out."""

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
        category: str,
        merchant: str | None = None,
        occurred_on: str | None = None,
        currency: str | None = None,
        note: str | None = None,
        allow_duplicate: bool = False,
        client_request_id: str | None = None,
        subcategory: str | None = None,
        funded_by_income_id: str | None = None,
    ) -> dict[str, Any]:
        """Log an expense.

        Args:
            amount: The amount in normal units, such as 450.50 for ₹450.50.
            category: An existing category; see list_categories.
            merchant: Who was paid, if the user said. Leave out rather than guessing.
            occurred_on: YYYY-MM-DD. Leave out for today.
            currency: ISO code. Leave out for the default currency.
            note: Optional detail.
            allow_duplicate: Save even if a matching expense exists. Only set after the user confirms.
            client_request_id: Optional unique ID that makes retrying this request safe.
            subcategory: Optional subcategory within the category.
            funded_by_income_id: The income this was paid from, if the user said.
        """
        return storage.add_expense(
            amount, merchant, category, occurred_on, currency, note, allow_duplicate, client_request_id,
            subcategory, funded_by_income_id,
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
        subcategory: str | None = None,
        funded_by_income_id: str | None = None,
    ) -> dict[str, Any]:
        """List expenses, newest first. Dates are YYYY-MM-DD and inclusive; merchant matches part of the name."""
        return storage.list_expenses(
            start_date, end_date, category, merchant, currency, include_deleted, limit, offset, subcategory,
            funded_by_income_id,
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
        subcategory: str | None = None,
        funded_by_income_id: str | None = None,
    ) -> dict[str, Any]:
        """Change an expense. Leave out anything that stays the same; an empty value clears an optional field.

        Changing the category clears the subcategory unless a new one is given.
        """
        return storage.update_expense(
            expense_id, amount, currency, occurred_on, merchant, category, note, subcategory,
            funded_by_income_id,
        )

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
        """List categories with their subcategories, and how many expenses each one has."""
        return {"categories": storage.list_categories(include_archived)}

    @server.tool()
    @_readable_errors
    def manage_categories(
        action: Literal["create", "rename", "archive", "unarchive", "merge"],
        name: str,
        new_name: str | None = None,
        into: str | None = None,
        subcategory: str | None = None,
    ) -> dict[str, Any]:
        """Create, rename, archive, unarchive or merge categories and subcategories.

        Args:
            action: What to do.
            name: The category to act on, or the parent category if subcategory is given.
            new_name: Required for rename.
            into: Required for merge: the category that receives the expenses, or the subcategory
                in the same category if subcategory is given. The old one is archived.
            subcategory: Act on this subcategory of the named category instead of the category.
        """
        if subcategory:
            if action == "create":
                return storage.create_subcategory(name, subcategory)
            if action == "rename":
                if not new_name:
                    raise ToolError("Renaming needs new_name.")
                return storage.rename_subcategory(name, subcategory, new_name)
            if action == "merge":
                if not into:
                    raise ToolError("Merging needs into.")
                return storage.merge_subcategory(name, subcategory, into)
            return storage.set_subcategory_archived(name, subcategory, action == "archive")
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
        group_by: Literal["category", "subcategory", "merchant", "month", "day"] = "category",
        category: str | None = None,
        currency: str | None = None,
        subcategory: str | None = None,
    ) -> dict[str, Any]:
        """Exact spending totals and counts for a period, per currency. Dates are YYYY-MM-DD and inclusive.

        Category totals include all their subcategories.
        """
        return storage.summarize(start_date, end_date, group_by, category, currency, subcategory)

    @server.tool()
    @_readable_errors
    def export_csv(
        start_date: str | None = None,
        end_date: str | None = None,
        include_deleted: bool = False,
        kind: Literal["expenses", "income"] = "expenses",
    ) -> dict[str, Any]:
        """Write expenses or income to a CSV file on this computer, and return its location."""
        if kind == "income":
            return storage.export_income_csv(start_date, end_date, include_deleted)
        return storage.export_csv(start_date, end_date, include_deleted)

    @server.tool()
    @_readable_errors
    def add_income(
        amount: str | int | float,
        income_type: str,
        received_on: str | None = None,
        currency: str | None = None,
        source: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        note: str | None = None,
        allow_duplicate: bool = False,
        client_request_id: str | None = None,
    ) -> dict[str, Any]:
        """Log money received, such as salary, arrears or a bonus.

        Args:
            amount: The take-home amount in normal units.
            income_type: An existing type; see list_income_types.
            received_on: YYYY-MM-DD. Leave out for today.
            currency: ISO code. Leave out for the default currency.
            source: Who paid it, if the user said.
            period_start: First month covered, as YYYY-MM, for arrears or similar.
            period_end: Last month covered, as YYYY-MM. Leave out for a single month.
            note: Optional detail.
            allow_duplicate: Save even if a matching entry exists. Only set after the user confirms.
            client_request_id: Optional unique ID that makes retrying this request safe.
        """
        return storage.add_income(
            amount, income_type, received_on, currency, source, period_start, period_end, note,
            allow_duplicate, client_request_id,
        )

    @server.tool(annotations=READ_ONLY)
    @_readable_errors
    def list_income(
        start_date: str | None = None,
        end_date: str | None = None,
        income_type: str | None = None,
        source: str | None = None,
        currency: str | None = None,
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List income, newest first, with how much is linked to expenses and how much remains.

        Remaining only counts expenses linked to that entry, so it isn't the user's bank balance.
        """
        return storage.list_income(
            start_date, end_date, income_type, source, currency, include_deleted, limit, offset
        )

    @server.tool()
    @_readable_errors
    def update_income(
        income_id: str,
        amount: str | int | float | None = None,
        currency: str | None = None,
        received_on: str | None = None,
        income_type: str | None = None,
        source: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Change an income entry. Leave out anything that stays the same; an empty value clears an optional field."""
        return storage.update_income(
            income_id, amount, currency, received_on, income_type, source, period_start, period_end, note
        )

    @server.tool()
    @_readable_errors
    def delete_income(income_id: str) -> dict[str, Any]:
        """Delete an income entry that has no linked expenses. It can be brought back with restore_income."""
        return storage.delete_income(income_id)

    @server.tool()
    @_readable_errors
    def restore_income(income_id: str) -> dict[str, Any]:
        """Bring back a deleted income entry."""
        return storage.restore_income(income_id)

    @server.tool(annotations=READ_ONLY)
    @_readable_errors
    def list_income_types(include_archived: bool = False) -> dict[str, Any]:
        """List income types, whether each counts as regular income, and how many entries each has."""
        return {"income_types": storage.list_income_types(include_archived)}

    @server.tool()
    @_readable_errors
    def manage_income_types(
        action: Literal["create", "rename", "archive", "unarchive", "merge", "set_regular"],
        name: str,
        new_name: str | None = None,
        into: str | None = None,
        regular: bool = False,
    ) -> dict[str, Any]:
        """Create, rename, archive, unarchive or merge income types, or set whether one counts as regular.

        Args:
            action: What to do.
            name: The income type to act on.
            new_name: Required for rename.
            into: Required for merge: the type that receives the entries. The old one is archived.
            regular: For create and set_regular: whether it's regular income like salary, not a one-off.
        """
        if action == "create":
            return storage.create_income_type(name, regular)
        if action == "rename":
            if not new_name:
                raise ToolError("Renaming needs new_name.")
            return storage.rename_income_type(name, new_name)
        if action == "merge":
            if not into:
                raise ToolError("Merging needs into.")
            return storage.merge_income_type(name, into)
        if action == "set_regular":
            return storage.set_income_type_regular(name, regular)
        return storage.set_income_type_archived(name, action == "archive")

    @server.tool(annotations=READ_ONLY)
    @_readable_errors
    def summarize_income(
        start_date: str | None = None,
        end_date: str | None = None,
        group_by: Literal["type", "source", "month"] = "type",
        income_type: str | None = None,
        currency: str | None = None,
    ) -> dict[str, Any]:
        """Exact income totals for a period, per currency, split into regular and one-off. Dates are YYYY-MM-DD and inclusive."""
        return storage.summarize_income(start_date, end_date, group_by, income_type, currency)

    @server.tool(annotations=READ_ONLY)
    @_readable_errors
    def cash_flow(
        start_date: str | None = None, end_date: str | None = None, currency: str | None = None
    ) -> dict[str, Any]:
        """Income against spending for each month and overall, with net and savings rate, per currency.

        Income counts in the month it was received, including arrears for earlier months.
        """
        return storage.cash_flow(start_date, end_date, currency)

    @server.tool(annotations=READ_ONLY)
    @_readable_errors
    def backup_status() -> dict[str, Any]:
        """Report whether the daily backup is working."""
        return backup_health(storage.settings)

    return server


def main() -> None:
    create_server(Storage(load_settings())).run()

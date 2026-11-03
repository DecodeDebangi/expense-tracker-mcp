"""Exercises the tools the way Claude does: through a real MCP client."""

import asyncio
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

from expense_tracker.config import Settings
from expense_tracker.server import create_server
from expense_tracker.storage import Storage

EXPECTED_TOOLS = {
    "add_expense", "list_expenses", "update_expense", "delete_expense", "restore_expense",
    "list_categories", "manage_categories", "summarize", "export_csv", "backup_status",
}


def call(storage: Storage, *calls: tuple[str, dict]) -> list:
    async def run() -> list:
        async with Client(create_server(storage)) as client:
            return [await client.call_tool(name, args) for name, args in calls]

    return asyncio.run(run())


def error_text(result) -> str:
    assert result.is_error
    return result.content[0].text


def test_all_tools_are_listed_with_descriptions(storage: Storage) -> None:
    async def run():
        async with Client(create_server(storage)) as client:
            return (await client.list_tools()).tools

    tools = {tool.name: tool for tool in asyncio.run(run())}
    assert set(tools) == EXPECTED_TOOLS
    assert all(tool.description for tool in tools.values())
    assert "amount" in tools["add_expense"].input_schema["required"]
    assert tools["summarize"].annotations.read_only_hint is True


def test_logging_and_summarising_through_the_client(storage: Storage) -> None:
    added, total = call(
        storage,
        ("add_expense", {"amount": 450.5, "merchant": "Demo Cafe", "category": "Food"}),
        ("summarize", {"group_by": "category"}),
    )
    assert not added.is_error
    assert added.structured_content["expense"]["amount_minor"] == 45050
    assert total.structured_content["totals"][0]["total"] == "450.50"


def test_validation_messages_reach_claude(storage: Storage) -> None:
    amount, category, day, currency = call(
        storage,
        ("add_expense", {"amount": "abc", "merchant": "A", "category": "Food"}),
        ("add_expense", {"amount": 5, "merchant": "A", "category": "Nope"}),
        ("add_expense", {"amount": 5, "merchant": "A", "category": "Food", "occurred_on": "yesterday"}),
        ("add_expense", {"amount": 5, "merchant": "A", "category": "Food", "currency": "XYZ"}),
    )
    assert "isn't an amount" in error_text(amount)
    assert "Available:" in error_text(category)
    assert "YYYY-MM-DD" in error_text(day)
    assert "currency code" in error_text(currency)


def test_duplicate_is_reported_not_an_error(storage: Storage) -> None:
    args = {"amount": 5, "merchant": "A", "category": "Food"}
    _, second = call(storage, ("add_expense", args), ("add_expense", args))
    assert not second.is_error
    assert second.structured_content["status"] == "possible_duplicate"


def test_manage_categories_through_the_client(storage: Storage) -> None:
    created, missing, merged = call(
        storage,
        ("manage_categories", {"action": "create", "name": "Pets"}),
        ("manage_categories", {"action": "rename", "name": "Pets"}),
        ("manage_categories", {"action": "merge", "name": "Pets", "into": "Other"}),
    )
    assert not created.is_error
    assert "new_name" in error_text(missing)
    assert merged.structured_content["into"] == "Other"


def test_backup_status_tool(storage: Storage) -> None:
    result = call(storage, ("backup_status", {}))[0]
    assert result.structured_content["healthy"] is False


def test_real_server_process_over_stdio(tmp_path: Path) -> None:
    db = tmp_path / "with space" / "expenses.db"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from expense_tracker.server import main; main()"],
        env={**os.environ, "EXPENSE_DB_PATH": str(db)},
    )

    async def run():
        async with Client(params) as client:
            await client.call_tool(
                "add_expense", {"amount": "99.99", "merchant": "Demo Cafe", "category": "Food"}
            )
            return await client.call_tool("summarize", {})

    result = asyncio.run(run())
    assert result.structured_content["totals"][0]["total_minor"] == 9999
    assert Storage(Settings(db_path=db)).list_expenses()["total"] == 1

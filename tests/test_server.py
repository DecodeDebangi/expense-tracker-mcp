from expense_tracker.server import server


def test_server_is_named() -> None:
    assert server.name == "expense-tracker"

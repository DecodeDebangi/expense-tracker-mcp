from datetime import date, timedelta

import pytest

from expense_tracker.errors import ExpenseError
from expense_tracker.validation import (
    format_amount,
    parse_amount,
    parse_currency,
    parse_date,
    parse_expense_date,
    parse_month,
    parse_period,
)


@pytest.mark.parametrize(
    ("value", "currency", "minor"),
    [
        ("450.50", "INR", 45050),
        (450.5, "INR", 45050),
        (0.1, "INR", 10),
        (19.99, "USD", 1999),
        (450, "INR", 45000),
        ("1,25,000.50", "INR", 12500050),
        ("1000", "JPY", 1000),
        ("1.500", "KWD", 1500),
    ],
)
def test_amounts_convert_exactly(value, currency, minor) -> None:
    assert parse_amount(value, currency) == minor


@pytest.mark.parametrize(
    ("value", "currency"),
    [
        ("0", "INR"),
        ("-5", "INR"),
        ("abc", "INR"),
        ("", "INR"),
        ("NaN", "INR"),
        ("Infinity", "INR"),
        ("10.555", "INR"),
        ("10.5", "JPY"),
        ("1e30", "INR"),
        (True, "INR"),
    ],
)
def test_bad_amounts_are_rejected(value, currency) -> None:
    with pytest.raises(ExpenseError):
        parse_amount(value, currency)


@pytest.mark.parametrize(
    ("minor", "currency", "text"),
    [(45050, "INR", "450.50"), (5, "INR", "0.05"), (1000, "JPY", "1000"), (1500, "KWD", "1.500")],
)
def test_amounts_format_exactly(minor, currency, text) -> None:
    assert format_amount(minor, currency) == text


def test_currency_is_normalised_and_checked() -> None:
    assert parse_currency(" inr ") == "INR"
    with pytest.raises(ExpenseError):
        parse_currency("XYZ")


@pytest.mark.parametrize("value", ["2026-02-30", "03/11/2026", "20261103", "2026-1-3"])
def test_bad_dates_are_rejected(value) -> None:
    with pytest.raises(ExpenseError):
        parse_date(value)


def test_expense_date_defaults_to_today_and_rejects_the_future() -> None:
    assert parse_expense_date(None) == date.today().isoformat()
    with pytest.raises(ExpenseError):
        parse_expense_date((date.today() + timedelta(days=1)).isoformat())


@pytest.mark.parametrize("value", ["2026-13", "2026-00", "2026-4", "26-04", "April"])
def test_bad_months_are_rejected(value) -> None:
    with pytest.raises(ExpenseError):
        parse_month(value)


def test_period_rules() -> None:
    assert parse_period(None, None) == (None, None)
    assert parse_period("2026-04", None) == ("2026-04", "2026-04")
    assert parse_period("2026-04", "2026-10") == ("2026-04", "2026-10")
    assert parse_period("2025-12", "2026-01") == ("2025-12", "2026-01")

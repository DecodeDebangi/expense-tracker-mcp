import csv
from pathlib import Path

import pytest

from expense_tracker.errors import ExpenseError
from expense_tracker.storage import Storage


def add(storage: Storage, **overrides) -> dict:
    fields = {"amount": "100", "merchant": "Demo Cafe", "category": "Food", "occurred_on": "2026-05-01"}
    return storage.add_expense(**{**fields, **overrides})


def test_add_uses_defaults_and_stores_exact_amount(storage: Storage) -> None:
    result = storage.add_expense("450.50", "  Demo   Cafe ", "food")
    expense = result["expense"]
    assert result["saved"] is True
    assert expense["amount_minor"] == 45050
    assert expense["amount"] == "450.50"
    assert expense["currency"] == "INR"
    assert expense["merchant"] == "Demo Cafe"
    assert expense["category"] == "Food"


def test_unknown_category_lists_the_options(storage: Storage) -> None:
    with pytest.raises(ExpenseError, match="Groceries"):
        add(storage, category="Nope")


def test_likely_duplicate_is_not_saved_until_confirmed(storage: Storage) -> None:
    add(storage)
    result = add(storage, merchant="DEMO CAFE")
    assert result["status"] == "possible_duplicate"
    assert result["saved"] is False
    assert storage.list_expenses()["total"] == 1

    assert add(storage, allow_duplicate=True)["saved"] is True
    assert storage.list_expenses()["total"] == 2


def test_deleted_expense_does_not_count_as_duplicate(storage: Storage) -> None:
    storage.delete_expense(add(storage)["expense"]["id"])
    assert add(storage)["saved"] is True


def test_retrying_with_a_request_id_does_not_save_twice(storage: Storage) -> None:
    first = add(storage, client_request_id="phone-1")
    retry = add(storage, client_request_id="phone-1")
    assert retry["status"] == "already_saved"
    assert retry["expense"]["id"] == first["expense"]["id"]
    assert storage.list_expenses()["total"] == 1


def test_delete_hides_from_lists_and_totals_and_restore_brings_back(seeded: Storage) -> None:
    before = seeded.summarize(currency="USD")["totals"][0]["total_minor"]
    target = seeded.list_expenses(currency="USD")["expenses"][0]["id"]
    assert before == 1999

    assert seeded.delete_expense(target)["deleted_at"]
    assert seeded.list_expenses(currency="USD")["total"] == 0
    assert seeded.summarize(currency="USD")["totals"] == []
    assert seeded.list_expenses(currency="USD", include_deleted=True)["total"] == 1
    with pytest.raises(ExpenseError):
        seeded.delete_expense(target)
    with pytest.raises(ExpenseError):
        seeded.update_expense(target, amount="1")

    assert seeded.restore_expense(target)["deleted_at"] is None
    assert seeded.summarize(currency="USD")["totals"][0]["total_minor"] == 1999


def test_update_changes_only_what_is_given(storage: Storage) -> None:
    original = add(storage, note="keep")["expense"]
    updated = storage.update_expense(original["id"], amount="250.75", category="Groceries")
    assert updated["amount_minor"] == 25075
    assert updated["category"] == "Groceries"
    assert updated["merchant"] == original["merchant"]
    assert updated["note"] == "keep"
    assert storage.update_expense(original["id"], note="")["note"] is None


def test_changing_currency_requires_the_amount(storage: Storage) -> None:
    expense = add(storage)["expense"]
    with pytest.raises(ExpenseError, match="amount"):
        storage.update_expense(expense["id"], currency="USD")
    assert storage.update_expense(expense["id"], currency="JPY", amount="500")["amount_minor"] == 500


def test_update_needs_a_change_and_a_real_id(storage: Storage) -> None:
    expense = add(storage)["expense"]
    with pytest.raises(ExpenseError):
        storage.update_expense(expense["id"])
    with pytest.raises(ExpenseError):
        storage.update_expense("missing", amount="1")


def test_list_filters_and_paging(seeded: Storage) -> None:
    february = seeded.list_expenses(start_date="2026-02-01", end_date="2026-02-28")
    assert february["total"] == 5
    assert seeded.list_expenses(category="rent")["total"] == 3
    assert seeded.list_expenses(merchant="grocer")["total"] == 3
    assert seeded.list_expenses(merchant="%")["total"] == 0

    page = seeded.list_expenses(limit=2, offset=1)
    assert page["total"] == 14
    assert [e["occurred_on"] for e in page["expenses"]] == ["2026-03-12", "2026-03-10"]


def test_list_rejects_bad_paging_and_reversed_dates(storage: Storage) -> None:
    with pytest.raises(ExpenseError):
        storage.list_expenses(limit=501)
    with pytest.raises(ExpenseError):
        storage.list_expenses(start_date="2026-02-01", end_date="2026-01-01")


# Expected figures below were worked out by hand from tests/fixtures/seed_expenses.csv.
def test_totals_keep_currencies_apart(seeded: Storage) -> None:
    totals = seeded.summarize()["totals"]
    assert totals == [
        {"currency": "INR", "total": "45460.25", "total_minor": 4546025, "count": 13},
        {"currency": "USD", "total": "19.99", "total_minor": 1999, "count": 1},
    ]


def test_summary_by_category(seeded: Storage) -> None:
    groups = seeded.summarize(currency="INR")["groups"]
    assert [(g["key"], g["total_minor"], g["count"]) for g in groups] == [
        ("Rent", 3750000, 3),
        ("Groceries", 253825, 3),
        ("Health", 250000, 1),
        ("Subscriptions", 194700, 3),
        ("Food", 79500, 2),
        ("Transport", 18000, 1),
    ]


def test_summary_by_month_is_in_date_order_and_adds_up(seeded: Storage) -> None:
    groups = seeded.summarize(group_by="month", currency="INR")["groups"]
    assert [(g["key"], g["total_minor"]) for g in groups] == [
        ("2026-01", 1449400),
        ("2026-02", 1703650),
        ("2026-03", 1392975),
    ]
    assert sum(g["total_minor"] for g in groups) == 4546025


def test_summary_by_merchant_ignores_case(storage: Storage) -> None:
    add(storage, merchant="Demo Cafe", amount="100")
    add(storage, merchant="DEMO CAFE", amount="50", occurred_on="2026-05-02")
    groups = storage.summarize(group_by="merchant")["groups"]
    assert len(groups) == 1
    assert groups[0]["total_minor"] == 15000


def test_summary_date_range_is_inclusive(seeded: Storage) -> None:
    result = seeded.summarize(start_date="2026-01-10", end_date="2026-01-12", currency="INR")
    assert result["totals"][0]["total_minor"] == 1250000 + 64900


def test_sums_stay_exact_over_many_small_amounts(storage: Storage) -> None:
    for day in range(1, 11):
        add(storage, amount=0.1, occurred_on=f"2026-05-{day:02d}")
    assert storage.summarize()["totals"][0]["total"] == "1.00"


def test_category_lifecycle(storage: Storage) -> None:
    storage.create_category("Pets")
    with pytest.raises(ExpenseError):
        storage.create_category("pets")
    storage.rename_category("Pets", "Pet Care")
    expense = add(storage, category="Pet Care")["expense"]
    assert expense["category"] == "Pet Care"

    storage.set_category_archived("Pet Care", True)
    with pytest.raises(ExpenseError, match="archived"):
        add(storage, category="Pet Care", merchant="Other")
    names = {c["name"] for c in storage.list_categories()}
    assert "Pet Care" not in names
    assert "Pet Care" in {c["name"] for c in storage.list_categories(include_archived=True)}
    assert storage.list_expenses()["expenses"][0]["category"] == "Pet Care"

    storage.set_category_archived("Pet Care", False)
    assert add(storage, category="Pet Care", merchant="Other")["saved"] is True


def test_rename_cannot_collide(storage: Storage) -> None:
    with pytest.raises(ExpenseError, match="Merge"):
        storage.rename_category("Food", "Groceries")
    assert storage.rename_category("Food", "FOOD")["name"] == "FOOD"


def test_merge_moves_everything_and_keeps_totals(seeded: Storage) -> None:
    before = seeded.summarize(currency="INR")["totals"][0]["total_minor"]
    deleted = seeded.list_expenses(category="Food")["expenses"][0]["id"]
    seeded.delete_expense(deleted)

    assert seeded.merge_category("Food", "Groceries")["expenses_moved"] == 2
    seeded.restore_expense(deleted)

    assert seeded.summarize(currency="INR")["totals"][0]["total_minor"] == before
    assert seeded.list_expenses(category="Food")["total"] == 0
    assert seeded.list_expenses(category="Groceries")["total"] == 5
    with pytest.raises(ExpenseError):
        seeded.merge_category("Rent", "Rent")


def test_export_writes_exact_data_with_private_permissions(seeded: Storage) -> None:
    result = seeded.export_csv(start_date="2026-03-01")
    path = Path(result["path"])
    assert result["rows"] == 4
    assert path.parent == seeded.settings.export_dir
    assert oct(path.stat().st_mode)[-3:] == "600"
    with open(path, newline="") as file:
        rows = list(csv.DictReader(file))
    assert [r["amount"] for r in rows][0] == "780.75"
    assert rows[-1]["currency"] == "USD"

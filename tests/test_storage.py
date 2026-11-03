import csv
import sqlite3
from pathlib import Path

import pytest

from expense_tracker.db import connect
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


def add_loan(storage: Storage, merchant: str, subcategory: str, amount: str, day: str) -> dict:
    return storage.add_expense(
        amount, merchant, "loans & emis", day, subcategory=subcategory
    )["expense"]


def test_expense_can_have_a_subcategory_or_none(storage: Storage) -> None:
    assert add_loan(storage, "Demo Bank", "home loan", "25000", "2026-05-01")["subcategory"] == "Home Loan"
    assert add(storage)["expense"]["subcategory"] is None


def test_subcategory_must_exist_in_the_expense_category(storage: Storage) -> None:
    with pytest.raises(ExpenseError, match="Dining Out"):
        add(storage, subcategory="Home Loan")
    assert storage.list_expenses()["total"] == 0


def test_database_rejects_a_subcategory_from_another_category(storage: Storage) -> None:
    expense = add_loan(storage, "Demo Bank", "Home Loan", "25000", "2026-05-01")
    conn = connect(storage.settings.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE expenses SET category_id = (SELECT id FROM categories WHERE name = 'Food') WHERE id = ?",
                (expense["id"],),
            )
    finally:
        conn.close()


def test_category_totals_include_subcategories_and_group_by_shows_them(storage: Storage) -> None:
    add_loan(storage, "Demo Bank", "Home Loan", "25000", "2026-05-01")
    add_loan(storage, "Demo Finance", "Two-wheeler Loan", "3000.50", "2026-05-02")
    storage.add_expense("1000", "Other Lender", "Loans & EMIs", "2026-05-03")

    by_category = storage.summarize()["groups"]
    assert [(g["key"], g["total_minor"], g["count"]) for g in by_category] == [("Loans & EMIs", 2900050, 3)]

    by_subcategory = storage.summarize(group_by="subcategory")["groups"]
    assert [(g["key"], g["total_minor"]) for g in by_subcategory] == [
        ("Loans & EMIs / Home Loan", 2500000),
        ("Loans & EMIs / Two-wheeler Loan", 300050),
        ("Loans & EMIs / (none)", 100000),
    ]
    assert sum(g["total_minor"] for g in by_subcategory) == 2900050

    assert storage.summarize(subcategory="Home Loan")["totals"][0]["total_minor"] == 2500000
    assert storage.list_expenses(subcategory="two-wheeler loan")["total"] == 1


def test_update_subcategory_rules(storage: Storage) -> None:
    expense = add_loan(storage, "Demo Bank", "Home Loan", "25000", "2026-05-01")
    expense_id = expense["id"]

    assert storage.update_expense(expense_id, note="kept")["subcategory"] == "Home Loan"
    assert storage.update_expense(expense_id, subcategory="Car Loan")["subcategory"] == "Car Loan"
    assert storage.update_expense(expense_id, category="Loans & EMIs")["subcategory"] == "Car Loan"
    assert storage.update_expense(expense_id, subcategory="")["subcategory"] is None

    storage.update_expense(expense_id, subcategory="Home Loan")
    moved = storage.update_expense(expense_id, category="Food")
    assert (moved["category"], moved["subcategory"]) == ("Food", None)

    both = storage.update_expense(expense_id, category="Transport", subcategory="Fuel")
    assert (both["category"], both["subcategory"]) == ("Transport", "Fuel")
    with pytest.raises(ExpenseError):
        storage.update_expense(expense_id, subcategory="Home Loan")


def test_subcategory_lifecycle(storage: Storage) -> None:
    storage.create_subcategory("Food", "Tiffin")
    with pytest.raises(ExpenseError):
        storage.create_subcategory("food", "tiffin")
    storage.rename_subcategory("Food", "Tiffin", "Tiffin Service")
    expense = add(storage, subcategory="Tiffin Service")["expense"]

    storage.set_subcategory_archived("Food", "Tiffin Service", True)
    with pytest.raises(ExpenseError, match="archived"):
        add(storage, merchant="Other", subcategory="Tiffin Service")
    food = next(c for c in storage.list_categories() if c["name"] == "Food")
    assert "Tiffin Service" not in {s["name"] for s in food["subcategories"]}
    assert storage.list_expenses()["expenses"][0]["subcategory"] == "Tiffin Service"

    storage.set_subcategory_archived("Food", "Tiffin Service", False)
    result = storage.merge_subcategory("Food", "Tiffin Service", "Office Meals")
    assert result["expenses_moved"] == 1
    assert storage.list_expenses()["expenses"][0]["id"] == expense["id"]
    assert storage.list_expenses()["expenses"][0]["subcategory"] == "Office Meals"
    with pytest.raises(ExpenseError):
        storage.rename_subcategory("Food", "Office Meals", "Dining Out")
    with pytest.raises(ExpenseError):
        storage.merge_subcategory("Food", "Office Meals", "Office Meals")


def test_same_subcategory_name_can_exist_in_different_categories(storage: Storage) -> None:
    family = storage.add_expense("100", "Shop", "Family Support", "2026-05-01", subcategory="Gifts")
    other = storage.add_expense("200", "Shop", "Other", "2026-05-02", subcategory="Gifts")
    assert family["expense"]["category"] != other["expense"]["category"]
    groups = storage.summarize(group_by="subcategory")["groups"]
    assert {g["key"] for g in groups} == {"Family Support / Gifts", "Other / Gifts"}


def test_merging_categories_carries_subcategories_and_keeps_totals(storage: Storage) -> None:
    moved_along = storage.add_expense("100", "A", "Family Support", "2026-05-01", subcategory="Medical")["expense"]
    joined = storage.add_expense("200", "B", "Family Support", "2026-05-02", subcategory="Gifts")["expense"]
    plain = storage.add_expense("300", "C", "Family Support", "2026-05-03")["expense"]
    deleted = storage.add_expense("400", "D", "Family Support", "2026-05-04", subcategory="Gifts")["expense"]
    storage.delete_expense(deleted["id"])
    before = storage.summarize()["totals"]

    assert storage.merge_category("Family Support", "Other")["expenses_moved"] == 4

    assert storage.summarize()["totals"] == before
    result = {e["id"]: e for e in storage.list_expenses(include_deleted=True)["expenses"]}
    assert (result[moved_along["id"]]["category"], result[moved_along["id"]]["subcategory"]) == ("Other", "Medical")
    assert (result[joined["id"]]["category"], result[joined["id"]]["subcategory"]) == ("Other", "Gifts")
    assert (result[plain["id"]]["category"], result[plain["id"]]["subcategory"]) == ("Other", None)
    assert (result[deleted["id"]]["category"], result[deleted["id"]]["subcategory"]) == ("Other", "Gifts")
    other = next(c for c in storage.list_categories() if c["name"] == "Other")
    assert sum(s["name"] == "Gifts" for s in other["subcategories"]) == 1
    assert "Medical" in {s["name"] for s in other["subcategories"]}


def test_export_includes_the_subcategory(storage: Storage) -> None:
    add_loan(storage, "Demo Bank", "Home Loan", "25000", "2026-05-01")
    with open(storage.export_csv()["path"], newline="") as file:
        assert next(csv.DictReader(file))["subcategory"] == "Home Loan"


def test_merchant_is_optional_and_never_invented(storage: Storage) -> None:
    expense = storage.add_expense("100", None, "Food", "2026-05-01")["expense"]
    assert expense["merchant"] is None
    assert storage.add_expense("100", "   ", "Food", "2026-05-02")["expense"]["merchant"] is None
    assert storage.summarize(group_by="merchant")["groups"][0]["key"] == "(no merchant)"
    named = storage.update_expense(expense["id"], merchant="Demo Cafe")
    assert named["merchant"] == "Demo Cafe"
    assert storage.update_expense(expense["id"], merchant="")["merchant"] is None


def test_duplicate_check_without_a_merchant_compares_the_category(storage: Storage) -> None:
    storage.add_expense("100", None, "Food", "2026-05-01")
    assert storage.add_expense("100", None, "Food", "2026-05-01")["status"] == "possible_duplicate"
    assert storage.add_expense("100", None, "Transport", "2026-05-01")["saved"] is True
    assert storage.add_expense("100", "Demo Cafe", "Food", "2026-05-01")["saved"] is True
    assert storage.add_expense("100", None, "Food", "2026-05-01", allow_duplicate=True)["saved"] is True


def test_daily_breakdown_is_in_date_order_and_adds_up(seeded: Storage) -> None:
    groups = seeded.summarize(group_by="day", currency="INR", start_date="2026-01-01", end_date="2026-01-31")["groups"]
    assert [(g["key"], g["total_minor"]) for g in groups] == [
        ("2026-01-03", 84500),
        ("2026-01-05", 32000),
        ("2026-01-10", 1250000),
        ("2026-01-12", 64900),
        ("2026-01-18", 18000),
    ]
    assert sum(g["total_minor"] for g in groups) == 1449400


# The user's own example, with answers worked out by hand.
@pytest.fixture
def november(storage: Storage) -> dict:
    salary = storage.add_income("1,00,000", "Salary", "2026-10-31", source="Demo Employer", note="After the hike")["income"]
    arrear = storage.add_income(
        "1,50,000", "Arrear", "2026-10-31", source="Demo Employer", period_start="2026-04", period_end="2026-10"
    )["income"]
    interiors = storage.add_expense(
        "30,000", "Mother", "Home", "2026-10-31", subcategory="Interiors", funded_by_income_id=arrear["id"]
    )["expense"]
    return {"salary": salary, "arrear": arrear, "interiors": interiors}


def test_worked_example_income_totals(storage: Storage, november: dict) -> None:
    total = storage.summarize_income(start_date="2026-10-01", end_date="2026-10-31")["totals"][0]
    assert total["total_minor"] == 25000000
    assert total["total"] == "250000.00"
    assert total["regular_minor"] == 10000000
    assert total["one_off_minor"] == 15000000
    assert total["count"] == 2


def test_worked_example_linked_spending_and_remaining(storage: Storage, november: dict) -> None:
    arrear = storage.list_income(income_type="Arrear")["income"][0]
    assert (arrear["period_start"], arrear["period_end"]) == ("2026-04", "2026-10")
    assert arrear["linked_spent_minor"] == 3000000
    assert arrear["remaining"] == "120000.00"
    assert storage.list_income(income_type="Salary")["income"][0]["remaining"] == "100000.00"
    assert storage.list_expenses(funded_by_income_id=arrear["id"])["total"] == 1
    assert storage.summarize()["totals"][0]["total_minor"] == 3000000


def test_worked_example_cash_flow(storage: Storage, november: dict) -> None:
    storage.add_expense("500", None, "Food", "2026-09-15")
    flow = storage.cash_flow()
    assert [(m["month"], m["income_minor"], m["spent_minor"], m["net_minor"]) for m in flow["months"]] == [
        ("2026-09", 0, 50000, -50000),
        ("2026-10", 25000000, 3000000, 22000000),
    ]
    assert flow["months"][0]["savings_rate_percent"] is None
    assert flow["months"][0]["net"] == "-500.00"
    assert flow["months"][1]["savings_rate_percent"] == 88.0
    assert flow["months"][1]["regular_income_minor"] == 10000000
    assert flow["totals"][0]["net_minor"] == 21950000


def test_link_cannot_exceed_what_is_left_or_cross_currencies(storage: Storage, november: dict) -> None:
    arrear_id = november["arrear"]["id"]
    with pytest.raises(ExpenseError, match="120000.00 left"):
        storage.add_expense("120000.01", "A", "Home", "2026-10-31", funded_by_income_id=arrear_id)
    assert storage.add_expense("120000", "A", "Home", "2026-10-31", funded_by_income_id=arrear_id)["saved"] is True
    assert storage.list_income(income_type="Arrear")["income"][0]["remaining_minor"] == 0
    with pytest.raises(ExpenseError):
        storage.add_expense("1", "B", "Food", "2026-10-31", funded_by_income_id=arrear_id)
    with pytest.raises(ExpenseError, match="USD"):
        storage.add_expense(
            "1", "B", "Food", "2026-10-31", "USD", funded_by_income_id=november["salary"]["id"]
        )
    with pytest.raises(ExpenseError, match="No income"):
        storage.add_expense("1", "B", "Food", "2026-10-31", funded_by_income_id="missing")


def test_updating_a_linked_expense_rechecks_the_limit(storage: Storage, november: dict) -> None:
    expense_id = november["interiors"]["id"]
    with pytest.raises(ExpenseError, match="left"):
        storage.update_expense(expense_id, amount="150000.01")
    assert storage.update_expense(expense_id, amount="150000")["amount_minor"] == 15000000
    with pytest.raises(ExpenseError, match="100000.00 left"):
        storage.update_expense(expense_id, funded_by_income_id=november["salary"]["id"])
    storage.update_expense(expense_id, amount="50000")
    assert storage.update_expense(expense_id, funded_by_income_id=november["salary"]["id"])["funded_by_income_id"] == november["salary"]["id"]
    assert storage.list_income(income_type="Arrear")["income"][0]["remaining_minor"] == 15000000
    assert storage.update_expense(expense_id, funded_by_income_id="")["funded_by_income_id"] is None


def test_deleting_and_restoring_a_linked_expense_moves_remaining(storage: Storage, november: dict) -> None:
    arrear_id, expense_id = november["arrear"]["id"], november["interiors"]["id"]
    storage.delete_expense(expense_id)
    assert storage.list_income(income_type="Arrear")["income"][0]["remaining_minor"] == 15000000

    other = storage.add_expense("150000", "A", "Home", "2026-10-31", funded_by_income_id=arrear_id)
    with pytest.raises(ExpenseError, match="left"):
        storage.restore_expense(expense_id)
    storage.delete_expense(other["expense"]["id"])
    assert storage.restore_expense(expense_id)["deleted_at"] is None


def test_income_with_linked_expenses_is_protected(storage: Storage, november: dict) -> None:
    arrear_id = november["arrear"]["id"]
    with pytest.raises(ExpenseError, match="linked"):
        storage.delete_income(arrear_id)
    with pytest.raises(ExpenseError, match="linked"):
        storage.update_income(arrear_id, amount="29999")
    with pytest.raises(ExpenseError, match="linked"):
        storage.update_income(arrear_id, currency="USD", amount="50000")
    assert storage.update_income(arrear_id, amount="30000")["remaining_minor"] == 0

    storage.delete_expense(november["interiors"]["id"])
    assert storage.delete_income(arrear_id)["deleted_at"]
    with pytest.raises(ExpenseError, match="deleted"):
        storage.restore_expense(november["interiors"]["id"])
    assert storage.summarize_income()["totals"][0]["total_minor"] == 10000000
    assert storage.restore_income(arrear_id)["deleted_at"] is None
    assert storage.restore_expense(november["interiors"]["id"])["deleted_at"] is None


def test_income_duplicates_and_retries(storage: Storage) -> None:
    first = storage.add_income("1000", "Bonus", "2026-05-01", client_request_id="r1")
    assert storage.add_income("1000", "Bonus", "2026-05-01")["status"] == "possible_duplicate"
    assert storage.add_income("1000", "Bonus", "2026-05-01", client_request_id="r1")["income"]["id"] == first["income"]["id"]
    assert storage.add_income("1000", "Extra Income", "2026-05-01")["saved"] is True
    assert storage.add_income("1000", "Bonus", "2026-05-01", allow_duplicate=True)["saved"] is True
    assert storage.list_income()["total"] == 3


@pytest.mark.parametrize(
    "kwargs",
    [
        {"amount": "0", "income_type": "Salary"},
        {"amount": "5", "income_type": "Salary", "received_on": "2999-01-01"},
        {"amount": "5", "income_type": "Nope"},
        {"amount": "5", "income_type": "Arrear", "period_start": "2026-13"},
        {"amount": "5", "income_type": "Arrear", "period_start": "2026-10", "period_end": "2026-04"},
        {"amount": "5", "income_type": "Arrear", "period_end": "2026-04"},
    ],
)
def test_bad_income_is_rejected(storage: Storage, kwargs: dict) -> None:
    with pytest.raises(ExpenseError):
        storage.add_income(**kwargs)
    assert storage.list_income()["total"] == 0


def test_income_period_rules(storage: Storage) -> None:
    single = storage.add_income("5", "Bonus", "2026-05-01", period_start="2026-03")["income"]
    assert (single["period_start"], single["period_end"]) == ("2026-03", "2026-03")
    wider = storage.update_income(single["id"], period_end="2026-04")
    assert (wider["period_start"], wider["period_end"]) == ("2026-03", "2026-04")
    moved = storage.update_income(single["id"], period_start="2026-01")
    assert (moved["period_start"], moved["period_end"]) == ("2026-01", "2026-01")
    cleared = storage.update_income(single["id"], period_start="")
    assert (cleared["period_start"], cleared["period_end"]) == (None, None)


def test_income_update_changes_only_what_is_given(storage: Storage) -> None:
    income = storage.add_income("5", "Bonus", "2026-05-01", source="Demo", note="keep")["income"]
    updated = storage.update_income(income["id"], amount="7.50", income_type="Extra Income")
    assert (updated["amount_minor"], updated["income_type"]) == (750, "Extra Income")
    assert (updated["source"], updated["note"]) == ("Demo", "keep")
    assert storage.update_income(income["id"], source="")["source"] is None
    with pytest.raises(ExpenseError):
        storage.update_income(income["id"])
    with pytest.raises(ExpenseError):
        storage.update_income(income["id"], currency="USD")


def test_income_type_lifecycle_and_regular_flag(storage: Storage) -> None:
    assert {t["name"] for t in storage.list_income_types()} == {"Salary", "Arrear", "Bonus", "Extra Income"}
    storage.create_income_type("Rental Income", regular=True)
    with pytest.raises(ExpenseError):
        storage.create_income_type("rental income")
    storage.add_income("100", "Rental Income", "2026-05-01")
    storage.add_income("50", "Bonus", "2026-05-01")
    assert storage.summarize_income()["totals"][0]["regular_minor"] == 10000

    storage.set_income_type_regular("Rental Income", False)
    assert storage.summarize_income()["totals"][0]["regular_minor"] == 0

    storage.rename_income_type("Rental Income", "Rent Received")
    storage.set_income_type_archived("Rent Received", True)
    with pytest.raises(ExpenseError, match="archived"):
        storage.add_income("1", "Rent Received", "2026-05-02")
    storage.set_income_type_archived("Rent Received", False)

    assert storage.merge_income_type("Rent Received", "Extra Income")["entries_moved"] == 1
    assert storage.summarize_income()["totals"][0]["total_minor"] == 15000
    assert storage.list_income(income_type="Extra Income")["total"] == 1
    with pytest.raises(ExpenseError):
        storage.rename_income_type("Bonus", "Salary")
    with pytest.raises(ExpenseError):
        storage.merge_income_type("Bonus", "Bonus")


def test_income_summary_groups(storage: Storage, november: dict) -> None:
    storage.add_income("100", "Bonus", "2026-09-01")
    by_type = storage.summarize_income()["groups"]
    assert [(g["key"], g["total_minor"]) for g in by_type] == [("Arrear", 15000000), ("Salary", 10000000), ("Bonus", 10000)]
    by_month = storage.summarize_income(group_by="month")["groups"]
    assert [(g["key"], g["total_minor"]) for g in by_month] == [("2026-09", 10000), ("2026-10", 25000000)]
    by_source = storage.summarize_income(group_by="source")["groups"]
    assert [g["key"] for g in by_source] == ["Demo Employer", "(no source)"]


def test_currencies_stay_apart_in_income_and_cash_flow(storage: Storage) -> None:
    storage.add_income("100", "Salary", "2026-05-01")
    storage.add_income("10", "Extra Income", "2026-05-01", "USD")
    storage.add_expense("5", "A", "Food", "2026-05-01", "USD")
    assert [t["currency"] for t in storage.summarize_income()["totals"]] == ["INR", "USD"]
    assert [(t["currency"], t["net_minor"]) for t in storage.cash_flow()["totals"]] == [("INR", 10000), ("USD", 500)]


def test_income_export_is_private_and_exact(storage: Storage, november: dict) -> None:
    result = storage.export_income_csv()
    path = Path(result["path"])
    assert result["rows"] == 2
    assert path.name.startswith("income-")
    assert oct(path.stat().st_mode)[-3:] == "600"
    with open(path, newline="") as file:
        assert {r["amount"] for r in csv.DictReader(file)} == {"100000.00", "150000.00"}

"""Turns tool input into the exact values that get stored."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from expense_tracker.errors import ExpenseError

ISO_4217_CODES = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN
    BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS
    GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW
    KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD
    NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD
    SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VED
    VES VND VUV WST XAF XCD XOF XPF YER ZAR ZMW ZWG
    """.split()
)
ZERO_DECIMAL_CURRENCIES = frozenset(
    "BIF CLP DJF GNF ISK JPY KMF KRW PYG RWF UGX VND VUV XAF XOF XPF".split()
)
THREE_DECIMAL_CURRENCIES = frozenset("BHD IQD JOD KWD LYD OMR TND".split())
MAX_AMOUNT_MINOR = 10**15


def parse_currency(value: str) -> str:
    code = value.strip().upper()
    if code not in ISO_4217_CODES:
        raise ExpenseError(f"'{value}' isn't a currency code. Use one such as INR or USD.")
    return code


def _decimal_places(currency: str) -> int:
    if currency in ZERO_DECIMAL_CURRENCIES:
        return 0
    return 3 if currency in THREE_DECIMAL_CURRENCIES else 2


def parse_amount(value: str | int | float, currency: str) -> int:
    """Convert an amount such as "1,25,000.50" to whole minor units, without rounding."""
    if isinstance(value, bool):
        raise ExpenseError(f"'{value}' isn't an amount.")
    text = str(value).strip().replace(",", "")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ExpenseError(f"'{value}' isn't an amount. Use a number such as 450.50.") from None
    if not amount.is_finite():
        raise ExpenseError(f"'{value}' isn't an amount. Use a number such as 450.50.")
    if amount <= 0:
        raise ExpenseError("The amount must be greater than zero.")

    places = _decimal_places(currency)
    minor = amount.scaleb(places)
    if minor != minor.to_integral_value():
        raise ExpenseError(f"{currency} amounts can have at most {places} decimal places.")
    if minor > MAX_AMOUNT_MINOR:
        raise ExpenseError("That amount is too large.")
    return int(minor)


def format_amount(minor: int, currency: str) -> str:
    places = _decimal_places(currency)
    if places == 0:
        return str(minor)
    sign = "-" if minor < 0 else ""
    whole, fraction = divmod(abs(minor), 10**places)
    return f"{sign}{whole}.{fraction:0{places}d}"


def parse_date(value: str, field: str = "date") -> str:
    text = value.strip()
    # fromisoformat also accepts forms like 20261103, which wouldn't sort with stored dates.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ExpenseError(f"The {field} '{value}' must be in YYYY-MM-DD format.")
    try:
        date.fromisoformat(text)
    except ValueError:
        raise ExpenseError(f"The {field} '{value}' isn't a real date.") from None
    return text


def parse_expense_date(value: str | None, field: str = "expense date") -> str:
    if value is None:
        return date.today().isoformat()
    parsed = parse_date(value, field)
    if parsed > date.today().isoformat():
        raise ExpenseError(f"The {field} {parsed} is in the future.")
    return parsed


def parse_month(value: str) -> str:
    text = value.strip()
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", text):
        raise ExpenseError(f"The month '{value}' must be in YYYY-MM format.")
    return text


def parse_period(start: str | None, end: str | None) -> tuple[str | None, str | None]:
    """A period covers whole months; a single month can be given as just its start."""
    if not start and not end:
        return None, None
    if not start:
        raise ExpenseError("A period needs its first month.")
    first = parse_month(start)
    last = parse_month(end) if end else first
    if first > last:
        raise ExpenseError("The period's first month is after its last month.")
    return first, last


def parse_date_range(start: str | None, end: str | None) -> tuple[str | None, str | None]:
    parsed_start = parse_date(start, "start date") if start else None
    parsed_end = parse_date(end, "end date") if end else None
    if parsed_start and parsed_end and parsed_start > parsed_end:
        raise ExpenseError("The start date is after the end date.")
    return parsed_start, parsed_end


def clean_merchant(value: str | None) -> str | None:
    return " ".join(value.split()) or None if value else None


def clean_category_name(value: str) -> str:
    name = " ".join(value.split())
    if not name:
        raise ExpenseError("The category name can't be empty.")
    return name


def clean_note(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None

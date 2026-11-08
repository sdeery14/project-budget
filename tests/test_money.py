"""``Money`` is exact: integer minor units, one currency per operation (ADR-003)."""

from decimal import Decimal, localcontext

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from projectbudget.domain.money import (
    CurrencyMismatchError,
    InvalidCurrencyError,
    Money,
    MoneyParseError,
    minor_unit_digits,
    sum_money,
    validate_currency,
)

amounts = st.integers(min_value=-(10**15), max_value=10**15)
currencies = st.sampled_from(["USD", "EUR", "GBP", "JPY", "KWD"])


@given(amounts, amounts, currencies)
def test_addition_is_commutative_and_exact(a: int, b: int, currency: str) -> None:
    left = Money(a, currency) + Money(b, currency)
    right = Money(b, currency) + Money(a, currency)
    assert left == right == Money(a + b, currency)


@given(amounts, amounts, amounts, currencies)
def test_addition_is_associative(a: int, b: int, c: int, currency: str) -> None:
    x, y, z = Money(a, currency), Money(b, currency), Money(c, currency)
    assert (x + y) + z == x + (y + z)


@given(amounts, amounts, currencies)
def test_subtraction_undoes_addition(a: int, b: int, currency: str) -> None:
    assert Money(a, currency) + Money(b, currency) - Money(b, currency) == Money(a, currency)


@given(amounts, st.integers(min_value=-1000, max_value=1000), currencies)
def test_scaling_by_an_integer_is_repeated_addition(a: int, n: int, currency: str) -> None:
    money = Money(a, currency)
    assert money * n == n * money == Money(a * n, currency)
    assert -money == money * -1


@settings(max_examples=300)
@given(amounts, currencies)
def test_text_round_trip_is_lossless(a: int, currency: str) -> None:
    """The decimal string is the only text form; parsing it gives back the same minor units."""
    money = Money(a, currency)
    assert Money.parse(str(money), currency) == money
    assert Decimal(str(money)).scaleb(minor_unit_digits(currency)) == a


@given(st.lists(amounts), currencies)
def test_sum_matches_integer_sum(values: list[int], currency: str) -> None:
    total = sum_money([Money(v, currency) for v in values], currency)
    assert total == Money(sum(values), currency)


@pytest.mark.parametrize(
    ("text", "currency", "amount"),
    [
        ("12.34", "USD", 1234),
        ("12.3", "USD", 1230),
        ("12", "USD", 1200),
        ("0.05", "USD", 5),
        ("-0.05", "USD", -5),
        (" 7 ", "USD", 700),
        ("1200", "JPY", 1200),
        ("1.234", "KWD", 1234),
        ("1e2", "USD", 10000),
    ],
)
def test_parse_decimal_strings(text: str, currency: str, amount: int) -> None:
    assert Money.parse(text, currency) == Money(amount, currency)


@pytest.mark.parametrize(
    ("text", "currency"),
    [
        ("12.345", "USD"),  # more fractional digits than cents
        ("1.5", "JPY"),  # yen has no minor unit
        ("abc", "USD"),
        ("", "USD"),
        ("NaN", "USD"),
        ("Infinity", "USD"),
        ("$12.34", "USD"),
        ("12,34", "USD"),
    ],
)
def test_parse_rejects_inexact_or_malformed_text(text: str, currency: str) -> None:
    with pytest.raises(MoneyParseError):
        Money.parse(text, currency)


def test_parse_does_not_depend_on_the_decimal_context() -> None:
    """Parsing reads the literal's digits; the caller's Decimal precision never rounds them."""
    with localcontext() as ctx:
        ctx.prec = 6
        assert Money.parse("12345.67", "USD") == Money(1234567, "USD")
        assert Money.parse("-12345.67", "USD") == Money(-1234567, "USD")
        assert Money.parse("123456789.123", "KWD") == Money(123456789123, "KWD")
    with localcontext() as ctx:
        ctx.prec = 2
        with pytest.raises(MoneyParseError):
            Money.parse("12345.678", "USD")


@pytest.mark.parametrize(
    "text",
    [
        "12.34000000000000000000000000001",  # a 1 beyond the default 28-digit precision
        "0." + "0" * 40 + "1",
        "1E-30",
    ],
)
def test_parse_rejects_tiny_remainders_beyond_default_precision(text: str) -> None:
    with pytest.raises(MoneyParseError):
        Money.parse(text, "USD")


def test_parse_accepts_trailing_zeros_of_any_length() -> None:
    assert Money.parse("12.34" + "0" * 40, "USD") == Money(1234, "USD")
    assert Money.parse("5" + "0" * 30 + "E-30", "JPY") == Money(5, "JPY")


@pytest.mark.parametrize("value", [12.34, 1234, Decimal("12.34"), None])
def test_parse_only_accepts_strings(value: object) -> None:
    """A float or a YAML number at a text boundary is a bug, not an amount."""
    with pytest.raises(MoneyParseError):
        Money.parse(value, "USD")


@pytest.mark.parametrize("value", [12.34, Decimal(5), "5", True])
def test_amount_must_be_an_int(value: object) -> None:
    with pytest.raises(TypeError):
        Money(value, "USD")  # type: ignore[arg-type]


def test_str_formats_with_the_currency_minor_digits() -> None:
    assert str(Money(1234, "USD")) == "12.34"
    assert str(Money(5, "USD")) == "0.05"
    assert str(Money(-5, "USD")) == "-0.05"
    assert str(Money(1200, "JPY")) == "1200"
    assert str(Money(1234, "KWD")) == "1.234"
    assert str(Money.zero("USD")) == "0.00"


def test_currency_is_normalised_and_validated() -> None:
    assert Money(1, "usd").currency == "USD"
    assert validate_currency(" eur ") == "EUR"
    for bad in ("", "US", "USDD", "U$D", "12A", "ÉUR"):
        with pytest.raises(InvalidCurrencyError):
            Money(1, bad)


def test_mixed_currencies_are_rejected() -> None:
    usd, eur = Money(100, "USD"), Money(100, "EUR")
    with pytest.raises(CurrencyMismatchError):
        _ = usd + eur
    with pytest.raises(CurrencyMismatchError):
        _ = usd - eur
    with pytest.raises(CurrencyMismatchError):
        _ = usd < eur
    with pytest.raises(CurrencyMismatchError):
        sum_money([usd, eur], "USD")
    assert usd != eur  # equality never raises; it is simply false


def test_ordering_within_a_currency() -> None:
    assert Money(1, "USD") < Money(2, "USD") <= Money(2, "USD")
    assert Money(3, "USD") > Money(2, "USD") >= Money(2, "USD")
    assert Money(-1, "USD").is_negative
    assert not Money.zero("USD").is_negative


def test_multiplying_by_a_non_integer_is_a_type_error() -> None:
    with pytest.raises(TypeError):
        _ = Money(100, "USD") * 1.5  # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = Money(100, "USD") * True  # bool is an int subclass, still refused

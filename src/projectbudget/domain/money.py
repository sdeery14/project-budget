"""Money: an integer amount in minor units plus an ISO 4217 code (ADR-003).

Floats never touch money. At text boundaries an amount is a decimal string
("12.34") parsed with ``Decimal`` and rejected when it carries more fractional
digits than the currency allows. Arithmetic between different currencies is an
error, never a conversion.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

CURRENCY_CODE_LENGTH = 3

# Minor-unit digits for currencies that are not two-decimal (ISO 4217).
_MINOR_UNIT_DIGITS: dict[str, int] = {
    "BIF": 0,
    "CLP": 0,
    "DJF": 0,
    "GNF": 0,
    "ISK": 0,
    "JPY": 0,
    "KMF": 0,
    "KRW": 0,
    "PYG": 0,
    "RWF": 0,
    "UGX": 0,
    "UYI": 0,
    "VND": 0,
    "VUV": 0,
    "XAF": 0,
    "XOF": 0,
    "XPF": 0,
    "BHD": 3,
    "IQD": 3,
    "JOD": 3,
    "KWD": 3,
    "LYD": 3,
    "OMR": 3,
    "TND": 3,
}
DEFAULT_MINOR_UNIT_DIGITS = 2


class InvalidCurrencyError(ValueError):
    """Not three ASCII letters."""


class CurrencyMismatchError(ValueError):
    """Two amounts in different currencies met in one operation."""

    def __init__(self, left: str, right: str) -> None:
        super().__init__(f"Currency mismatch: {left} vs {right}")
        self.left = left
        self.right = right


class MoneyParseError(ValueError):
    """The text is not a decimal amount this currency can represent exactly."""


def validate_currency(code: str) -> str:
    """Return the code upper-cased, or raise. Accepts any three ASCII letters."""
    clean = code.strip().upper()
    if len(clean) != CURRENCY_CODE_LENGTH or not clean.isascii() or not clean.isalpha():
        msg = f"Currency must be a three-letter ISO 4217 code, got {code!r}"
        raise InvalidCurrencyError(msg)
    return clean


def minor_unit_digits(currency: str) -> int:
    return _MINOR_UNIT_DIGITS.get(validate_currency(currency), DEFAULT_MINOR_UNIT_DIGITS)


@dataclass(frozen=True, slots=True)
class Money:
    """An exact amount: ``amount`` is in minor units (cents for USD)."""

    amount: int
    currency: str

    def __post_init__(self) -> None:
        # bool is an int subclass; a float or Decimal is never accepted silently.
        if type(self.amount) is not int:
            msg = f"Money.amount must be an int in minor units, got {type(self.amount).__name__}"
            raise TypeError(msg)
        object.__setattr__(self, "currency", validate_currency(self.currency))

    @classmethod
    def zero(cls, currency: str) -> "Money":
        return cls(0, currency)

    @classmethod
    def parse(cls, text: object, currency: str) -> "Money":
        """Parse a decimal string such as ``"12.34"`` into minor units, exactly or not at all.

        Only ``str`` is accepted: a float or a YAML number at a text boundary is a
        bug, not an amount (ADR-003).
        """
        if not isinstance(text, str):
            msg = f"Amounts are decimal strings, not {type(text).__name__}"
            raise MoneyParseError(msg)
        digits = minor_unit_digits(currency)
        try:
            value = Decimal(text.strip())
        except InvalidOperation as exc:
            raise MoneyParseError(f"Not a decimal amount: {text!r}") from exc
        if not value.is_finite():
            raise MoneyParseError(f"Not a finite amount: {text!r}")
        # Work on the exact digits of the literal, not through Decimal arithmetic: scaleb()
        # and to_integral_value() round to the active context's precision, which would make
        # the result depend on caller state and let "12.340...01" pass as 12.34.
        sign, mantissa_digits, exponent = value.as_tuple()
        if not isinstance(exponent, int):  # NaN and infinity carry a letter; excluded above
            raise MoneyParseError(f"Not a finite amount: {text!r}")
        mantissa = int("".join(map(str, mantissa_digits)) or "0")
        shift = exponent + digits
        if shift >= 0:
            amount = mantissa * 10**shift
        else:
            amount, remainder = divmod(mantissa, 10**-shift)
            if remainder:
                msg = (
                    f"{text!r} has more than {digits} decimal places, which {currency} cannot hold"
                )
                raise MoneyParseError(msg)
        return cls(-amount if sign else amount, currency)

    @property
    def minor_unit_digits(self) -> int:
        return minor_unit_digits(self.currency)

    def __str__(self) -> str:
        """Decimal string with exactly the currency's minor digits: ``"12.34"``, ``"-0.05"``."""
        digits = self.minor_unit_digits
        sign = "-" if self.amount < 0 else ""
        magnitude = abs(self.amount)
        if digits == 0:
            return f"{sign}{magnitude}"
        whole, fraction = divmod(magnitude, 10**digits)
        return f"{sign}{whole}.{fraction:0{digits}d}"

    def _same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(self.currency, other.currency)

    def __add__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.amount, self.currency)

    def __mul__(self, factor: int) -> "Money":
        if type(factor) is not int:
            return NotImplemented
        return Money(self.amount * factor, self.currency)

    __rmul__ = __mul__

    def __lt__(self, other: "Money") -> bool:
        self._same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        self._same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: "Money") -> bool:
        self._same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: "Money") -> bool:
        self._same_currency(other)
        return self.amount >= other.amount

    @property
    def is_negative(self) -> bool:
        return self.amount < 0


def sum_money(amounts: Iterable[Money], currency: str) -> Money:
    """Total of amounts that must all be in ``currency``; the empty total is zero."""
    total = Money.zero(currency)
    for amount in amounts:
        total = total + amount
    return total

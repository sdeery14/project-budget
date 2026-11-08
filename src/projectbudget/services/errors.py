"""The errors a caller can cause, grouped so routers map them uniformly.

Routers never contain business logic (RFC v3 §3); they only need to know
which exceptions mean "your input was wrong" (422) and which mean "that
conflicts with what exists" (409). Everything else is a bug and stays a 500.
"""

from projectbudget.domain.catalog import (
    InvalidNameError,
    InvalidPriorityError,
    InvalidQuantityError,
    InvalidSlugError,
    InvalidUnitError,
)
from projectbudget.domain.money import CurrencyMismatchError, InvalidCurrencyError, MoneyParseError
from projectbudget.services.backlog import DuplicateOrderError
from projectbudget.services.budget import InvalidOverrideError, InvalidPlanError
from projectbudget.services.catalog import NegativeEstimateError, SlugTakenError, UnitInUseError
from projectbudget.services.inventory import (
    InsufficientOnHandError,
    InvalidMovementError,
    OverConsumptionError,
    RequirementNotActiveError,
    SharedReusableConsumptionError,
)
from projectbudget.services.purchases import InvalidPurchaseError
from projectbudget.services.requirements import (
    InvalidArchiveReasonError,
    ItemReferenceError,
    UnitMismatchError,
)

VALIDATION_ERRORS: tuple[type[Exception], ...] = (
    InvalidNameError,
    InvalidPriorityError,
    InvalidQuantityError,
    InvalidSlugError,
    InvalidUnitError,
    InvalidCurrencyError,
    CurrencyMismatchError,
    MoneyParseError,
    NegativeEstimateError,
    InvalidArchiveReasonError,
    ItemReferenceError,
    UnitMismatchError,
    InvalidMovementError,
    DuplicateOrderError,
    InvalidPlanError,
    InvalidOverrideError,
    InvalidPurchaseError,
)
"""Bad input: answer 422."""

CONFLICT_ERRORS: tuple[type[Exception], ...] = (
    SlugTakenError,
    UnitInUseError,
    SharedReusableConsumptionError,
    InsufficientOnHandError,
    OverConsumptionError,
    RequirementNotActiveError,
)
"""Valid input that the current state forbids: answer 409."""

USER_ERRORS: tuple[type[Exception], ...] = VALIDATION_ERRORS + CONFLICT_ERRORS
"""Everything a form can show back to the user instead of failing."""

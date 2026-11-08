"""Manifest v1 as the domain sees it (RFC v3 §6, ADR-007). Pure data, no parsing.

The adapters turn YAML or CSV text into these records and report schema errors
with line numbers; the sync engine reads them. Identity is the item id, never
the name. Money arrives as a decimal string and is only turned into ``Money``
once the workspace currency is known.
"""

from dataclasses import dataclass

from projectbudget.domain.catalog import ItemKind, Sharing
from projectbudget.domain.money import CurrencyMismatchError, Money

MANIFEST_VERSION = 1
AMOUNT_PATTERN = r"^\d+(\.\d{1,2})?$"
DEFAULT_MANIFEST_PATH = "project-budget.yaml"


@dataclass(frozen=True, slots=True)
class ManifestIssue:
    """One schema problem, with the line it was found on when the format has lines."""

    path: str
    message: str
    line: int | None = None

    def __str__(self) -> str:
        where = f"line {self.line}, {self.path}" if self.line is not None else self.path
        return f"{where}: {self.message}"


class ManifestError(ValueError):
    """A schema error: the whole file is rejected and nothing is applied."""

    def __init__(self, issues: tuple[ManifestIssue, ...]) -> None:
        super().__init__("; ".join(str(issue) for issue in issues) or "Invalid manifest")
        self.issues = issues


@dataclass(frozen=True, slots=True)
class ManifestEstimate:
    """``amount`` is a decimal string (or ``amount_minor`` an integer); the currency is optional."""

    amount: str | None = None
    amount_minor: int | None = None
    currency: str | None = None

    def to_money(self, workspace_currency: str) -> Money:
        """Resolve against the workspace currency; a different currency is a mismatch, not a conversion."""
        currency = (self.currency or workspace_currency).upper()
        if currency != workspace_currency:
            raise CurrencyMismatchError(workspace_currency, currency)
        if self.amount_minor is not None:
            return Money(self.amount_minor, currency)
        return Money.parse(self.amount or "", currency)


@dataclass(frozen=True, slots=True)
class ManifestItem:
    id: str
    name: str
    qty: int
    unit: str
    kind: ItemKind
    priority: int
    sharing: Sharing
    estimate: ManifestEstimate | None
    catalog: str | None
    notes: str


@dataclass(frozen=True, slots=True)
class Manifest:
    version: int
    project_id: str
    project_name: str
    items: tuple[ManifestItem, ...]

    def by_id(self) -> dict[str, ManifestItem]:
        return {item.id: item for item in self.items}

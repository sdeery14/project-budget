"""The canonical export document (ADR-008): one versioned JSON file for the whole workspace.

Pure data: pydantic models that validate a document and nothing else. Money is an
integer in minor units (``*_minor`` fields) in the document's single currency;
dates are ISO strings; every id is preserved so an import reproduces the
workspace exactly. Unknown fields are rejected so a typo cannot silently drop data.
"""

import json
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from projectbudget.domain.catalog import (
    Confidence,
    EstimateOrigin,
    Intent,
    ItemKind,
    MovementReason,
    PurchaseStatus,
    ReviewState,
    Sharing,
)

FORMAT = "projectbudget-workspace"
VERSION = 1


class InvalidDocumentError(ValueError):
    """Not a ProjectBudget export, the wrong version, or a field that does not validate."""


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class ProjectRecord(Record):
    id: str
    name: str
    archived: bool
    notes: str
    created_at: datetime
    updated_at: datetime


class CatalogItemRecord(Record):
    id: str
    slug: str
    name: str
    kind: ItemKind
    unit: str
    review_state: ReviewState
    merged_into_id: str | None
    created_at: datetime
    updated_at: datetime


class RequirementRecord(Record):
    id: str
    project_id: str
    catalog_item_id: str
    qty: int
    priority: int
    sort_order: int
    intent: Intent
    archive_reason: str | None
    sharing: Sharing
    notes: str
    created_at: datetime
    updated_at: datetime


class PriceEstimateRecord(Record):
    id: str
    catalog_item_id: str
    amount_minor: int
    as_of: date
    confidence: Confidence
    origin: EstimateOrigin
    created_at: datetime


class InventoryMovementRecord(Record):
    id: str
    catalog_item_id: str
    delta_qty: int
    reason: MovementReason
    requirement_id: str | None
    purchase_line_id: str | None
    occurred_on: date
    note: str
    created_at: datetime


class PurchaseLineRecord(Record):
    id: str
    catalog_item_id: str
    position: int
    qty: int
    unit_price_minor: int
    received_qty: int


class PurchaseRecord(Record):
    id: str
    vendor: str
    ordered_on: date
    status: PurchaseStatus
    shipping_minor: int
    tax_minor: int
    notes: str
    lines: list[PurchaseLineRecord]
    created_at: datetime
    updated_at: datetime


class MonthOverrideRecord(Record):
    id: str
    month: date
    delta_minor: int
    label: str
    created_at: datetime


class BudgetPlanRecord(Record):
    earmarked_funds_minor: int
    balance_as_of: date
    protected_reserve_minor: int
    monthly_contribution_minor: int
    horizon_months: int
    contingency_pct: int
    updated_at: datetime
    overrides: list[MonthOverrideRecord]


class WorkspaceDocument(Record):
    """The whole workspace. Collections are sorted by id so two exports of one state are equal."""

    format: str = Field(default=FORMAT)
    version: int = Field(default=VERSION)
    exported_at: datetime
    currency: str
    projects: list[ProjectRecord]
    catalog_items: list[CatalogItemRecord]
    requirements: list[RequirementRecord]
    price_estimates: list[PriceEstimateRecord]
    inventory_movements: list[InventoryMovementRecord]
    purchases: list[PurchaseRecord]
    backlog_order: list[str]
    budget_plan: BudgetPlanRecord | None

    def to_json(self) -> str:
        """Stable text: sorted keys, two-space indent, trailing newline."""
        return json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"

    def content_without_timestamp(self) -> dict[str, Any]:
        """Everything but ``exported_at``: what two exports of the same workspace share."""
        data = self.model_dump(mode="json")
        data.pop("exported_at")
        return data


def parse_document(data: bytes | str | dict[str, Any]) -> WorkspaceDocument:
    """Validate raw JSON (or an already-decoded object) into a document, or raise."""
    try:
        raw: Any = json.loads(data) if isinstance(data, bytes | str) else data
    except json.JSONDecodeError as exc:
        raise InvalidDocumentError(f"Not JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise InvalidDocumentError("An export is a JSON object")
    if raw.get("format") != FORMAT:
        raise InvalidDocumentError(f"Not a ProjectBudget export (format {raw.get('format')!r})")
    if raw.get("version") != VERSION:
        raise InvalidDocumentError(
            f"Export version {raw.get('version')!r} is not supported; this build reads version {VERSION}"
        )
    try:
        return WorkspaceDocument.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise InvalidDocumentError(f"Invalid export at {where}: {first['msg']}") from exc

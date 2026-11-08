"""Export and import of the whole workspace (ADR-008).

Export reads every workspace table into the canonical document; import writes
one back, id for id, into an empty workspace (or over the current one when the
caller says so). Owner, sessions and API tokens are not workspace data and are
never part of the document.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from projectbudget.adapters import export_format as fmt
from projectbudget.domain.money import validate_currency
from projectbudget.domain.principal import Principal
from projectbudget.persistence.models import (
    BacklogOrder,
    BudgetPlan,
    CatalogItem,
    InventoryMovement,
    MonthOverride,
    PriceEstimate,
    Project,
    Purchase,
    PurchaseLine,
    Requirement,
    WorkspaceSettings,
)
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import workspace
from projectbudget.services.budget import PLAN_ID
from projectbudget.services.clock import utcnow
from projectbudget.services.guards import require_write

WORKSPACE_TABLES = (
    MonthOverride,
    BudgetPlan,
    BacklogOrder,
    InventoryMovement,
    PurchaseLine,
    Purchase,
    PriceEstimate,
    Requirement,
    CatalogItem,
    Project,
)
"""Every workspace table, children before parents."""


class WorkspaceNotEmptyError(ValueError):
    """Import into a workspace that already has data needs an explicit ``replace``."""

    def __init__(self) -> None:
        super().__init__("The workspace already has data; pass replace=True to overwrite it")


class ImportConflictError(ValueError):
    """The document references an id twice, or one that is not in the document."""


@dataclass(frozen=True, slots=True)
class ImportSummary:
    projects: int
    catalog_items: int
    requirements: int
    price_estimates: int
    inventory_movements: int
    purchases: int
    purchase_lines: int
    budget_plan: bool
    replaced: bool


def export_workspace(
    session_factory: SessionFactory, principal: Principal, *, now: datetime | None = None
) -> fmt.WorkspaceDocument:
    """Read the whole workspace into the canonical document, every collection sorted by id."""
    currency = workspace.get_currency(session_factory)
    with session_factory() as db:
        projects = db.scalars(select(Project).order_by(Project.id)).all()
        items = db.scalars(select(CatalogItem).order_by(CatalogItem.id)).all()
        requirements = db.scalars(select(Requirement).order_by(Requirement.id)).all()
        estimates = db.scalars(select(PriceEstimate).order_by(PriceEstimate.id)).all()
        movements = db.scalars(select(InventoryMovement).order_by(InventoryMovement.id)).all()
        purchases = db.scalars(select(Purchase).order_by(Purchase.id)).all()
        lines = db.scalars(
            select(PurchaseLine).order_by(PurchaseLine.position, PurchaseLine.id)
        ).all()
        order = db.scalars(
            select(BacklogOrder.catalog_item_id).order_by(BacklogOrder.position)
        ).all()
        plan = db.get(BudgetPlan, PLAN_ID)
        overrides = db.scalars(select(MonthOverride).order_by(MonthOverride.id)).all()

    lines_by_purchase: dict[str, list[fmt.PurchaseLineRecord]] = {}
    for line in lines:
        lines_by_purchase.setdefault(line.purchase_id, []).append(
            fmt.PurchaseLineRecord(
                id=line.id,
                catalog_item_id=line.catalog_item_id,
                position=line.position,
                qty=line.qty,
                unit_price_minor=line.unit_price,
                received_qty=line.received_qty,
            )
        )
    return fmt.WorkspaceDocument(
        exported_at=now or utcnow(),
        currency=currency,
        projects=[
            fmt.ProjectRecord(
                id=p.id,
                name=p.name,
                archived=p.archived,
                notes=p.notes,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
            for p in projects
        ],
        catalog_items=[
            fmt.CatalogItemRecord(
                id=i.id,
                slug=i.slug,
                name=i.name,
                kind=i.kind,  # type: ignore[arg-type]
                unit=i.unit,
                review_state=i.review_state,  # type: ignore[arg-type]
                merged_into_id=i.merged_into_id,
                created_at=i.created_at,
                updated_at=i.updated_at,
            )
            for i in items
        ],
        requirements=[
            fmt.RequirementRecord(
                id=r.id,
                project_id=r.project_id,
                catalog_item_id=r.catalog_item_id,
                qty=r.qty,
                priority=r.priority,
                sort_order=r.sort_order,
                intent=r.intent,  # type: ignore[arg-type]
                archive_reason=r.archive_reason,
                sharing=r.sharing,  # type: ignore[arg-type]
                notes=r.notes,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in requirements
        ],
        price_estimates=[
            fmt.PriceEstimateRecord(
                id=e.id,
                catalog_item_id=e.catalog_item_id,
                amount_minor=e.amount,
                as_of=e.as_of,
                confidence=e.confidence,  # type: ignore[arg-type]
                origin=e.origin,  # type: ignore[arg-type]
                created_at=e.created_at,
            )
            for e in estimates
        ],
        inventory_movements=[
            fmt.InventoryMovementRecord(
                id=m.id,
                catalog_item_id=m.catalog_item_id,
                delta_qty=m.delta_qty,
                reason=m.reason,  # type: ignore[arg-type]
                requirement_id=m.requirement_id,
                purchase_line_id=m.purchase_line_id,
                occurred_on=m.occurred_on,
                note=m.note,
                created_at=m.created_at,
            )
            for m in movements
        ],
        purchases=[
            fmt.PurchaseRecord(
                id=p.id,
                vendor=p.vendor,
                ordered_on=p.ordered_on,
                status=p.status,  # type: ignore[arg-type]
                shipping_minor=p.shipping,
                tax_minor=p.tax,
                notes=p.notes,
                lines=lines_by_purchase.get(p.id, []),
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
            for p in purchases
        ],
        backlog_order=list(order),
        budget_plan=fmt.BudgetPlanRecord(
            earmarked_funds_minor=plan.earmarked_funds,
            balance_as_of=plan.balance_as_of,
            protected_reserve_minor=plan.protected_reserve,
            monthly_contribution_minor=plan.monthly_contribution,
            horizon_months=plan.horizon_months,
            contingency_pct=plan.contingency_pct,
            updated_at=plan.updated_at,
            overrides=[
                fmt.MonthOverrideRecord(
                    id=o.id,
                    month=o.month,
                    delta_minor=o.delta,
                    label=o.label,
                    created_at=o.created_at,
                )
                for o in overrides
            ],
        )
        if plan is not None
        else None,
    )


def _is_empty(db: Session) -> bool:
    for table in (Project, CatalogItem, Purchase, InventoryMovement, BudgetPlan):
        if db.scalar(select(func.count()).select_from(table)):
            return False
    return True


def _wipe(db: Session) -> None:
    """Delete every workspace row, children first; the settings row and the owner stay."""
    db.execute(
        update(CatalogItem).values(merged_into_id=None).execution_options(synchronize_session=False)
    )
    for table in WORKSPACE_TABLES:
        db.execute(delete(table))


def wipe_workspace(session_factory: SessionFactory, principal: Principal) -> None:
    """Remove every project, item, requirement, estimate, movement, purchase and plan."""
    require_write(principal)
    with session_factory.begin() as db:
        _wipe(db)


def import_workspace(
    session_factory: SessionFactory,
    principal: Principal,
    document: fmt.WorkspaceDocument,
    *,
    replace: bool = False,
    now: datetime | None = None,
) -> ImportSummary:
    """Write a document into the workspace, preserving every id, in one transaction."""
    require_write(principal)
    currency = validate_currency(document.currency)
    moment = now or utcnow()
    try:
        with session_factory.begin() as db:
            empty = _is_empty(db)
            if not empty:
                if not replace:
                    raise WorkspaceNotEmptyError
                _wipe(db)
            settings = db.get(WorkspaceSettings, workspace.WORKSPACE_ID)
            if settings is None:
                db.add(
                    WorkspaceSettings(
                        id=workspace.WORKSPACE_ID, currency=currency, updated_at=moment
                    )
                )
            else:
                settings.currency = currency
                settings.updated_at = moment
            _insert_all(db, document, currency)
            line_count = sum(len(p.lines) for p in document.purchases)
            return ImportSummary(
                projects=len(document.projects),
                catalog_items=len(document.catalog_items),
                requirements=len(document.requirements),
                price_estimates=len(document.price_estimates),
                inventory_movements=len(document.inventory_movements),
                purchases=len(document.purchases),
                purchase_lines=line_count,
                budget_plan=document.budget_plan is not None,
                replaced=not empty,
            )
    except IntegrityError as exc:
        raise ImportConflictError(
            "The export references an id twice or points at a record it does not contain"
        ) from exc


def _insert_all(db: Session, doc: fmt.WorkspaceDocument, currency: str) -> None:
    db.add_all(
        Project(
            id=p.id,
            name=p.name,
            archived=p.archived,
            notes=p.notes,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p in doc.projects
    )
    # Items first without redirects (a target may come later in the list), then the redirects.
    db.add_all(
        CatalogItem(
            id=i.id,
            slug=i.slug,
            name=i.name,
            kind=i.kind.value,
            unit=i.unit,
            review_state=i.review_state.value,
            merged_into_id=None,
            created_at=i.created_at,
            updated_at=i.updated_at,
        )
        for i in doc.catalog_items
    )
    db.flush()
    for i in doc.catalog_items:
        if i.merged_into_id is not None:
            db.execute(
                update(CatalogItem)
                .where(CatalogItem.id == i.id)
                .values(merged_into_id=i.merged_into_id)
                .execution_options(synchronize_session=False)
            )
    db.add_all(
        Requirement(
            id=r.id,
            project_id=r.project_id,
            catalog_item_id=r.catalog_item_id,
            qty=r.qty,
            priority=r.priority,
            sort_order=r.sort_order,
            intent=r.intent.value,
            archive_reason=r.archive_reason,
            sharing=r.sharing.value,
            notes=r.notes,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in doc.requirements
    )
    db.add_all(
        PriceEstimate(
            id=e.id,
            catalog_item_id=e.catalog_item_id,
            amount=e.amount_minor,
            currency=currency,
            as_of=e.as_of,
            confidence=e.confidence.value,
            origin=e.origin.value,
            created_at=e.created_at,
        )
        for e in doc.price_estimates
    )
    db.flush()
    for p in doc.purchases:
        db.add(
            Purchase(
                id=p.id,
                vendor=p.vendor,
                ordered_on=p.ordered_on,
                status=p.status.value,
                currency=currency,
                shipping=p.shipping_minor,
                tax=p.tax_minor,
                notes=p.notes,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
        )
        db.add_all(
            PurchaseLine(
                id=line.id,
                purchase_id=p.id,
                catalog_item_id=line.catalog_item_id,
                position=line.position,
                qty=line.qty,
                unit_price=line.unit_price_minor,
                received_qty=line.received_qty,
            )
            for line in p.lines
        )
    db.flush()
    db.add_all(
        InventoryMovement(
            id=m.id,
            catalog_item_id=m.catalog_item_id,
            delta_qty=m.delta_qty,
            reason=m.reason.value,
            requirement_id=m.requirement_id,
            purchase_line_id=m.purchase_line_id,
            occurred_on=m.occurred_on,
            note=m.note,
            created_at=m.created_at,
        )
        for m in doc.inventory_movements
    )
    db.add_all(
        BacklogOrder(catalog_item_id=item_id, position=position)
        for position, item_id in enumerate(doc.backlog_order)
    )
    if doc.budget_plan is not None:
        plan = doc.budget_plan
        db.add(
            BudgetPlan(
                id=PLAN_ID,
                currency=currency,
                earmarked_funds=plan.earmarked_funds_minor,
                balance_as_of=plan.balance_as_of,
                protected_reserve=plan.protected_reserve_minor,
                monthly_contribution=plan.monthly_contribution_minor,
                horizon_months=plan.horizon_months,
                contingency_pct=plan.contingency_pct,
                updated_at=plan.updated_at,
            )
        )
        db.add_all(
            MonthOverride(
                id=o.id,
                plan_id=PLAN_ID,
                month=o.month,
                delta=o.delta_minor,
                label=o.label,
                created_at=o.created_at,
            )
            for o in plan.overrides
        )
    db.flush()

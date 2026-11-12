"""``/api/v1/catalog/items``: items, their price estimates and stock movements."""

from fastapi import APIRouter, Depends, Request

from projectbudget.api.schemas import (
    AdjustmentIn,
    CatalogItemIn,
    CatalogItemOut,
    CatalogItemPatch,
    EstimateIn,
    EstimateOut,
    MergeIn,
    MovementOut,
)
from projectbudget.domain.catalog import ReviewState
from projectbudget.domain.money import Money
from projectbudget.services import catalog, inventory, workspace
from projectbudget.web.auth import Owner, require_owner, session_factory

router = APIRouter(prefix="/catalog/items", tags=["catalog"], dependencies=[Depends(require_owner)])


@router.get("")
def list_items(
    request: Request,
    principal: Owner,
    include_merged: bool = False,
    review_state: ReviewState | None = None,
) -> list[CatalogItemOut]:
    """Every live item; ``review_state=unreviewed`` is the catalog page's filter (#36)."""
    rows = catalog.list_items(
        session_factory(request),
        principal,
        include_merged=include_merged,
        review_state=review_state,
    )
    return [CatalogItemOut.of(i) for i in rows]


@router.post("/{item_id}/merge")
def merge_item(request: Request, principal: Owner, item_id: str, body: MergeIn) -> CatalogItemOut:
    """Fold this item into ``target_id``: every reference moves and this id becomes a redirect."""
    info = catalog.merge_items(session_factory(request), principal, item_id, body.target_id)
    return CatalogItemOut.of(info)


@router.post("", status_code=201)
def create_item(request: Request, principal: Owner, body: CatalogItemIn) -> CatalogItemOut:
    info = catalog.create_item(
        session_factory(request),
        principal,
        catalog.CatalogItemCreate(
            name=body.name,
            kind=body.kind,
            unit=body.unit,
            slug=body.slug,
            review_state=body.review_state,
        ),
    )
    return CatalogItemOut.of(info)


@router.get("/{item_id}")
def get_item(request: Request, principal: Owner, item_id: str) -> CatalogItemOut:
    return CatalogItemOut.of(catalog.get_item(session_factory(request), principal, item_id))


@router.patch("/{item_id}")
def update_item(
    request: Request, principal: Owner, item_id: str, body: CatalogItemPatch
) -> CatalogItemOut:
    info = catalog.update_item(
        session_factory(request),
        principal,
        item_id,
        catalog.CatalogItemUpdate(
            name=body.name, kind=body.kind, unit=body.unit, review_state=body.review_state
        ),
    )
    return CatalogItemOut.of(info)


@router.get("/{item_id}/estimates")
def list_estimates(request: Request, principal: Owner, item_id: str) -> list[EstimateOut]:
    rows = catalog.list_estimates(session_factory(request), principal, item_id)
    return [EstimateOut.of(row) for row in rows]


@router.get("/{item_id}/estimates/current")
def current_estimate(request: Request, principal: Owner, item_id: str) -> EstimateOut | None:
    """The estimate in force, or ``null`` when the item is unpriced (never zero)."""
    info = catalog.current_estimate(session_factory(request), principal, item_id)
    return EstimateOut.of(info) if info is not None else None


@router.post("/{item_id}/estimates", status_code=201)
def add_estimate(request: Request, principal: Owner, item_id: str, body: EstimateIn) -> EstimateOut:
    factory = session_factory(request)
    price = Money.parse(body.price, workspace.get_currency(factory))
    info = catalog.add_estimate(
        factory,
        principal,
        item_id,
        catalog.EstimateCreate(
            price=price, as_of=body.as_of, confidence=body.confidence, origin=body.origin
        ),
    )
    return EstimateOut.of(info)


@router.get("/{item_id}/movements")
def list_movements(request: Request, principal: Owner, item_id: str) -> list[MovementOut]:
    rows = inventory.list_movements(session_factory(request), principal, item_id)
    return [MovementOut.of(row) for row in rows]


@router.post("/{item_id}/adjustments", status_code=201)
def adjust_stock(
    request: Request, principal: Owner, item_id: str, body: AdjustmentIn
) -> MovementOut:
    info = inventory.adjust(
        session_factory(request),
        principal,
        item_id,
        body.delta,
        occurred_on=body.occurred_on,
        note=body.note,
    )
    return MovementOut.of(info)

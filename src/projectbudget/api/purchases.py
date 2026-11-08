"""``/api/v1/purchases``: record purchases and the one-click "mark purchased" (RFC v3 §4)."""

from fastapi import APIRouter, Depends, Request

from projectbudget.api.schemas import MarkPurchasedIn, PurchaseIn, PurchaseOut
from projectbudget.domain.money import Money
from projectbudget.services import purchases, workspace
from projectbudget.web.auth import Owner, require_owner, session_factory

router = APIRouter(prefix="/purchases", tags=["purchases"], dependencies=[Depends(require_owner)])


@router.get("")
def list_purchases(request: Request, principal: Owner) -> list[PurchaseOut]:
    return [
        PurchaseOut.of(p) for p in purchases.list_purchases(session_factory(request), principal)
    ]


@router.get("/{purchase_id}")
def get_purchase(request: Request, principal: Owner, purchase_id: str) -> PurchaseOut:
    return PurchaseOut.of(purchases.get_purchase(session_factory(request), principal, purchase_id))


@router.post("", status_code=201)
def record_purchase(request: Request, principal: Owner, body: PurchaseIn) -> PurchaseOut:
    factory = session_factory(request)
    currency = workspace.get_currency(factory)
    command = purchases.PurchaseCreate(
        lines=tuple(
            purchases.PurchaseLineCreate(
                catalog_item_id=line.catalog_item_id,
                qty=line.qty,
                unit_price=Money.parse(line.unit_price, currency),
            )
            for line in body.lines
        ),
        vendor=body.vendor,
        ordered_on=body.ordered_on,
        status=body.status,
        shipping=Money.parse(body.shipping, currency) if body.shipping is not None else None,
        tax=Money.parse(body.tax, currency) if body.tax is not None else None,
        notes=body.notes,
    )
    return PurchaseOut.of(purchases.record_purchase(factory, principal, command))


@router.post("/mark", status_code=201)
def mark_purchased(request: Request, principal: Owner, body: MarkPurchasedIn) -> PurchaseOut:
    """One item, received today, straight into stock: the timeline's "mark purchased" twin."""
    factory = session_factory(request)
    currency = workspace.get_currency(factory)
    info = purchases.mark_purchased(
        factory,
        principal,
        body.item_id,
        body.qty,
        Money.parse(body.unit_price, currency),
        vendor=body.vendor,
    )
    return PurchaseOut.of(info)

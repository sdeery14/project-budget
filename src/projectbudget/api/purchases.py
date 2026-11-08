"""``/api/v1/purchases``: the purchase lifecycle and the one-click "mark purchased" (RFC v3 §4)."""

from fastapi import APIRouter, Depends, Request

from projectbudget.api.schemas import (
    MarkPurchasedIn,
    PurchaseIn,
    PurchaseLineIn,
    PurchaseOut,
    PurchasePatch,
    PurchaseStatusIn,
    ReceiveIn,
    ReceiveLineIn,
)
from projectbudget.domain.money import Money
from projectbudget.services import purchases, workspace
from projectbudget.web.auth import Owner, require_owner, session_factory

router = APIRouter(prefix="/purchases", tags=["purchases"], dependencies=[Depends(require_owner)])


def _money(request: Request, text: str | None) -> Money | None:
    if text is None:
        return None
    return Money.parse(text, workspace.get_currency(session_factory(request)))


@router.get("")
def list_purchases(request: Request, principal: Owner) -> list[PurchaseOut]:
    return [
        PurchaseOut.of(p) for p in purchases.list_purchases(session_factory(request), principal)
    ]


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


@router.get("/{purchase_id}")
def get_purchase(request: Request, principal: Owner, purchase_id: str) -> PurchaseOut:
    return PurchaseOut.of(purchases.get_purchase(session_factory(request), principal, purchase_id))


@router.patch("/{purchase_id}")
def update_purchase(
    request: Request, principal: Owner, purchase_id: str, body: PurchasePatch
) -> PurchaseOut:
    info = purchases.update_purchase(
        session_factory(request),
        principal,
        purchase_id,
        purchases.PurchaseUpdate(
            vendor=body.vendor,
            ordered_on=body.ordered_on,
            shipping=_money(request, body.shipping),
            tax=_money(request, body.tax),
            notes=body.notes,
        ),
    )
    return PurchaseOut.of(info)


@router.post("/{purchase_id}/status")
def set_status(
    request: Request, principal: Owner, purchase_id: str, body: PurchaseStatusIn
) -> PurchaseOut:
    """Planned, ordered or cancelled; ``received`` receives everything outstanding."""
    info = purchases.set_status(session_factory(request), principal, purchase_id, body.status)
    return PurchaseOut.of(info)


@router.post("/{purchase_id}/receive")
def receive_all(
    request: Request, principal: Owner, purchase_id: str, body: ReceiveIn | None = None
) -> PurchaseOut:
    info = purchases.receive_all(
        session_factory(request),
        principal,
        purchase_id,
        occurred_on=body.occurred_on if body else None,
    )
    return PurchaseOut.of(info)


@router.post("/{purchase_id}/lines", status_code=201)
def add_line(
    request: Request, principal: Owner, purchase_id: str, body: PurchaseLineIn
) -> PurchaseOut:
    factory = session_factory(request)
    info = purchases.add_line(
        factory,
        principal,
        purchase_id,
        purchases.PurchaseLineCreate(
            catalog_item_id=body.catalog_item_id,
            qty=body.qty,
            unit_price=Money.parse(body.unit_price, workspace.get_currency(factory)),
        ),
    )
    return PurchaseOut.of(info)


@router.delete("/{purchase_id}/lines/{line_id}")
def remove_line(request: Request, principal: Owner, purchase_id: str, line_id: str) -> PurchaseOut:
    info = purchases.remove_line(session_factory(request), principal, purchase_id, line_id)
    return PurchaseOut.of(info)


@router.post("/{purchase_id}/lines/{line_id}/receive")
def receive_line(
    request: Request, principal: Owner, purchase_id: str, line_id: str, body: ReceiveLineIn
) -> PurchaseOut:
    """Receive part or all of one line; the remainder stays on order (acceptance test 4b)."""
    info = purchases.receive_line(
        session_factory(request), principal, line_id, body.qty, occurred_on=body.occurred_on
    )
    if info.id != purchase_id:
        raise purchases.PurchaseLineNotFoundError(line_id)
    return PurchaseOut.of(info)

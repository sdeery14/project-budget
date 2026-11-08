"""Purchases pages: list, create, detail with lines, partial receipt and status changes.

JSON twin: ``/api/v1/purchases``.
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from projectbudget.domain.catalog import PurchaseStatus
from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.services import catalog, purchases, workspace
from projectbudget.services.errors import USER_ERRORS
from projectbudget.web.auth import Owner, session_factory
from projectbudget.web.templates import render

PURCHASES_PATH = "/purchases"

router = APIRouter(tags=["pages"])


def _list_context(request: Request, principal: Principal, **extra: Any) -> dict[str, Any]:
    factory = session_factory(request)
    return {
        "purchases": purchases.list_purchases(factory, principal),
        "items": catalog.list_items(factory, principal),
        "currency": workspace.get_currency(factory),
        "statuses": [PurchaseStatus.PLANNED, PurchaseStatus.ORDERED, PurchaseStatus.RECEIVED],
        **extra,
    }


def _detail_context(
    request: Request, principal: Principal, purchase_id: str, **extra: Any
) -> dict[str, Any]:
    factory = session_factory(request)
    info = purchases.get_purchase(factory, principal, purchase_id)
    items = {item.id: item for item in catalog.list_items(factory, principal, include_merged=True)}
    return {
        "purchase": info,
        "items": items,
        "catalog": [item for item in items.values() if not item.merged],
        "currency": workspace.get_currency(factory),
        **extra,
    }


def _detail_error(
    request: Request, principal: Principal, purchase_id: str, exc: Exception
) -> HTMLResponse:
    return render(
        request,
        "purchase.html",
        _detail_context(request, principal, purchase_id, error=str(exc)),
        status_code=422,
    )


@router.get(PURCHASES_PATH, response_class=HTMLResponse)
def purchases_page(request: Request, principal: Owner) -> HTMLResponse:
    return render(request, "purchases.html", _list_context(request, principal))


@router.post(PURCHASES_PATH, response_class=HTMLResponse)
def create_purchase(
    request: Request,
    principal: Owner,
    catalog_item_id: Annotated[str, Form()],
    qty: Annotated[int, Form()],
    unit_price: Annotated[str, Form()],
    vendor: Annotated[str, Form()] = "",
    ordered_on: Annotated[date | None, Form()] = None,
    status: Annotated[PurchaseStatus, Form()] = PurchaseStatus.ORDERED,
    shipping: Annotated[str, Form()] = "0",
    tax: Annotated[str, Form()] = "0",
    notes: Annotated[str, Form()] = "",
) -> Response:
    """Start a purchase with its first line; more lines are added on the purchase page."""
    factory = session_factory(request)
    currency = workspace.get_currency(factory)
    try:
        info = purchases.record_purchase(
            factory,
            principal,
            purchases.PurchaseCreate(
                lines=(
                    purchases.PurchaseLineCreate(
                        catalog_item_id=catalog_item_id,
                        qty=qty,
                        unit_price=Money.parse(unit_price, currency),
                    ),
                ),
                vendor=vendor,
                ordered_on=ordered_on,
                status=status,
                shipping=Money.parse(shipping or "0", currency),
                tax=Money.parse(tax or "0", currency),
                notes=notes,
            ),
        )
    except USER_ERRORS as exc:
        return render(
            request,
            "purchases.html",
            _list_context(request, principal, error=str(exc)),
            status_code=422,
        )
    return RedirectResponse(f"{PURCHASES_PATH}/{info.id}", status_code=303)


@router.get(PURCHASES_PATH + "/{purchase_id}", response_class=HTMLResponse)
def purchase_page(request: Request, principal: Owner, purchase_id: str) -> HTMLResponse:
    return render(request, "purchase.html", _detail_context(request, principal, purchase_id))


@router.post(PURCHASES_PATH + "/{purchase_id}", response_class=HTMLResponse)
def update_purchase(
    request: Request,
    principal: Owner,
    purchase_id: str,
    vendor: Annotated[str, Form()] = "",
    ordered_on: Annotated[date | None, Form()] = None,
    shipping: Annotated[str, Form()] = "0",
    tax: Annotated[str, Form()] = "0",
    notes: Annotated[str, Form()] = "",
) -> Response:
    factory = session_factory(request)
    currency = workspace.get_currency(factory)
    try:
        purchases.update_purchase(
            factory,
            principal,
            purchase_id,
            purchases.PurchaseUpdate(
                vendor=vendor,
                ordered_on=ordered_on,
                shipping=Money.parse(shipping or "0", currency),
                tax=Money.parse(tax or "0", currency),
                notes=notes,
            ),
        )
    except USER_ERRORS as exc:
        return _detail_error(request, principal, purchase_id, exc)
    return RedirectResponse(f"{PURCHASES_PATH}/{purchase_id}", status_code=303)


@router.post(PURCHASES_PATH + "/{purchase_id}/status", response_class=HTMLResponse)
def set_status(
    request: Request,
    principal: Owner,
    purchase_id: str,
    status: Annotated[PurchaseStatus, Form()],
) -> Response:
    try:
        purchases.set_status(session_factory(request), principal, purchase_id, status)
    except USER_ERRORS as exc:
        return _detail_error(request, principal, purchase_id, exc)
    return RedirectResponse(f"{PURCHASES_PATH}/{purchase_id}", status_code=303)


@router.post(PURCHASES_PATH + "/{purchase_id}/lines", response_class=HTMLResponse)
def add_line(
    request: Request,
    principal: Owner,
    purchase_id: str,
    catalog_item_id: Annotated[str, Form()],
    qty: Annotated[int, Form()],
    unit_price: Annotated[str, Form()],
) -> Response:
    factory = session_factory(request)
    try:
        purchases.add_line(
            factory,
            principal,
            purchase_id,
            purchases.PurchaseLineCreate(
                catalog_item_id=catalog_item_id,
                qty=qty,
                unit_price=Money.parse(unit_price, workspace.get_currency(factory)),
            ),
        )
    except USER_ERRORS as exc:
        return _detail_error(request, principal, purchase_id, exc)
    return RedirectResponse(f"{PURCHASES_PATH}/{purchase_id}", status_code=303)


@router.post(PURCHASES_PATH + "/{purchase_id}/lines/{line_id}/remove", response_class=HTMLResponse)
def remove_line(request: Request, principal: Owner, purchase_id: str, line_id: str) -> Response:
    try:
        purchases.remove_line(session_factory(request), principal, purchase_id, line_id)
    except USER_ERRORS as exc:
        return _detail_error(request, principal, purchase_id, exc)
    return RedirectResponse(f"{PURCHASES_PATH}/{purchase_id}", status_code=303)


@router.post(PURCHASES_PATH + "/{purchase_id}/lines/{line_id}/receive", response_class=HTMLResponse)
def receive_line(
    request: Request,
    principal: Owner,
    purchase_id: str,
    line_id: str,
    qty: Annotated[int, Form()],
    occurred_on: Annotated[date | None, Form()] = None,
) -> Response:
    """Receive some or all of a line; what is left stays on order (acceptance test 4b)."""
    try:
        purchases.receive_line(
            session_factory(request), principal, line_id, qty, occurred_on=occurred_on
        )
    except USER_ERRORS as exc:
        return _detail_error(request, principal, purchase_id, exc)
    return RedirectResponse(f"{PURCHASES_PATH}/{purchase_id}", status_code=303)

"""Catalog pages: items, price estimates and stock.

JSON twin: ``/api/v1/catalog/items``.
"""

from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from projectbudget.domain.catalog import Confidence, ItemKind, ReviewState
from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.services import catalog, inventory, workspace
from projectbudget.services.catalog import CatalogItemInfo, EstimateInfo
from projectbudget.services.errors import USER_ERRORS
from projectbudget.web.auth import Owner, session_factory
from projectbudget.web.templates import render

CATALOG_PATH = "/catalog"

router = APIRouter(tags=["pages"])


@dataclass(frozen=True, slots=True)
class ItemRow:
    item: CatalogItemInfo
    estimate: EstimateInfo | None
    on_hand: int


def _list_context(request: Request, principal: Principal, **extra: Any) -> dict[str, Any]:
    factory = session_factory(request)
    rows = [
        ItemRow(
            item=item,
            estimate=catalog.current_estimate(factory, principal, item.id),
            on_hand=inventory.on_hand(factory, principal, item.id),
        )
        for item in catalog.list_items(factory, principal)
    ]
    return {"rows": rows, "kinds": list(ItemKind), **extra}


def _detail_context(
    request: Request, principal: Principal, item_id: str, **extra: Any
) -> dict[str, Any]:
    factory = session_factory(request)
    return {
        "item": catalog.get_item(factory, principal, item_id),
        "estimates": catalog.list_estimates(factory, principal, item_id),
        "current": catalog.current_estimate(factory, principal, item_id),
        "movements": inventory.list_movements(factory, principal, item_id),
        "on_hand": inventory.on_hand(factory, principal, item_id),
        "currency": workspace.get_currency(factory),
        "kinds": list(ItemKind),
        "review_states": list(ReviewState),
        "confidences": list(Confidence),
        "merge_targets": [
            other
            for other in catalog.list_items(factory, principal)
            if other.id != item_id
            and other.unit == catalog.get_item(factory, principal, item_id).unit
            and other.kind == catalog.get_item(factory, principal, item_id).kind
        ],
        **extra,
    }


@router.get(CATALOG_PATH, response_class=HTMLResponse)
def catalog_page(request: Request, principal: Owner) -> HTMLResponse:
    return render(request, "catalog.html", _list_context(request, principal))


@router.post(CATALOG_PATH, response_class=HTMLResponse)
def create_item(
    request: Request,
    principal: Owner,
    name: Annotated[str, Form()],
    kind: Annotated[ItemKind, Form()] = ItemKind.CONSUMABLE,
    unit: Annotated[str, Form()] = "each",
    slug: Annotated[str, Form()] = "",
) -> Response:
    try:
        info = catalog.create_item(
            session_factory(request),
            principal,
            catalog.CatalogItemCreate(name=name, kind=kind, unit=unit, slug=slug.strip() or None),
        )
    except USER_ERRORS as exc:
        return render(
            request,
            "catalog.html",
            _list_context(request, principal, error=str(exc)),
            status_code=422,
        )
    return RedirectResponse(f"{CATALOG_PATH}/{info.id}", status_code=303)


@router.get(CATALOG_PATH + "/{item_id}", response_class=HTMLResponse)
def item_page(request: Request, principal: Owner, item_id: str) -> HTMLResponse:
    return render(request, "catalog_item.html", _detail_context(request, principal, item_id))


def _detail_error(
    request: Request, principal: Principal, item_id: str, exc: Exception
) -> HTMLResponse:
    return render(
        request,
        "catalog_item.html",
        _detail_context(request, principal, item_id, error=str(exc)),
        status_code=422,
    )


@router.post(CATALOG_PATH + "/{item_id}", response_class=HTMLResponse)
def update_item(
    request: Request,
    principal: Owner,
    item_id: str,
    name: Annotated[str, Form()],
    kind: Annotated[ItemKind, Form()],
    unit: Annotated[str, Form()],
    review_state: Annotated[ReviewState, Form()],
) -> Response:
    try:
        catalog.update_item(
            session_factory(request),
            principal,
            item_id,
            catalog.CatalogItemUpdate(name=name, kind=kind, unit=unit, review_state=review_state),
        )
    except USER_ERRORS as exc:
        return _detail_error(request, principal, item_id, exc)
    return RedirectResponse(f"{CATALOG_PATH}/{item_id}", status_code=303)


@router.post(CATALOG_PATH + "/{item_id}/merge", response_class=HTMLResponse)
def merge_item(
    request: Request, principal: Owner, item_id: str, target_id: Annotated[str, Form()] = ""
) -> Response:
    try:
        if not target_id.strip():
            raise catalog.MergeError("Choose the item to keep")
        target = catalog.merge_items(session_factory(request), principal, item_id, target_id)
    except USER_ERRORS as exc:
        return _detail_error(request, principal, item_id, exc)
    return RedirectResponse(f"{CATALOG_PATH}/{target.id}", status_code=303)


@router.post(CATALOG_PATH + "/{item_id}/estimates", response_class=HTMLResponse)
def add_estimate(
    request: Request,
    principal: Owner,
    item_id: str,
    price: Annotated[str, Form()],
    as_of: Annotated[date | None, Form()] = None,
    confidence: Annotated[Confidence, Form()] = Confidence.MEDIUM,
) -> Response:
    factory = session_factory(request)
    try:
        money = Money.parse(price, workspace.get_currency(factory))
        catalog.add_estimate(
            factory,
            principal,
            item_id,
            catalog.EstimateCreate(price=money, as_of=as_of, confidence=confidence),
        )
    except USER_ERRORS as exc:
        return _detail_error(request, principal, item_id, exc)
    return RedirectResponse(f"{CATALOG_PATH}/{item_id}", status_code=303)


@router.post(CATALOG_PATH + "/{item_id}/adjustments", response_class=HTMLResponse)
def adjust_stock(
    request: Request,
    principal: Owner,
    item_id: str,
    delta: Annotated[int, Form()],
    note: Annotated[str, Form()] = "",
    occurred_on: Annotated[date | None, Form()] = None,
) -> Response:
    try:
        inventory.adjust(
            session_factory(request), principal, item_id, delta, occurred_on=occurred_on, note=note
        )
    except USER_ERRORS as exc:
        return _detail_error(request, principal, item_id, exc)
    return RedirectResponse(f"{CATALOG_PATH}/{item_id}", status_code=303)

"""The timeline page: month-by-month explanation, both schedules, "mark purchased" (ADR-006).

JSON twins: ``GET /api/v1/forecast`` and ``POST /api/v1/purchases/mark``.
"""

import json
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from projectbudget.api.schemas import DISCLAIMER
from projectbudget.domain.forecast import Forecast, Schedule
from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.services import forecast, purchases, workspace
from projectbudget.services.errors import USER_ERRORS
from projectbudget.web.auth import Owner, session_factory
from projectbudget.web.templates import render

TIMELINE_PATH = "/timeline"

router = APIRouter(tags=["pages"])


def _chart_data(result: Forecast) -> dict[str, str]:
    """Series for the vendored chart, as JSON strings for ``data-*`` attributes (no inline script)."""

    def closings(sched: Schedule) -> list[str]:
        return [str(month.closing) for month in sched.months]

    # The engine carries both schedules over the same months, so the series line up
    # point for point; nothing is padded or repeated here.
    labels = [month.month.strftime("%b %Y") for month in result.conservative.months]
    return {
        "labels": json.dumps(labels),
        "conservative": json.dumps(closings(result.conservative)),
        "optimistic": json.dumps(closings(result.optimistic)),
        "purchases": json.dumps(
            [str(month.purchases_total) for month in result.conservative.months]
        ),
        "currency": result.plan.currency,
    }


def _context(
    request: Request, principal: Principal, *, today: date | None = None, **extra: Any
) -> dict[str, Any]:
    factory = session_factory(request)
    result = forecast.get_forecast(factory, principal, today=today)
    optimistic_month = (
        {outcome.item.item_id: outcome for outcome in result.optimistic.items} if result else {}
    )
    return {
        "forecast": result,
        "optimistic_by_item": optimistic_month,
        "chart": _chart_data(result) if result else None,
        "currency": workspace.get_currency(factory),
        "disclaimer": DISCLAIMER,
        **extra,
    }


@router.get(TIMELINE_PATH, response_class=HTMLResponse)
def timeline_page(request: Request, principal: Owner, today: date | None = None) -> HTMLResponse:
    return render(request, "timeline.html", _context(request, principal, today=today))


@router.post("/purchases/mark", response_class=HTMLResponse)
def mark_purchased(
    request: Request,
    principal: Owner,
    item_id: Annotated[str, Form()],
    qty: Annotated[int, Form()],
    unit_price: Annotated[str, Form()],
    vendor: Annotated[str, Form()] = "",
) -> Response:
    """Record a received purchase for one backlog row; it leaves the backlog and the timeline."""
    factory = session_factory(request)
    try:
        purchases.mark_purchased(
            factory,
            principal,
            item_id,
            qty,
            Money.parse(unit_price, workspace.get_currency(factory)),
            vendor=vendor,
        )
    except USER_ERRORS as exc:
        return render(
            request, "timeline.html", _context(request, principal, error=str(exc)), status_code=422
        )
    return RedirectResponse(TIMELINE_PATH, status_code=303)

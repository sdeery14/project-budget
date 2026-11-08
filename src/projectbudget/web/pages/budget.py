"""The budget page: balance as of a date, reserve, contribution, horizon, contingency, overrides.

JSON twin: ``/api/v1/budget``.
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from projectbudget.domain.forecast import DEFAULT_HORIZON_MONTHS
from projectbudget.domain.money import Money, MoneyParseError
from projectbudget.domain.principal import Principal
from projectbudget.services import budget, workspace
from projectbudget.services.errors import USER_ERRORS
from projectbudget.web.auth import Owner, session_factory
from projectbudget.web.templates import render

BUDGET_PATH = "/budget"

router = APIRouter(tags=["pages"])


def _context(request: Request, principal: Principal, **extra: Any) -> dict[str, Any]:
    factory = session_factory(request)
    return {
        "plan": budget.get_plan(factory, principal),
        "currency": workspace.get_currency(factory),
        "default_horizon": DEFAULT_HORIZON_MONTHS,
        **extra,
    }


def _error(request: Request, principal: Principal, exc: Exception) -> HTMLResponse:
    return render(
        request, "budget.html", _context(request, principal, error=str(exc)), status_code=422
    )


def _month(text: str) -> date:
    """An ``<input type="month">`` value, ``YYYY-MM``, as the first of that month."""
    try:
        year, month = (int(part) for part in text.strip().split("-", 1))
        return date(year, month, 1)
    except ValueError as exc:
        raise MoneyParseError(f"Month must look like 2026-12, got {text!r}") from exc


@router.get(BUDGET_PATH, response_class=HTMLResponse)
def budget_page(request: Request, principal: Owner) -> HTMLResponse:
    return render(request, "budget.html", _context(request, principal))


@router.post(BUDGET_PATH, response_class=HTMLResponse)
def save_plan(
    request: Request,
    principal: Owner,
    earmarked_funds: Annotated[str, Form()],
    protected_reserve: Annotated[str, Form()] = "0",
    monthly_contribution: Annotated[str, Form()] = "0",
    horizon_months: Annotated[int, Form()] = DEFAULT_HORIZON_MONTHS,
    contingency_pct: Annotated[int, Form()] = 0,
    balance_as_of: Annotated[date | None, Form()] = None,
) -> Response:
    factory = session_factory(request)
    currency = workspace.get_currency(factory)
    try:
        budget.set_plan(
            factory,
            principal,
            budget.PlanUpdate(
                earmarked_funds=Money.parse(earmarked_funds, currency),
                protected_reserve=Money.parse(protected_reserve, currency),
                monthly_contribution=Money.parse(monthly_contribution, currency),
                horizon_months=horizon_months,
                contingency_pct=contingency_pct,
                balance_as_of=balance_as_of,
            ),
        )
    except USER_ERRORS as exc:
        return _error(request, principal, exc)
    return RedirectResponse(BUDGET_PATH, status_code=303)


@router.post(BUDGET_PATH + "/overrides", response_class=HTMLResponse)
def add_override(
    request: Request,
    principal: Owner,
    month: Annotated[str, Form()],
    delta: Annotated[str, Form()],
    label: Annotated[str, Form()],
) -> Response:
    factory = session_factory(request)
    currency = workspace.get_currency(factory)
    try:
        budget.add_override(
            factory,
            principal,
            budget.OverrideCreate(
                month=_month(month), delta=Money.parse(delta, currency), label=label
            ),
        )
    except USER_ERRORS as exc:
        return _error(request, principal, exc)
    return RedirectResponse(BUDGET_PATH, status_code=303)


@router.post(BUDGET_PATH + "/overrides/{override_id}/delete")
def remove_override(request: Request, principal: Owner, override_id: str) -> Response:
    budget.remove_override(session_factory(request), principal, override_id)
    return RedirectResponse(BUDGET_PATH, status_code=303)

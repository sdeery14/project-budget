"""``/api/v1/budget``: the plan and its month overrides (ADR-006)."""

from fastapi import APIRouter, Depends, HTTPException, Request

from projectbudget.api.schemas import OverrideIn, OverrideOut, PlanIn, PlanOut
from projectbudget.domain.money import Money
from projectbudget.services import budget, workspace
from projectbudget.web.auth import Owner, require_owner, session_factory

router = APIRouter(prefix="/budget", tags=["budget"], dependencies=[Depends(require_owner)])


@router.get("/plan")
def get_plan(request: Request, principal: Owner) -> PlanOut:
    info = budget.get_plan(session_factory(request), principal)
    if info is None:
        raise HTTPException(status_code=404, detail="No budget plan yet; PUT one first")
    return PlanOut.of(info)


@router.put("/plan")
def set_plan(request: Request, principal: Owner, body: PlanIn) -> PlanOut:
    factory = session_factory(request)
    currency = workspace.get_currency(factory)
    info = budget.set_plan(
        factory,
        principal,
        budget.PlanUpdate(
            earmarked_funds=Money.parse(body.earmarked_funds, currency),
            protected_reserve=Money.parse(body.protected_reserve, currency),
            monthly_contribution=Money.parse(body.monthly_contribution, currency),
            horizon_months=body.horizon_months,
            contingency_pct=body.contingency_pct,
            balance_as_of=body.balance_as_of,
        ),
    )
    return PlanOut.of(info)


@router.post("/overrides", status_code=201)
def add_override(request: Request, principal: Owner, body: OverrideIn) -> OverrideOut:
    factory = session_factory(request)
    currency = workspace.get_currency(factory)
    info = budget.add_override(
        factory,
        principal,
        budget.OverrideCreate(
            month=body.month, delta=Money.parse(body.delta, currency), label=body.label
        ),
    )
    return OverrideOut.of(info)


@router.delete("/overrides/{override_id}", status_code=204)
def remove_override(request: Request, principal: Owner, override_id: str) -> None:
    budget.remove_override(session_factory(request), principal, override_id)

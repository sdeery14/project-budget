"""Gather the forecast's inputs and hand them to the pure engine (ADR-006).

The engine itself (``domain.forecast``) touches no database. This module only
loads the plan, the backlog and the spend since ``balance_as_of``.
"""

from datetime import date

from projectbudget.domain.forecast import Forecast
from projectbudget.domain.forecast import forecast as run_forecast
from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import backlog, budget, purchases
from projectbudget.services.clock import today as clock_today


def get_forecast(
    session_factory: SessionFactory, principal: Principal, *, today: date | None = None
) -> Forecast | None:
    """The forecast, or None until a budget plan exists."""
    plan = budget.get_plan(session_factory, principal)
    if plan is None:
        return None
    current = backlog.get_backlog(session_factory, principal)
    with session_factory() as db:
        recent = purchases.total_since(db, plan.balance_as_of, plan.currency)
    return run_forecast(budget.to_domain(plan), current, recent, today or clock_today())

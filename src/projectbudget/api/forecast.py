"""``/api/v1/forecast``: both schedules with their month-by-month explanation (ADR-006)."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request

from projectbudget.api.schemas import ForecastOut
from projectbudget.services import forecast
from projectbudget.web.auth import Owner, require_owner, session_factory

router = APIRouter(prefix="/forecast", tags=["forecast"], dependencies=[Depends(require_owner)])


@router.get("")
def get_forecast(request: Request, principal: Owner, today: date | None = None) -> ForecastOut:
    """The forecast as of ``today`` (default: today in the workspace time zone). 404 until a plan exists."""
    result = forecast.get_forecast(session_factory(request), principal, today=today)
    if result is None:
        raise HTTPException(status_code=404, detail="No budget plan yet; set one at /budget")
    return ForecastOut.of(result)

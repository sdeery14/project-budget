"""``GET /health``: the one route that never requires the owner (RFC v3 §7).

Reports the package version and whether the database answers ``SELECT 1``.
Returns 503 when it does not, so container health checks and reverse proxies
see a real signal rather than a process that is up but cannot serve.
"""

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from projectbudget import __version__

router = APIRouter(tags=["health"])


class HealthReport(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    version: str


def database_is_reachable(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True


@router.get("/health", response_model=HealthReport)
def health(request: Request, response: Response) -> HealthReport:
    engine: Engine = request.app.state.engine
    if database_is_reachable(engine):
        return HealthReport(status="ok", database="ok", version=__version__)
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthReport(status="degraded", database="unavailable", version=__version__)

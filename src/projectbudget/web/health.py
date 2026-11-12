"""The routes that never require the owner: ``GET /health`` and ``GET /favicon.ico``.

``/health`` reports the package version and whether the database answers ``SELECT 1``
(RFC v3 §7). It returns 503 when it does not, so container health checks and reverse
proxies see a real signal rather than a process that is up but cannot serve.

``/favicon.ico`` exists because browsers ask for it before any page, signed in or not;
``base.html`` links the same SVG, so a browser that honours the link never asks (#16).
"""

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from projectbudget import __version__
from projectbudget.web.templates import STATIC_DIR

router = APIRouter(tags=["health"])

FAVICON = STATIC_DIR / "favicon.svg"


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


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    """The tab icon for browsers that ask at the conventional path instead of the link."""
    return FileResponse(
        FAVICON, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"}
    )

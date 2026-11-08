"""Application factory.

``create_app()`` with no arguments reads settings from the environment, which is
what ``uvicorn --factory`` and the container use. Tests pass settings explicitly.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from projectbudget import __version__
from projectbudget.persistence.engine import make_engine
from projectbudget.settings import Settings
from projectbudget.web.health import router as health_router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application with its engine attached to ``app.state``."""
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved
        app.state.engine = make_engine(resolved.effective_database_url)
        try:
            yield
        finally:
            app.state.engine.dispose()

    app = FastAPI(
        title="ProjectBudget",
        version=__version__,
        lifespan=lifespan,
        # Interactive API docs stay off until every route requires the owner (PR3, ADR-005).
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(health_router)
    return app

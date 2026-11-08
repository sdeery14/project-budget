"""Application factory.

``create_app()`` with no arguments reads settings from the environment, which is
what ``uvicorn --factory`` and the container use. Tests pass settings explicitly.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from projectbudget import __version__
from projectbudget.api.router import router as api_router
from projectbudget.persistence.engine import make_engine
from projectbudget.persistence.session import make_session_factory
from projectbudget.settings import Settings
from projectbudget.web.errors import register_error_handlers
from projectbudget.web.health import router as health_router
from projectbudget.web.pages.backlog import router as backlog_router
from projectbudget.web.pages.catalog import router as catalog_router
from projectbudget.web.pages.login import router as login_router
from projectbudget.web.pages.projects import router as projects_router
from projectbudget.web.pages.tokens import router as tokens_router
from projectbudget.web.security import OriginCheckMiddleware, SecurityHeadersMiddleware
from projectbudget.web.templates import STATIC_DIR


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application with its engine attached to ``app.state``."""
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved
        app.state.engine = make_engine(resolved.effective_database_url)
        app.state.session_factory = make_session_factory(app.state.engine)
        try:
            yield
        finally:
            app.state.engine.dispose()

    app = FastAPI(
        title="ProjectBudget",
        version=__version__,
        lifespan=lifespan,
        # Swagger UI and ReDoc load from a CDN the CSP forbids. The schema itself is
        # served behind owner auth at /api/v1/openapi.json.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # Outermost last: headers wrap everything, including the Origin check's 403.
    app.add_middleware(OriginCheckMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=resolved.secure_cookies)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(health_router)
    app.include_router(login_router)
    app.include_router(backlog_router)
    app.include_router(projects_router)
    app.include_router(catalog_router)
    app.include_router(tokens_router)
    app.include_router(api_router)
    register_error_handlers(app)
    return app

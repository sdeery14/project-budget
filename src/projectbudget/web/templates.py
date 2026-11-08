"""Jinja2 rendering with the context every page needs (ADR-002)."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from projectbudget import __version__

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def render(
    request: Request,
    name: str,
    context: Mapping[str, Any] | None = None,
    *,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
) -> HTMLResponse:
    """Render a template with the principal and CSRF token when a session exists."""
    full_context: dict[str, Any] = {
        "principal": getattr(request.state, "principal", None),
        "csrf_token": getattr(request.state, "csrf_token", None),
        "version": __version__,
        **(context or {}),
    }
    return templates.TemplateResponse(
        request, name, full_context, status_code=status_code, headers=dict(headers or {})
    )

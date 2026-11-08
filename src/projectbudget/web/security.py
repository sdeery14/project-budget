"""Security headers and the cross-origin check (RFC v3 §7, ADR-005).

Both are plain ASGI middleware so they wrap every response, including errors
and the login page. The CSP allows no inline script or style; HTMX 2 runs
within it as long as templates avoid ``hx-on`` and ``js:`` (a test greps for both).
"""

from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
    ]
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
HSTS_HEADER = "max-age=31536000"


class SecurityHeadersMiddleware:
    """Add the fixed security headers; ``no-store`` for everything but static files."""

    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                if self.hsts:
                    headers["Strict-Transport-Security"] = HSTS_HEADER
                if not path.startswith("/static/"):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)


class OriginCheckMiddleware:
    """Reject state-changing requests whose Origin (or Referer) is not this host.

    Bearer-token requests are exempt: no cookie travels with them, so a cross-site
    page cannot forge one. Everything else, including login, must come from here.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            headers = Headers(scope=scope)
            if not _has_bearer(headers) and not origin_allowed(headers):
                path: str = scope["path"]
                detail = "Cross-origin request rejected"
                if path.startswith("/api/"):
                    await JSONResponse({"detail": detail}, status_code=403)(scope, receive, send)
                else:
                    await PlainTextResponse(detail, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _has_bearer(headers: Headers) -> bool:
    return headers.get("authorization", "").lower().startswith("bearer ")


def origin_allowed(headers: Headers) -> bool:
    """True when Origin, or failing that Referer, names the host the request arrived at."""
    hosts = {
        value.strip().lower()
        for value in (headers.get("host"), headers.get("x-forwarded-host"))
        if value
    }
    if not hosts:
        return False
    source = headers.get("origin") or headers.get("referer")
    if not source or source == "null":
        return False
    netloc = urlsplit(source).netloc.strip().lower()
    return bool(netloc) and netloc in hosts

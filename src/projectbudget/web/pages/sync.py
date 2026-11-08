"""Sync pages: sources, a dry-run plan, apply, run history (ADR-007).

JSON twin: ``/api/v1/sync``. An uploaded manifest travels from the preview to
the apply form as base64 so what you saw is what gets applied.
"""

import base64
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from projectbudget.adapters.sources import FetchedManifest
from projectbudget.domain.catalog import SourceKind
from projectbudget.domain.principal import Principal
from projectbudget.services import sync
from projectbudget.services.clock import today
from projectbudget.services.errors import USER_ERRORS
from projectbudget.web.auth import Owner, app_settings, session_factory
from projectbudget.web.templates import render

SYNC_PATH = "/sync"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

router = APIRouter(tags=["pages"])


def _list_context(request: Request, principal: Principal, **extra: Any) -> dict[str, Any]:
    factory = session_factory(request)
    return {
        "sources": sync.list_sources(factory, principal),
        "kinds": list(SourceKind),
        "import_dir": app_settings(request).effective_sync_import_dir,
        **extra,
    }


def _detail_context(
    request: Request, principal: Principal, source_id: str, **extra: Any
) -> dict[str, Any]:
    factory = session_factory(request)
    return {
        "source": sync.get_source(factory, principal, source_id),
        "runs": sync.list_runs(factory, principal, source_id),
        "preview": None,
        "result": None,
        "content_b64": None,
        **extra,
    }


def _detail_error(
    request: Request, principal: Principal, source_id: str, exc: Exception
) -> HTMLResponse:
    issues = getattr(exc, "issues", ())
    return render(
        request,
        "sync_source.html",
        _detail_context(request, principal, source_id, error=str(exc), issues=list(issues)),
        status_code=422,
    )


async def _uploaded(file: UploadFile | None) -> FetchedManifest | None:
    if file is None or not file.filename:
        return None
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise sync.InvalidSourceError("That file is larger than 2 MB")
    return FetchedManifest.of(
        raw, revision=None, as_of=today(), description=f"upload {file.filename}"
    )


def _from_b64(text: str | None) -> FetchedManifest | None:
    if not text:
        return None
    try:
        raw = base64.b64decode(text, validate=True)
    except ValueError as exc:
        raise sync.InvalidSourceError(
            "The uploaded manifest did not survive the round trip; upload it again"
        ) from exc
    return FetchedManifest.of(raw, revision=None, as_of=today(), description="upload")


@router.get(SYNC_PATH, response_class=HTMLResponse)
def sync_page(request: Request, principal: Owner) -> HTMLResponse:
    return render(request, "sync.html", _list_context(request, principal))


# token_env below is the NAME of an environment variable, never a secret.
@router.post(SYNC_PATH, response_class=HTMLResponse)
def create_source(  # nosec B107
    request: Request,
    principal: Owner,
    kind: Annotated[SourceKind, Form()],
    locator: Annotated[str, Form()],
    branch: Annotated[str, Form()] = "",
    path: Annotated[str, Form()] = "",
    external_project_id: Annotated[str, Form()] = "",
    token_env: Annotated[str, Form()] = "",
) -> Response:
    try:
        info = sync.create_source(
            session_factory(request),
            principal,
            sync.SourceCreate(
                kind=kind,
                locator=locator,
                branch=branch or None,
                path=path or None,
                external_project_id=external_project_id or None,
                token_env=token_env or None,
            ),
            import_dir=app_settings(request).effective_sync_import_dir,
        )
    except USER_ERRORS as exc:
        return render(
            request, "sync.html", _list_context(request, principal, error=str(exc)), status_code=422
        )
    return RedirectResponse(f"{SYNC_PATH}/{info.id}", status_code=303)


@router.get(SYNC_PATH + "/{source_id}", response_class=HTMLResponse)
def source_page(request: Request, principal: Owner, source_id: str) -> HTMLResponse:
    return render(request, "sync_source.html", _detail_context(request, principal, source_id))


@router.post(SYNC_PATH + "/{source_id}/preview", response_class=HTMLResponse)
async def preview(
    request: Request,
    principal: Owner,
    source_id: str,
    file: Annotated[UploadFile | None, File()] = None,
    force: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    try:
        fetched = await _uploaded(file)
        shown = sync.preview(
            session_factory(request),
            principal,
            source_id,
            import_dir=app_settings(request).effective_sync_import_dir,
            fetched=fetched,
            force=force,
        )
    except USER_ERRORS as exc:
        return _detail_error(request, principal, source_id, exc)
    content_b64 = base64.b64encode(fetched.content).decode("ascii") if fetched else None
    return render(
        request,
        "sync_source.html",
        _detail_context(request, principal, source_id, preview=shown, content_b64=content_b64),
    )


@router.post(SYNC_PATH + "/{source_id}/apply", response_class=HTMLResponse)
def apply(
    request: Request,
    principal: Owner,
    source_id: str,
    expected_content_hash: Annotated[str, Form()] = "",
    expected_plan_digest: Annotated[str, Form()] = "",
    content_b64: Annotated[str, Form()] = "",
    force: Annotated[bool, Form()] = False,
) -> Response:
    try:
        result = sync.apply(
            session_factory(request),
            principal,
            source_id,
            import_dir=app_settings(request).effective_sync_import_dir,
            fetched=_from_b64(content_b64),
            expected_content_hash=expected_content_hash or None,
            expected_plan_digest=expected_plan_digest or None,
            force=force,
        )
    except USER_ERRORS as exc:
        return _detail_error(request, principal, source_id, exc)
    return render(
        request, "sync_source.html", _detail_context(request, principal, source_id, result=result)
    )


@router.post(SYNC_PATH + "/{source_id}/detach")
def detach(request: Request, principal: Owner, source_id: str) -> Response:
    sync.detach_source(session_factory(request), principal, source_id)
    return RedirectResponse(SYNC_PATH, status_code=303)

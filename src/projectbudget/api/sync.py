"""``/api/v1/sync``: sources, runs, dry-run preview and apply (ADR-007)."""

from fastapi import APIRouter, Depends, Request

from projectbudget.adapters.sources import FetchedManifest
from projectbudget.api.schemas import (
    SyncApplyIn,
    SyncPreviewIn,
    SyncPreviewOut,
    SyncResultOut,
    SyncRunOut,
    SyncSourceIn,
    SyncSourceOut,
    SyncSourceUpdateIn,
)
from projectbudget.services import sync
from projectbudget.services.clock import today
from projectbudget.web.auth import Owner, app_settings, require_owner, session_factory

router = APIRouter(prefix="/sync", tags=["sync"], dependencies=[Depends(require_owner)])


def _uploaded(text: str | None, description: str) -> FetchedManifest | None:
    """A manifest sent in the request body instead of fetched from the source."""
    if text is None:
        return None
    return FetchedManifest.of(
        text.encode("utf-8"), revision=None, as_of=today(), description=description
    )


@router.get("/sources")
def list_sources(request: Request, principal: Owner) -> list[SyncSourceOut]:
    return [SyncSourceOut.of(s) for s in sync.list_sources(session_factory(request), principal)]


@router.post("/sources", status_code=201)
def create_source(request: Request, principal: Owner, body: SyncSourceIn) -> SyncSourceOut:
    info = sync.create_source(
        session_factory(request),
        principal,
        sync.SourceCreate(
            kind=body.kind,
            locator=body.locator,
            branch=body.branch,
            path=body.path,
            external_project_id=body.external_project_id,
            token_env=body.token_env,
            project_id=body.project_id,
        ),
        import_dir=app_settings(request).effective_sync_import_dir,
    )
    return SyncSourceOut.of(info)


@router.get("/sources/{source_id}")
def get_source(request: Request, principal: Owner, source_id: str) -> SyncSourceOut:
    return SyncSourceOut.of(sync.get_source(session_factory(request), principal, source_id))


@router.patch("/sources/{source_id}")
def update_source(
    request: Request, principal: Owner, source_id: str, body: SyncSourceUpdateIn
) -> SyncSourceOut:
    """Change branch, path, token variable or CSV project id; kind and locator are fixed."""
    factory = session_factory(request)
    current = sync.get_source(factory, principal, source_id)

    def pick(new: str | None, old: str | None) -> str | None:
        return old if new is None else (new or None)

    info = sync.update_source(
        factory,
        principal,
        source_id,
        sync.SourceUpdate(
            branch=pick(body.branch, current.branch),
            path=pick(body.path, current.path),
            external_project_id=pick(body.external_project_id, current.external_project_id),
            token_env=pick(body.token_env, current.token_env),
        ),
        import_dir=app_settings(request).effective_sync_import_dir,
    )
    return SyncSourceOut.of(info)


@router.delete("/sources/{source_id}", status_code=204)
def detach_source(request: Request, principal: Owner, source_id: str) -> None:
    """Remove the source; its project and requirements stay and become manual."""
    sync.detach_source(session_factory(request), principal, source_id)


@router.get("/sources/{source_id}/runs")
def list_runs(request: Request, principal: Owner, source_id: str) -> list[SyncRunOut]:
    return [
        SyncRunOut.of(r) for r in sync.list_runs(session_factory(request), principal, source_id)
    ]


@router.post("/sources/{source_id}/preview")
def preview(
    request: Request, principal: Owner, source_id: str, body: SyncPreviewIn | None = None
) -> SyncPreviewOut:
    """Dry run: fetch (or take ``manifest_text``), parse and diff. Writes only the run record for no-ops."""
    body = body or SyncPreviewIn()
    shown = sync.preview(
        session_factory(request),
        principal,
        source_id,
        import_dir=app_settings(request).effective_sync_import_dir,
        fetched=_uploaded(body.manifest_text, "upload"),
        force=body.force,
        github_token=app_settings(request).effective_github_token,
    )
    return SyncPreviewOut.of(shown)


@router.post("/sources/{source_id}/apply")
def apply(
    request: Request, principal: Owner, source_id: str, body: SyncApplyIn | None = None
) -> SyncResultOut:
    """Apply in one transaction. Pass ``expected_content_hash`` from the preview to refuse a changed source."""
    body = body or SyncApplyIn()
    result = sync.apply(
        session_factory(request),
        principal,
        source_id,
        import_dir=app_settings(request).effective_sync_import_dir,
        fetched=_uploaded(body.manifest_text, "upload"),
        expected_content_hash=body.expected_content_hash,
        expected_plan_digest=body.expected_plan_digest,
        force=body.force,
        github_token=app_settings(request).effective_github_token,
    )
    return SyncResultOut.of(result)

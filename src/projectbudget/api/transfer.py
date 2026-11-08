"""``/api/v1/export`` and ``/api/v1/import``: the canonical workspace document (ADR-008)."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from projectbudget.adapters.export_format import parse_document
from projectbudget.api.schemas import ImportSummaryOut
from projectbudget.services import transfer
from projectbudget.web.auth import Owner, require_owner, session_factory

router = APIRouter(tags=["transfer"], dependencies=[Depends(require_owner)])


@router.get("/export")
def export_workspace(request: Request, principal: Owner) -> Response:
    """The whole workspace as the versioned JSON document, byte-stable for one state."""
    document = transfer.export_workspace(session_factory(request), principal)
    return Response(
        content=document.to_json(),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="projectbudget-export.json"'},
    )


@router.post("/import")
def import_workspace(
    request: Request, principal: Owner, body: dict[str, Any], replace: bool = False
) -> ImportSummaryOut:
    """Load a document into the workspace, id for id. Needs ``replace=true`` over existing data."""
    document = parse_document(body)
    summary = transfer.import_workspace(
        session_factory(request), principal, document, replace=replace
    )
    return ImportSummaryOut.of(summary)

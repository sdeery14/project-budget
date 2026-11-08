"""Export and import of the whole workspace from the browser (ADR-008).

JSON twins: ``GET /api/v1/export`` and ``POST /api/v1/import``.
"""

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse

from projectbudget.adapters.export_format import parse_document
from projectbudget.domain.principal import Principal
from projectbudget.services import transfer
from projectbudget.services.errors import USER_ERRORS
from projectbudget.web.auth import Owner, session_factory
from projectbudget.web.templates import render

DATA_PATH = "/settings/data"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

router = APIRouter(tags=["pages"])


def _context(request: Request, principal: Principal, **extra: Any) -> dict[str, Any]:
    return {"summary": None, "error": None, **extra}


@router.get(DATA_PATH, response_class=HTMLResponse)
def data_page(request: Request, principal: Owner) -> HTMLResponse:
    return render(request, "data.html", _context(request, principal))


@router.get(DATA_PATH + "/export.json")
def download_export(request: Request, principal: Owner) -> Response:
    document = transfer.export_workspace(session_factory(request), principal)
    return Response(
        content=document.to_json(),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="projectbudget-export.json"'},
    )


@router.post(DATA_PATH + "/import", response_class=HTMLResponse)
async def upload_import(
    request: Request,
    principal: Owner,
    file: Annotated[UploadFile, File()],
    replace: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        return render(
            request,
            "data.html",
            _context(request, principal, error="That file is larger than 50 MB"),
            status_code=413,
        )
    try:
        document = parse_document(raw)
        summary = transfer.import_workspace(
            session_factory(request), principal, document, replace=replace
        )
    except USER_ERRORS as exc:  # includes InvalidDocumentError
        return render(
            request, "data.html", _context(request, principal, error=str(exc)), status_code=422
        )
    return render(request, "data.html", _context(request, principal, summary=summary))

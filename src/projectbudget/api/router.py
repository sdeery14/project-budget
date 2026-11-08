"""``/api/v1``: who am I, API tokens, the domain routers and the OpenAPI document.

Every state-changing page action has a twin here (RFC v3 §3, ADR-002). The
domain routers live in their own modules; this one wires them under one prefix
and one owner dependency.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from projectbudget.api.backlog import router as backlog_router
from projectbudget.api.budget import router as budget_router
from projectbudget.api.catalog import router as catalog_router
from projectbudget.api.forecast import router as forecast_router
from projectbudget.api.projects import router as projects_router
from projectbudget.api.purchases import router as purchases_router
from projectbudget.api.requirements import router as requirements_router
from projectbudget.domain.principal import AuthMethod
from projectbudget.services import tokens
from projectbudget.web.auth import Owner, require_owner, session_factory

router = APIRouter(prefix="/api/v1", tags=["api"], dependencies=[Depends(require_owner)])


class MeOut(BaseModel):
    username: str
    auth: AuthMethod
    read_only: bool


class TokenOut(BaseModel):
    id: str
    name: str
    read_only: bool
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class IssuedTokenOut(TokenOut):
    token: str = Field(description="Shown once; store it now.")


class TokenCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=tokens.TOKEN_NAME_MAX_LENGTH)
    read_only: bool = False


def _token_out(info: tokens.TokenInfo) -> TokenOut:
    return TokenOut(
        id=info.id,
        name=info.name,
        read_only=info.read_only,
        created_at=info.created_at,
        last_used_at=info.last_used_at,
        revoked_at=info.revoked_at,
    )


@router.get("/me")
def me(principal: Owner) -> MeOut:
    return MeOut(username=principal.username, auth=principal.auth, read_only=principal.read_only)


@router.get("/tokens")
def list_tokens(request: Request, principal: Owner) -> list[TokenOut]:
    return [_token_out(t) for t in tokens.list_tokens(session_factory(request), principal)]


@router.post("/tokens", status_code=201)
def create_token(request: Request, principal: Owner, body: TokenCreate) -> IssuedTokenOut:
    try:
        issued = tokens.issue_token(
            session_factory(request), principal, body.name, read_only=body.read_only
        )
    except tokens.TokenManagementRequiresSessionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except tokens.InvalidTokenNameError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return IssuedTokenOut(**_token_out(issued.info).model_dump(), token=issued.secret)


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(request: Request, token_id: str, principal: Owner) -> None:
    try:
        info = tokens.revoke_token(session_factory(request), principal, token_id)
    except tokens.TokenManagementRequiresSessionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if info is None:
        raise HTTPException(status_code=404, detail="No such token")


@router.get("/openapi.json", include_in_schema=False)
def openapi_document(request: Request) -> dict[str, Any]:
    """The schema, behind owner auth. Swagger UI stays off: it needs a CDN the CSP forbids."""
    schema: dict[str, Any] = request.app.openapi()
    return schema


router.include_router(projects_router)
router.include_router(catalog_router)
router.include_router(requirements_router)
router.include_router(backlog_router)
router.include_router(budget_router)
router.include_router(purchases_router)
router.include_router(forecast_router)

"""``/api/v1``: who am I, API tokens, and the OpenAPI document (ADR-002, ADR-005)."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from projectbudget.domain.principal import AuthMethod, Principal
from projectbudget.services import tokens
from projectbudget.web.auth import require_owner, session_factory

router = APIRouter(prefix="/api/v1", tags=["api"], dependencies=[Depends(require_owner)])

Owner = Annotated[Principal, Depends(require_owner)]


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

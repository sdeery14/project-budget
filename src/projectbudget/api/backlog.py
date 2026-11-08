"""``/api/v1/backlog``: the consolidated backlog and its workspace order (ADR-004)."""

from fastapi import APIRouter, Depends, Request

from projectbudget.api.schemas import BacklogOut, OrderIn
from projectbudget.services import backlog
from projectbudget.web.auth import Owner, require_owner, session_factory

router = APIRouter(prefix="/backlog", tags=["backlog"], dependencies=[Depends(require_owner)])


@router.get("")
def get_backlog(request: Request, principal: Owner) -> BacklogOut:
    return BacklogOut.of(backlog.get_backlog(session_factory(request), principal))


@router.put("/order")
def set_order(request: Request, principal: Owner, body: OrderIn) -> list[str]:
    """Replace the workspace backlog order; rows not listed append in priority order."""
    return backlog.set_order(session_factory(request), principal, body.item_ids)

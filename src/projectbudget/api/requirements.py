"""``/api/v1/requirements``: create, edit, change intent, consume stock."""

from fastapi import APIRouter, Depends, Request

from projectbudget.api.schemas import (
    ConsumeIn,
    IntentIn,
    MovementOut,
    RequirementIn,
    RequirementOut,
    RequirementPatch,
)
from projectbudget.services import inventory, requirements
from projectbudget.web.auth import Owner, require_owner, session_factory

router = APIRouter(
    prefix="/requirements", tags=["requirements"], dependencies=[Depends(require_owner)]
)


@router.post("", status_code=201)
def add_requirement(request: Request, principal: Owner, body: RequirementIn) -> RequirementOut:
    info = requirements.add_requirement(
        session_factory(request),
        principal,
        requirements.RequirementCreate(
            project_id=body.project_id,
            qty=body.qty,
            priority=body.priority,
            catalog_item_id=body.catalog_item_id,
            item_text=body.item_text,
            unit=body.unit,
            sharing=body.sharing,
            notes=body.notes,
        ),
    )
    return RequirementOut.of(info)


@router.get("/{requirement_id}")
def get_requirement(request: Request, principal: Owner, requirement_id: str) -> RequirementOut:
    info = requirements.get_requirement(session_factory(request), principal, requirement_id)
    return RequirementOut.of(info)


@router.patch("/{requirement_id}")
def update_requirement(
    request: Request, principal: Owner, requirement_id: str, body: RequirementPatch
) -> RequirementOut:
    info = requirements.update_requirement(
        session_factory(request),
        principal,
        requirement_id,
        requirements.RequirementUpdate(
            qty=body.qty,
            priority=body.priority,
            sort_order=body.sort_order,
            sharing=body.sharing,
            notes=body.notes,
        ),
    )
    return RequirementOut.of(info)


@router.post("/{requirement_id}/intent")
def set_intent(
    request: Request, principal: Owner, requirement_id: str, body: IntentIn
) -> RequirementOut:
    info = requirements.set_intent(
        session_factory(request),
        principal,
        requirement_id,
        body.intent,
        archive_reason=body.archive_reason,
    )
    return RequirementOut.of(info)


@router.post("/{requirement_id}/consume", status_code=201)
def consume(
    request: Request, principal: Owner, requirement_id: str, body: ConsumeIn
) -> MovementOut:
    info = inventory.consume(
        session_factory(request),
        principal,
        requirement_id,
        body.qty,
        occurred_on=body.occurred_on,
        note=body.note,
    )
    return MovementOut.of(info)

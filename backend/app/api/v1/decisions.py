"""Work-item decision APIs with explicit, profile-bound execution authority.

Ordinary decisions never mint execution authority.  Work items whose server
profile requires authority must use ``authorized-decisions`` for approval; the
command records the durable Decision and mints a distinct exact-scope
ExecutionAuthorization in the same transaction before the worker can observe
the decision outbox event.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_session, require_roles
from app.core.errors import NotFoundError, ValidationError
from app.models.workflow import WorkItem, WorkItemDecision
from app.schemas.base import IDEMPOTENCY_KEY_HEADER
from app.schemas.commands import WorkItemDecisionSubmit
from app.schemas.events import ROLES
from app.services.commands import canonical_hash
from app.services.responsibility_execution import (
    authorization_profile_for_work_item,
    issue_work_item_execution_authorization,
)
from app.services.work_items import list_work_items
from app.services.workflows import (
    check_idempotency,
    complete_idempotency,
    open_idempotency,
    submit_decision,
)

router = APIRouter(prefix="/v1", tags=["decisions"])

AUTHORIZED_DECISION_SCOPE_PREFIX = "authorized-decision:"


class WorkItemDecisionResponse(BaseModel):
    """Result of a submitted work item decision."""

    workItemId: uuid.UUID
    status: str
    workflowId: uuid.UUID


class AuthorizedDecisionSubmit(BaseModel):
    """Explicit request to make a Decision and mint separate execution authority.

    ``scope`` is retained for listing-publication compatibility. Return
    financial scope and allowed operations are always computed by the server
    from the work item and current ReturnCase facts.
    """

    decision: Literal["approve"] = "approve"
    reason: str | None = Field(default=None, max_length=2000)
    expectedWorkflowVersion: int | None = Field(default=None, ge=1)
    scope: dict[str, Any] = Field(default_factory=dict)
    expiresAt: dt.datetime | None = None


class AuthorizedDecisionResponse(WorkItemDecisionResponse):
    authorizationId: uuid.UUID
    authorizationProfile: str


def _work_item_or_404(db: Session, work_item_id: uuid.UUID) -> WorkItem:
    item = db.get(WorkItem, work_item_id)
    if item is None:
        raise NotFoundError("work item not found")
    return item


@router.post("/work-items/{work_item_id}/decisions", response_model=WorkItemDecisionResponse)
def submit_work_item_decision(
    work_item_id: uuid.UUID,
    body: WorkItemDecisionSubmit,
    db: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    idempotency_key: Annotated[str, Header(alias=IDEMPOTENCY_KEY_HEADER)],
) -> WorkItemDecisionResponse:
    """Submit an ordinary decision; approval cannot bypass a required profile."""

    item = _work_item_or_404(db, work_item_id)
    profile = authorization_profile_for_work_item(db, item)
    if body.decision == "approve" and profile is not None:
        raise ValidationError(
            f"{profile.name} requires explicit execution authorization; use authorized-decisions"
        )
    result = submit_decision(
        work_item_id=work_item_id,
        actor=user_id,
        decision=body.decision,
        expected_version=body.expectedWorkflowVersion,
        idempotency_key=idempotency_key,
        db=db,
        reason=body.reason,
    )
    return WorkItemDecisionResponse(
        workItemId=result.workItemId,
        status=result.status,
        workflowId=result.workflowId,
    )


@router.post(
    "/work-items/{work_item_id}/authorized-decisions",
    response_model=AuthorizedDecisionResponse,
)
def submit_authorized_work_item_decision(
    work_item_id: uuid.UUID,
    body: AuthorizedDecisionSubmit,
    db: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    idempotency_key: Annotated[str, Header(alias=IDEMPOTENCY_KEY_HEADER)],
) -> AuthorizedDecisionResponse:
    """Record a Decision and mint only the server-selected authority profile."""

    item = _work_item_or_404(db, work_item_id)
    profile = authorization_profile_for_work_item(db, item)
    if profile is None:
        raise ValidationError("work item does not require execution authorization")

    scope = f"{AUTHORIZED_DECISION_SCOPE_PREFIX}{work_item_id}"
    request_hash = canonical_hash(body.model_dump(mode="json"))
    existing = check_idempotency(
        db,
        scope=scope,
        key=idempotency_key,
        request_hash=request_hash,
    )
    if existing is not None:
        return AuthorizedDecisionResponse.model_validate(existing.result_json or {})
    record = open_idempotency(
        db,
        scope=scope,
        key=idempotency_key,
        request_hash=request_hash,
    )

    result = submit_decision(
        work_item_id=work_item_id,
        actor=user_id,
        decision=body.decision,
        expected_version=body.expectedWorkflowVersion,
        idempotency_key=None,
        db=db,
        reason=body.reason,
    )
    decision_row = db.execute(
        select(WorkItemDecision).where(WorkItemDecision.work_item_id == work_item_id)
    ).scalar_one()
    authorization, resolved_profile = issue_work_item_execution_authorization(
        db,
        decision=decision_row,
        item=item,
        actor_user_id=user_id,
        listing_scope=body.scope,
        expires_at=body.expiresAt,
    )
    response = AuthorizedDecisionResponse(
        workItemId=result.workItemId,
        status=result.status,
        workflowId=result.workflowId,
        authorizationId=authorization.id,
        authorizationProfile=resolved_profile.name,
    )
    complete_idempotency(record, result=response.model_dump(mode="json"))
    db.flush()
    return response


@router.get("/work-items")
def list_pending_work_items(
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles(*ROLES))],
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List work items (approval inbox) for the operations console."""
    return list_work_items(db, status=status, limit=limit, offset=offset)

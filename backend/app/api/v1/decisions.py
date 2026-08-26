"""Work-item decision APIs, including the explicit authorization pilot.

Ordinary decisions continue to route through the WP4 ``submit_decision``
facade.  The separate ``authorized-decisions`` endpoint is intentionally a
different command: it records the durable Decision and, only because the
caller explicitly requested it, mints a distinct exact-scope
``ExecutionAuthorization`` in the same database transaction before the DBOS
worker can observe the decision outbox event.
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
from app.models.workflow import WorkItem, WorkItemDecision
from app.schemas.base import IDEMPOTENCY_KEY_HEADER
from app.schemas.commands import WorkItemDecisionSubmit
from app.schemas.events import ROLES
from app.services.commands import canonical_hash
from app.services.responsibility_execution import issue_listing_execution_authorization
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
    """Explicit request to make a Decision and mint separate execution authority."""

    decision: Literal["approve"] = "approve"
    reason: str | None = Field(default=None, max_length=2000)
    expectedWorkflowVersion: int | None = Field(default=None, ge=1)
    scope: dict[str, Any] = Field(default_factory=dict)
    expiresAt: dt.datetime | None = None


class AuthorizedDecisionResponse(WorkItemDecisionResponse):
    authorizationId: uuid.UUID


@router.post("/work-items/{work_item_id}/decisions", response_model=WorkItemDecisionResponse)
def submit_work_item_decision(
    work_item_id: uuid.UUID,
    body: WorkItemDecisionSubmit,
    db: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    idempotency_key: Annotated[str, Header(alias=IDEMPOTENCY_KEY_HEADER)],
) -> WorkItemDecisionResponse:
    """Submit an ordinary decision; this never creates execution authority."""
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
    """Explicit listing-publication pilot: Decision + separate Authorization.

    The combined idempotency record hashes the full authorization request, so
    changing scope/expiry under the same key is a conflict rather than a way
    to mint a second authority on a replayed decision.
    """

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
    item = db.get(WorkItem, work_item_id)
    if item is None:
        raise RuntimeError("decision committed without its work item")
    decision_row = db.execute(
        select(WorkItemDecision).where(WorkItemDecision.work_item_id == work_item_id)
    ).scalar_one()
    authorization = issue_listing_execution_authorization(
        db,
        decision=decision_row,
        item=item,
        actor_user_id=user_id,
        scope=body.scope,
        expires_at=body.expiresAt,
    )
    response = AuthorizedDecisionResponse(
        workItemId=result.workItemId,
        status=result.status,
        workflowId=result.workflowId,
        authorizationId=authorization.id,
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

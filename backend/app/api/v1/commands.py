"""POST /v1/* write-command endpoints.

Every endpoint requires an ``Idempotency-Key`` header (missing header is a
422 ``validation_error``) and delegates to the WP4 ``accept_command`` facade
in ``app.services.workflows``, returning 202 Accepted with the
``{workflowId, status, statusUrl}`` result. Command initiation follows the
strict domain RBAC matrix (整改计划 §四.2).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.orm import Session

from app.api.deps import get_correlation_id, get_current_user, get_session, require_roles
from app.core.uuid7 import uuid7
from app.schemas.base import IDEMPOTENCY_KEY_HEADER, AcceptedResponse
from app.schemas.commands import (
    CatalogRevisionCreate,
    ListingPublicationCreate,
    ProcurementCreate,
    ReconciliationCreate,
    ReturnCreate,
)
from app.services.rbac import COMMAND_INITIATE_ROLES
from app.services.workflows import accept_command

router = APIRouter(prefix="/v1", tags=["commands"])


def _accept(
    *,
    command,
    command_type: str,
    key: str,
    actor_user_id: uuid.UUID,
    db: Session,
) -> AcceptedResponse:
    """Dispatch a write command and shape the 202 acceptance response."""
    result = accept_command(
        command=command,
        actor=actor_user_id,
        idempotency_key=key,
        correlation_id=get_correlation_id() or str(uuid7()),
        db=db,
        command_type=command_type,
    )
    # The wire contract always reports the asynchronous acceptance status;
    # the internal run status is observable via GET /v1/workflows/{id}.
    return AcceptedResponse.model_validate(result.model_dump())


@router.post(
    "/catalog-revisions",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_catalog_revision(
    body: CatalogRevisionCreate,
    idempotency_key: Annotated[str, Header(alias=IDEMPOTENCY_KEY_HEADER)],
    db: Annotated[Session, Depends(get_session)],
    actor_user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles("catalog_owner"))],
) -> AcceptedResponse:
    """Submit a catalog revision (Catalog-PIM domain)."""
    return _accept(
        command=body,
        command_type="catalog-revision",
        key=idempotency_key,
        actor_user_id=actor_user_id,
        db=db,
    )


@router.post(
    "/listing-publications",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_listing_publication(
    body: ListingPublicationCreate,
    idempotency_key: Annotated[str, Header(alias=IDEMPOTENCY_KEY_HEADER)],
    db: Annotated[Session, Depends(get_session)],
    actor_user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles("catalog_owner"))],
) -> AcceptedResponse:
    """Create a listing publication plan (first channel: Shopify)."""
    return _accept(
        command=body,
        command_type="listing-publication",
        key=idempotency_key,
        actor_user_id=actor_user_id,
        db=db,
    )


@router.post(
    "/procurements",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_procurement(
    body: ProcurementCreate,
    idempotency_key: Annotated[str, Header(alias=IDEMPOTENCY_KEY_HEADER)],
    db: Annotated[Session, Depends(get_session)],
    actor_user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles("procurement_lead"))],
) -> AcceptedResponse:
    """Create a procurement order (demand_detected)."""
    return _accept(
        command=body,
        command_type="procurement",
        key=idempotency_key,
        actor_user_id=actor_user_id,
        db=db,
    )


@router.post(
    "/returns",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_return(
    body: ReturnCreate,
    idempotency_key: Annotated[str, Header(alias=IDEMPOTENCY_KEY_HEADER)],
    db: Annotated[Session, Depends(get_session)],
    actor_user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles("customer_service"))],
) -> AcceptedResponse:
    """Register a customer return case."""
    return _accept(
        command=body,
        command_type="return",
        key=idempotency_key,
        actor_user_id=actor_user_id,
        db=db,
    )


@router.post(
    "/reconciliations",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_reconciliation(
    body: ReconciliationCreate,
    idempotency_key: Annotated[str, Header(alias=IDEMPOTENCY_KEY_HEADER)],
    db: Annotated[Session, Depends(get_session)],
    actor_user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles(*COMMAND_INITIATE_ROLES["reconciliation"]))],
) -> AcceptedResponse:
    """Trigger a reconciliation run.

    Strict domain RBAC (整改计划 §四.2): only ``accountant`` / ``system_admin``
    may trigger a reconciliation run; diff resolve and reads are enforced on
    the reconciliation router.
    """
    return _accept(
        command=body,
        command_type="reconciliation",
        key=idempotency_key,
        actor_user_id=actor_user_id,
        db=db,
    )

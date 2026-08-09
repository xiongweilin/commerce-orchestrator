"""POST /v1/* write-command endpoints.

Every endpoint requires an ``Idempotency-Key`` header (missing header is a
422 ``validation_error``) and delegates to the shared idempotent command
dispatcher in ``app.services.commands``, returning 202 Accepted with the
dispatcher's ``{workflowId, status, statusUrl}`` result.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.orm import Session

from app.api.deps import get_correlation_id, get_current_user, get_session
from app.core.uuid7 import uuid7
from app.schemas.base import IDEMPOTENCY_KEY_HEADER, AcceptedResponse
from app.schemas.commands import (
    CatalogRevisionCreate,
    ListingPublicationCreate,
    ProcurementCreate,
    ReconciliationCreate,
    ReturnCreate,
)
from app.services.commands import dispatch_command

router = APIRouter(prefix="/v1", tags=["commands"])


def _accept(
    db: Session,
    *,
    scope: str,
    command_type: str,
    payload: dict,
    key: str,
    actor_user_id: uuid.UUID,
) -> AcceptedResponse:
    """Dispatch a write command and shape the 202 acceptance response."""
    result = dispatch_command(
        db,
        scope=scope,
        key=key,
        command_type=command_type,
        payload=payload,
        actor_user_id=actor_user_id,
        correlation_id=get_correlation_id() or str(uuid7()),
    )
    # The wire contract always reports the asynchronous acceptance status;
    # the internal run status is observable via GET /v1/workflows/{id}.
    return AcceptedResponse.model_validate({**result, "status": "accepted"})


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
) -> AcceptedResponse:
    """Submit a catalog revision (Catalog-PIM domain)."""
    return _accept(
        db,
        scope="catalog-revision",
        command_type="catalog-revision",
        payload=body.model_dump(mode="json"),
        key=idempotency_key,
        actor_user_id=actor_user_id,
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
) -> AcceptedResponse:
    """Create a listing publication plan (first channel: Shopify)."""
    return _accept(
        db,
        scope="listing-publication",
        command_type="listing-publication",
        payload=body.model_dump(mode="json"),
        key=idempotency_key,
        actor_user_id=actor_user_id,
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
) -> AcceptedResponse:
    """Create a procurement order (demand_detected)."""
    return _accept(
        db,
        scope="procurement",
        command_type="procurement",
        payload=body.model_dump(mode="json"),
        key=idempotency_key,
        actor_user_id=actor_user_id,
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
) -> AcceptedResponse:
    """Register a customer return case."""
    return _accept(
        db,
        scope="return",
        command_type="return",
        payload=body.model_dump(mode="json"),
        key=idempotency_key,
        actor_user_id=actor_user_id,
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
) -> AcceptedResponse:
    """Trigger a reconciliation run."""
    return _accept(
        db,
        scope="reconciliation",
        command_type="reconciliation",
        payload=body.model_dump(mode="json"),
        key=idempotency_key,
        actor_user_id=actor_user_id,
    )

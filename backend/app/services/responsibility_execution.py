"""Commerce execution-authority specialization for the first publication pilot.

The module binds one explicit :class:`ExecutionAuthorization` to the exact
listing-publication subject that a durable work-item decision concerns.  It
never derives authority from the decision itself: callers must explicitly use
the authorized-decision API, which mints a separate durable authorization.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.core.errors import NotFoundError, ValidationError
from app.models.listing import ListingPublication
from app.models.responsibility import ExecutionAuthorization
from app.models.workflow import WorkItem, WorkItemDecision, WorkflowRun
from app.services.responsibility import authorization_allows_effect, issue_execution_authorization

LISTING_PUBLICATION_WORKFLOW = "listing-publication"
LISTING_PUBLICATION_EFFECT = "shopify.product_publish"
LISTING_SUBJECT_TYPE = "listing_publication"


@dataclass(frozen=True)
class ListingAuthorizationSubject:
    subject_type: str
    subject_ref: str
    subject_version: str
    subject_fingerprint: str
    policy_version: str
    environment_ref: str


def _listing_fingerprint(listing: ListingPublication) -> str:
    """Fingerprint publication-relevant subject content, excluding lifecycle state."""

    payload = {
        "id": str(listing.id),
        "sku": listing.sku,
        "channel": listing.channel,
        "version": listing.version,
        "payload": listing.payload or {},
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def listing_authorization_subject(db, item: WorkItem) -> ListingAuthorizationSubject:
    """Resolve the exact subject/context of a listing-publication approval item."""

    run = db.get(WorkflowRun, item.workflow_id)
    if run is None:
        raise ValidationError("work item references missing workflow")
    if run.workflow_type != LISTING_PUBLICATION_WORKFLOW:
        raise ValidationError("authorized-decision pilot supports listing-publication only")
    payload = item.payload_json or {}
    listing_ref = payload.get("listing_id")
    if not listing_ref:
        raise ValidationError("listing-publication work item is missing listing_id")
    try:
        listing_id = uuid.UUID(str(listing_ref))
    except ValueError as exc:
        raise ValidationError("listing-publication work item has invalid listing_id") from exc
    listing = db.get(ListingPublication, listing_id)
    if listing is None:
        raise NotFoundError("listing publication not found")
    listing_payload = listing.payload or {}
    qualification = listing_payload.get("qualification_context")
    if not isinstance(qualification, dict):
        raise ValidationError("listing publication is missing qualification context")
    policy_version = str(qualification.get("policy_version") or "").strip()
    environment_ref = str(qualification.get("environment_ref") or "").strip()
    if not policy_version or not environment_ref:
        raise ValidationError("listing qualification policy/environment binding is incomplete")
    return ListingAuthorizationSubject(
        subject_type=LISTING_SUBJECT_TYPE,
        subject_ref=str(listing.id),
        subject_version=str(listing.version),
        subject_fingerprint=_listing_fingerprint(listing),
        policy_version=policy_version,
        environment_ref=environment_ref,
    )


def issue_listing_execution_authorization(
    db,
    *,
    decision: WorkItemDecision,
    item: WorkItem,
    actor_user_id: uuid.UUID,
    scope: dict[str, object],
    expires_at: dt.datetime | None = None,
) -> ExecutionAuthorization:
    """Explicitly mint and attach the exact publication authorization."""

    subject = listing_authorization_subject(db, item)
    authorization = issue_execution_authorization(
        db,
        decision_ref=decision.id,
        subject_type=subject.subject_type,
        subject_ref=subject.subject_ref,
        subject_version=subject.subject_version,
        subject_fingerprint=subject.subject_fingerprint,
        target_system="shopify",
        allowed_operations=[LISTING_PUBLICATION_EFFECT],
        scope=dict(scope),
        policy_version=subject.policy_version,
        environment_ref=subject.environment_ref,
        issued_by_user_id=actor_user_id,
        expires_at=expires_at,
    )
    # JSON columns do not reliably detect in-place mutation; assign a new dict.
    item.payload_json = {**(item.payload_json or {}), "authorization_ref": str(authorization.id)}
    db.flush()
    return authorization


def resolve_effect_authorization(
    db,
    *,
    workflow_ref: uuid.UUID | None,
    operation: str,
) -> ExecutionAuthorization | None:
    """Resolve and revalidate an explicit authorization for a planned effect.

    Only the listing-publication pilot is authority-gated in this migration.
    Other workflow types retain their existing behavior while still receiving
    ``workflow_ref`` provenance on newly recorded effects.
    """

    if workflow_ref is None:
        return None
    run = db.get(WorkflowRun, workflow_ref)
    if run is None:
        raise ValidationError("effect workflow_ref does not resolve to a workflow")
    if run.workflow_type != LISTING_PUBLICATION_WORKFLOW:
        return None
    if operation != LISTING_PUBLICATION_EFFECT:
        return None

    items = list(
        db.execute(
            select(WorkItem)
            .where(WorkItem.workflow_id == run.id)
            .order_by(WorkItem.created_at.desc())
        ).scalars()
    )
    item = next(
        (candidate for candidate in items if (candidate.payload_json or {}).get("listing_id")),
        None,
    )
    if item is None:
        raise ValidationError("listing publication requires its approval work item")
    auth_ref = (item.payload_json or {}).get("authorization_ref")
    if not auth_ref:
        raise ValidationError(
            "listing publication requires explicit ExecutionAuthorization; Decision alone is insufficient"
        )
    try:
        authorization_id = uuid.UUID(str(auth_ref))
    except ValueError as exc:
        raise ValidationError("work item authorization_ref is invalid") from exc
    authorization = db.get(ExecutionAuthorization, authorization_id)
    if authorization is None:
        raise ValidationError("work item references missing ExecutionAuthorization")

    subject = listing_authorization_subject(db, item)
    if authorization.workflow_ref != run.id:
        raise ValidationError("ExecutionAuthorization belongs to a different workflow")
    if authorization.policy_version != subject.policy_version:
        raise ValidationError("ExecutionAuthorization policy version no longer matches current subject")
    if not authorization_allows_effect(
        authorization,
        operation=operation,
        subject_type=subject.subject_type,
        subject_ref=subject.subject_ref,
        subject_version=subject.subject_version,
        subject_fingerprint=subject.subject_fingerprint,
        environment_ref=subject.environment_ref,
    ):
        raise ValidationError("ExecutionAuthorization is absent, stale, expired, revoked, or rebound")
    return authorization


__all__ = [
    "LISTING_PUBLICATION_EFFECT",
    "LISTING_PUBLICATION_WORKFLOW",
    "ListingAuthorizationSubject",
    "issue_listing_execution_authorization",
    "listing_authorization_subject",
    "resolve_effect_authorization",
]

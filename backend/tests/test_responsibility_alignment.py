from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.errors import ValidationError
from app.models.effect import EffectStatus
from app.models.effect_realization import EffectRealizationAssessment, EffectRealizationStatus
from app.models.listing import ListingPublication, ListingStatus
from app.models.responsibility import ConfirmedOutcome, ResponsibilityBinding
from app.models.workflow import (
    WorkflowRun,
    WorkflowRunStatus,
    WorkItem,
    WorkItemDecision,
    WorkItemDecisionType,
    WorkItemKind,
    WorkItemStatus,
)
from app.responsibility.store import CommerceResponsibilityStore
from app.services.effect_ledger import mark_dispatched, record_effect
from app.services.responsibility import (
    confirm_effect_outcome,
    open_responsibility_obligation,
    workflow_responsibility_view,
)
from app.services.responsibility_execution import issue_listing_execution_authorization
from portable_runtime.public_contracts.catalog import contract_catalog
from portable_runtime.public_contracts.experience import evaluate_experience_use_contract
from portable_runtime.public_contracts.models import ExperienceUseRequirementV1
from portable_runtime.records.knowledge import KnowledgeProjection


def _listing_workflow(db, user_id):
    run = WorkflowRun(
        workflow_type="listing-publication",
        workflow_version=2,
        orchestration_engine="dbos",
        initiated_by_user_id=user_id,
        status=WorkflowRunStatus.AWAITING_APPROVAL,
        input_json={},
    )
    db.add(run)
    db.flush()
    listing = ListingPublication(
        sku="SKU-R",
        channel="shopify",
        status=ListingStatus.PENDING_APPROVAL,
        payload={
            "catalog_revision_id": str(uuid.uuid4()),
            "qualification_context": {
                "purpose": "publish",
                "policy_version": "policy-r1",
                "adapter_version": "shopify-r1",
                "environment_ref": "prod-r1",
            },
        },
    )
    db.add(listing)
    db.flush()
    item = WorkItem(
        workflow_id=run.id,
        kind=WorkItemKind.APPROVAL,
        title="Approve listing",
        required_roles=["catalog_owner"],
        payload_json={"listing_id": str(listing.id), "next_step": "approve"},
        status=WorkItemStatus.APPROVED,
        expected_version=run.version,
    )
    db.add(item)
    db.flush()
    decision = WorkItemDecision(
        work_item_id=item.id,
        decision=WorkItemDecisionType.APPROVE,
        user_id=user_id,
        submitted_version=run.version,
    )
    db.add(decision)
    db.flush()
    return run, listing, item, decision


def test_decision_is_not_authorization_and_dispatch_revalidates(db, make_user):
    user_id = make_user(["catalog_owner"])
    run, _listing, item, decision = _listing_workflow(db, user_id)

    with pytest.raises(ValidationError, match="Decision alone is insufficient"):
        record_effect(
            db,
            target_system="shopify",
            operation="product_publish",
            approval_ref=run.id,
            idempotency_key="listing:no-auth",
        )

    authorization = issue_listing_execution_authorization(
        db,
        decision=decision,
        item=item,
        actor_user_id=user_id,
        scope={"channel": "shopify", "purpose": "publish"},
    )
    effect = record_effect(
        db,
        target_system="shopify",
        operation="product_publish",
        approval_ref=run.id,
        idempotency_key="listing:authorized",
    )
    assert effect.workflow_ref == run.id
    assert effect.authorization_ref == authorization.id
    assert effect.approval_ref == run.id  # legacy provenance only

    authorization.revoked_at = authorization.issued_at
    db.flush()
    with pytest.raises(ValidationError, match="expired, revoked, or rebound"):
        mark_dispatched(db, effect.intent_id)


def test_effect_success_is_not_confirmed_outcome(db, make_user):
    user_id = make_user(["system_admin"])
    effect = record_effect(
        db,
        target_system="odoo",
        operation="product_update",
        idempotency_key="outcome:separation",
    )
    effect.status = EffectStatus.SUCCEEDED
    db.flush()
    assert db.execute(select(ConfirmedOutcome)).scalars().first() is None

    unverified = EffectRealizationAssessment(
        effect_id=effect.id,
        realization_status=EffectRealizationStatus.UNVERIFIED,
        evidence_refs=["evidence:readback"],
        assessed_by_user_id=user_id,
    )
    db.add(unverified)
    db.flush()
    with pytest.raises(ValidationError, match="requires VERIFIED"):
        confirm_effect_outcome(
            db,
            effect_id=effect.id,
            realization_assessment_id=unverified.id,
            outcome_type="catalog_updated",
            confirmed_by_user_id=user_id,
        )

    verified = EffectRealizationAssessment(
        effect_id=effect.id,
        realization_status=EffectRealizationStatus.VERIFIED,
        evidence_refs=["evidence:readback"],
        assessed_by_user_id=user_id,
    )
    db.add(verified)
    db.flush()
    outcome = confirm_effect_outcome(
        db,
        effect_id=effect.id,
        realization_assessment_id=verified.id,
        outcome_type="catalog_updated",
        confirmed_by_user_id=user_id,
    )
    assert outcome.effect_id == effect.id
    assert outcome.evidence_refs == ["evidence:readback"]


def test_workflow_inspector_isolates_sidecars(db, make_user):
    user_id = make_user(["compliance"])
    first = WorkflowRun(
        workflow_type="reconciliation",
        workflow_version=2,
        orchestration_engine="dbos",
        initiated_by_user_id=user_id,
        status=WorkflowRunStatus.RUNNING,
    )
    second = WorkflowRun(
        workflow_type="reconciliation",
        workflow_version=2,
        orchestration_engine="dbos",
        initiated_by_user_id=user_id,
        status=WorkflowRunStatus.RUNNING,
    )
    db.add_all([first, second])
    db.flush()
    db.add_all(
        [
            ResponsibilityBinding(
                workflow_ref=first.id,
                subject_type="listing",
                subject_ref="listing:first",
                subject_version="1",
                judgment_ref="judgment:first",
                historical_use_ref="historical:first",
                requirement_digest="a" * 64,
                snapshot_digest="b" * 64,
            ),
            ResponsibilityBinding(
                workflow_ref=second.id,
                subject_type="listing",
                subject_ref="listing:second",
                subject_version="1",
                judgment_ref="judgment:second",
                historical_use_ref="historical:second",
                requirement_digest="c" * 64,
                snapshot_digest="d" * 64,
            ),
        ]
    )
    open_responsibility_obligation(
        db,
        workflow_ref=first.id,
        subject_ref="listing:first",
        source_kind="reconciliation_resolution",
        source_ref="resolution:first",
        reason="revalidate scoped experience",
        scope={"channel": "shopify"},
        projection_refs=["projection:first"],
        recorded_by_user_id=user_id,
    )
    open_responsibility_obligation(
        db,
        workflow_ref=second.id,
        subject_ref="listing:second",
        source_kind="reconciliation_resolution",
        source_ref="resolution:second",
        reason="other workflow",
        scope={"channel": "shopify"},
        projection_refs=["projection:second"],
        recorded_by_user_id=user_id,
    )
    db.flush()

    view = workflow_responsibility_view(db, first.id)
    assert [row["subjectRef"] for row in view["historical"]] == ["listing:first"]
    assert [row["subjectRef"] for row in view["openResponsibility"]] == ["listing:first"]
    assert view["authorityBearing"] is False


def test_portable_public_contract_oracle_is_the_experience_reference(db):
    assert contract_catalog()["owner"] == "portable-runtime/contracts"
    store = CommerceResponsibilityStore(db)
    projection = KnowledgeProjection(
        id="projection_candidate",
        title="candidate experience",
        lifecycle_status="candidate",
    )
    store.save_knowledge_projection(projection)
    result = evaluate_experience_use_contract(
        store,
        ExperienceUseRequirementV1(
            projection_refs=[projection.id],
            use_scope={"purpose": "publish"},
            subject_version_refs=["listing:v1"],
            environment_bindings={"commerce": "test"},
            use_context={"workflow": "listing-publication"},
        ),
    )
    assert result.status in {"not-applicable", "allowed", "blocked", "stale", "unavailable"}
    assert len(result.requirement_digest) == 64
    assert len(result.snapshot_digest) == 64

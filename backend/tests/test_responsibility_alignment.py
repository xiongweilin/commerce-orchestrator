from __future__ import annotations

import uuid

import pytest
from portable_runtime.public_contracts.catalog import contract_catalog
from portable_runtime.public_contracts.experience import (
    commit_historical_experience_use_contract,
    evaluate_experience_use_contract,
)
from portable_runtime.public_contracts.models import (
    ExperienceUseRequirementV1,
    HistoricalExperienceUseCommitV1,
)
from portable_runtime.records.authorization import create_grant_for_approval
from portable_runtime.records.knowledge import KnowledgeProjection
from portable_runtime.records.models import Assertion, ChangeObjectRecord, EvidenceArtifact
from portable_runtime.records.relations import RecordRelation
from sqlalchemy import select

from app.core.errors import ValidationError
from app.models.catalog import (
    CatalogCandidateStatus,
    CatalogChangeCandidate,
    CatalogRevision,
    CatalogRevisionStatus,
)
from app.models.effect import EffectStatus
from app.models.effect_realization import EffectRealizationAssessment, EffectRealizationStatus
from app.models.listing import ListingPublication, ListingStatus
from app.models.publication_qualification import PublicationQualificationStatus
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
from app.services.publication_qualification import append_publication_qualification_assessment
from app.services.responsibility import (
    confirm_effect_outcome,
    open_responsibility_obligation,
    workflow_responsibility_view,
)
from app.services.responsibility_execution import issue_listing_execution_authorization


def _listing_workflow(db, user_id, *, experience_derived: bool = False):
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

    candidate = None
    if experience_derived:
        candidate = CatalogChangeCandidate(
            source_refs=["feedback:case-1"],
            model_id="catalog-assistant-r1",
            evidence={"refs": ["feedback:case-1"]},
            proposal_json={"title": "Derived title"},
            status=CatalogCandidateStatus.FROZEN,
        )
        db.add(candidate)
        db.flush()

    revision = CatalogRevision(
        candidate_id=None if candidate is None else candidate.id,
        sku="SKU-R",
        title="Responsibility listing",
        status=CatalogRevisionStatus.APPROVED,
        proposed={"title": "Responsibility listing"},
    )
    db.add(revision)
    db.flush()

    qualification_context = {
        "purpose": "publish",
        "policy_version": "policy-r1",
        "adapter_version": "shopify-r1",
        "environment_ref": "prod-r1",
    }
    append_publication_qualification_assessment(
        db,
        catalog_revision_id=revision.id,
        channel="shopify",
        purpose="publish",
        policy_version="policy-r1",
        adapter_version="shopify-r1",
        environment_ref="prod-r1",
        assessment_status=PublicationQualificationStatus.QUALIFIED,
        assessed_by_user_id=user_id,
        evidence_refs=["qualification:evidence-r1"],
    )

    listing = ListingPublication(
        sku="SKU-R",
        channel="shopify",
        status=ListingStatus.PENDING_APPROVAL,
        payload={
            "catalog_revision_id": str(revision.id),
            "qualification_context": qualification_context,
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


def _bind_listing_experience(db, run, listing):
    store = CommerceResponsibilityStore(db)
    projection_id = f"projection_listing_{listing.id}"
    claim = Assertion(
        id=f"claim_listing_{listing.id}",
        statement="feedback-derived listing content remains supported",
        epistemic_status="supported",
        lifecycle_status="current",
    )
    epistemic_judgment = Assertion(
        id=f"epistemic_listing_{listing.id}",
        statement="available evidence supports reuse for publication",
        epistemic_status="supported",
        lifecycle_status="current",
        metadata={
            "epistemic_role": "epistemic-judgment",
            "judgment_for_refs": [claim.id],
        },
    )
    evidence = EvidenceArtifact(
        id=f"evidence_listing_{listing.id}",
        kind="feedback-evaluation",
        lifecycle_status="current",
    )
    scope = ChangeObjectRecord(
        id=f"scope_listing_{listing.id}",
        lifecycle_status="draft",
    )
    for record in (claim, epistemic_judgment, evidence, scope):
        store.save_record(record)

    grant = create_grant_for_approval(
        principal_ref="human:knowledge-owner",
        grantee_ref="agent:catalog-evaluator",
        allowed_capabilities=["knowledge.promote"],
        subject_version_refs=[scope.id],
        resource_scope=[projection_id],
        effect_ceiling="write-local",
        ttl_seconds=None,
    )
    store.save_authorization(grant)
    projection = KnowledgeProjection(
        id=projection_id,
        title="feedback-derived listing experience",
        lifecycle_status="official",
        current_assertion_refs=[claim.id],
        evidence_summary_refs=[evidence.id],
        epistemic_judgment_refs=[epistemic_judgment.id],
        authorization_refs=[grant.id],
        scope_version_refs=[scope.id],
        validity_scope={"purpose": "publish"},
        environment_bindings={"commerce": "prod-r1"},
        metadata={
            "actor_ref": "agent:catalog-evaluator",
            "resource_ref": projection_id,
            "effect_class": "write-local",
        },
    )
    store.save_knowledge_projection(projection)
    requirement = ExperienceUseRequirementV1(
        projection_refs=[projection.id],
        use_scope={"purpose": "publish", "channel": "shopify"},
        subject_version_refs=[scope.id, f"listing:{listing.id}:v{listing.version}"],
        environment_bindings={"commerce": "prod-r1"},
        use_context={"workflow": "listing-publication"},
    )
    admission = evaluate_experience_use_contract(store, requirement)
    assert admission.status == "allowed"

    judgment = Assertion(
        id=f"judgment_listing_{listing.id}",
        statement="publish this feedback-derived listing",
        epistemic_status="supported",
        lifecycle_status="current",
        metadata={"semantic_role": "task-domain-judgment"},
    )
    historical = commit_historical_experience_use_contract(
        store,
        HistoricalExperienceUseCommitV1(
            judgment=judgment.model_dump(mode="json"),
            requirement=requirement,
            expected_requirement_digest=admission.requirement_digest,
            expected_snapshot_digest=admission.snapshot_digest,
            expected_admission_contract_version=admission.admission_contract_version,
        ),
    )
    binding = ResponsibilityBinding(
        workflow_ref=run.id,
        subject_type="listing_publication",
        subject_ref=str(listing.id),
        subject_version=str(listing.version),
        judgment_ref=historical.judgment_ref,
        historical_use_ref=historical.id,
        requirement_digest=historical.requirement_digest,
        snapshot_digest=historical.snapshot_digest,
    )
    db.add(binding)
    db.flush()
    return store, projection, binding


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


def test_listing_full_experience_history_dispatch_vertical_is_fail_closed(db, make_user):
    user_id = make_user(["catalog_owner"])
    run, listing, item, decision = _listing_workflow(db, user_id, experience_derived=True)
    store, projection, binding = _bind_listing_experience(db, run, listing)
    original_binding = (
        binding.historical_use_ref,
        binding.requirement_digest,
        binding.snapshot_digest,
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
        idempotency_key="listing:experience-vertical",
    )
    assert effect.authorization_ref == authorization.id

    store.save_relation(
        RecordRelation(
            id=f"revalidation_{projection.id}",
            relation_type="requires-revalidation",
            subject_ref=projection.id,
            object_ref=projection.current_assertion_refs[0],
            metadata={"reason": "new feedback requires current-use revalidation"},
        )
    )
    db.flush()
    with pytest.raises(ValidationError, match="current Experience is not eligible"):
        mark_dispatched(db, effect.intent_id)

    db.refresh(binding)
    assert (
        binding.historical_use_ref,
        binding.requirement_digest,
        binding.snapshot_digest,
    ) == original_binding
    assert effect.status is EffectStatus.PLANNED


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

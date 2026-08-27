from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

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
from app.models.responsibility import ResponsibilityBinding
from app.models.returns import ReturnCase, ReturnDisposition, ReturnStatus
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
from app.services.effect_ledger import record_effect
from app.services.publication_qualification import append_publication_qualification_assessment
from app.services.responsibility import (
    confirm_effect_outcome,
    discharge_responsibility_obligation,
    open_responsibility_obligation,
    workflow_responsibility_view,
)
from app.services.responsibility_execution import (
    issue_listing_execution_authorization,
    issue_work_item_execution_authorization,
)


def _return_run_and_case(db, actor: uuid.UUID) -> tuple[WorkflowRun, ReturnCase]:
    run = WorkflowRun(
        workflow_type="return-to-refund",
        workflow_version=2,
        orchestration_engine="dbos",
        initiated_by_user_id=actor,
        status=WorkflowRunStatus.AWAITING_APPROVAL,
        input_json={},
    )
    db.add(run)
    db.flush()
    case = ReturnCase(
        return_ref="RET-C19",
        order_ref="ORDER-C19",
        shopify_order_id="9019",
        customer_ref="customer-c19",
        reason="damaged",
        status=ReturnStatus.DISPOSITION_APPROVED,
        refund_amount=Decimal("88.20"),
        currency="CNY",
        disposition=ReturnDisposition.RESTOCK,
    )
    db.add(case)
    db.flush()
    return run, case


def _return_decision(
    db,
    *,
    run: WorkflowRun,
    case: ReturnCase,
    actor: uuid.UUID,
    step: str,
) -> tuple[WorkItem, WorkItemDecision]:
    role = "accountant" if step == "approve_credit_note" else "finance_approver"
    item = WorkItem(
        workflow_id=run.id,
        kind=WorkItemKind.APPROVAL,
        title=f"C19 {step}",
        required_roles=[role],
        payload_json={"case_id": str(case.id), "next_step": step},
        status=WorkItemStatus.APPROVED,
        expected_version=run.version,
    )
    db.add(item)
    db.flush()
    decision = WorkItemDecision(
        work_item_id=item.id,
        decision=WorkItemDecisionType.APPROVE,
        user_id=actor,
        submitted_version=run.version,
    )
    db.add(decision)
    db.flush()
    return item, decision


def test_return_projection_explains_two_distinct_financial_responsibilities(db, make_user):
    accountant = make_user(["accountant"])
    finance = make_user(["finance_approver"])
    run, case = _return_run_and_case(db, accountant)
    credit_item, credit_decision = _return_decision(
        db,
        run=run,
        case=case,
        actor=accountant,
        step="approve_credit_note",
    )
    refund_item, refund_decision = _return_decision(
        db,
        run=run,
        case=case,
        actor=finance,
        step="approve_refund",
    )
    credit_auth, _ = issue_work_item_execution_authorization(
        db,
        decision=credit_decision,
        item=credit_item,
        actor_user_id=accountant,
    )
    refund_auth, _ = issue_work_item_execution_authorization(
        db,
        decision=refund_decision,
        item=refund_item,
        actor_user_id=finance,
    )
    credit_create = record_effect(
        db,
        target_system="odoo",
        operation="credit_note_create",
        approval_ref=run.id,
        idempotency_key="c19:credit-create",
    )
    credit_validate = record_effect(
        db,
        target_system="odoo",
        operation="credit_note_validate",
        approval_ref=run.id,
        idempotency_key="c19:credit-validate",
    )
    refund = record_effect(
        db,
        target_system="shopify",
        operation="refund_create",
        approval_ref=run.id,
        idempotency_key="c19:refund",
    )

    view = workflow_responsibility_view(db, run.id)
    decisions = {row["id"]: row for row in view["decisions"]}
    authorizations = {row["id"]: row for row in view["authorizations"]}
    effects = {row["effectId"]: row for row in view["execution"]}

    assert decisions[str(credit_decision.id)]["nextStep"] == "approve_credit_note"
    assert decisions[str(credit_decision.id)]["requiredRoles"] == ["accountant"]
    assert decisions[str(credit_decision.id)]["authorizationProfile"] == "return-credit-note-v1"
    assert decisions[str(refund_decision.id)]["nextStep"] == "approve_refund"
    assert decisions[str(refund_decision.id)]["requiredRoles"] == ["finance_approver"]
    assert decisions[str(refund_decision.id)]["authorizationProfile"] == "return-refund-v1"

    credit_projection = authorizations[str(credit_auth.id)]
    refund_projection = authorizations[str(refund_auth.id)]
    assert credit_projection["decisionRef"] == str(credit_decision.id)
    assert credit_projection["targetSystem"] == "odoo"
    assert credit_projection["scope"]["refund_amount"] == "88.20"
    assert credit_projection["scope"]["currency"] == "CNY"
    assert set(credit_projection["allowedOperations"]) == {
        "odoo.credit_note_create",
        "odoo.credit_note_validate",
    }
    assert refund_projection["decisionRef"] == str(refund_decision.id)
    assert refund_projection["targetSystem"] == "shopify"
    assert refund_projection["allowedOperations"] == ["shopify.refund_create"]
    assert credit_projection["id"] != refund_projection["id"]

    assert effects[str(credit_create.id)]["authorizationRef"] == str(credit_auth.id)
    assert effects[str(credit_validate.id)]["authorizationRef"] == str(credit_auth.id)
    assert effects[str(refund.id)]["authorizationRef"] == str(refund_auth.id)
    assert view["authorityBearing"] is False


def test_outcome_edge_and_obligation_lifecycle_are_additive_read_facts(db, make_user):
    actor = make_user(["system_admin"])
    run = WorkflowRun(
        workflow_type="reconciliation",
        workflow_version=2,
        orchestration_engine="dbos",
        initiated_by_user_id=actor,
        status=WorkflowRunStatus.RUNNING,
    )
    db.add(run)
    db.flush()
    effect = record_effect(
        db,
        target_system="odoo",
        operation="product_update",
        approval_ref=run.id,
        idempotency_key="c19:outcome-edge",
    )
    effect.status = EffectStatus.SUCCEEDED
    assessment = EffectRealizationAssessment(
        effect_id=effect.id,
        realization_status=EffectRealizationStatus.VERIFIED,
        evidence_refs=["odoo-readback:c19"],
        assessed_by_user_id=actor,
    )
    db.add(assessment)
    db.flush()
    outcome = confirm_effect_outcome(
        db,
        effect_id=effect.id,
        realization_assessment_id=assessment.id,
        outcome_type="catalog_updated",
        confirmed_by_user_id=actor,
    )
    obligation = open_responsibility_obligation(
        db,
        workflow_ref=run.id,
        subject_ref="listing:c19",
        source_kind="reconciliation_resolution",
        source_ref="resolution:c19",
        reason="revalidate current listing use",
        scope={"channel": "shopify"},
        projection_refs=["projection:c19"],
        recorded_by_user_id=actor,
    )
    discharge_responsibility_obligation(
        db,
        obligation_id=obligation.id,
        actor_user_id=actor,
    )

    view = workflow_responsibility_view(db, run.id)
    projected_outcome = next(row for row in view["confirmedOutcomes"] if row["id"] == str(outcome.id))
    assert projected_outcome["effectId"] == str(effect.id)
    assert projected_outcome["realizationAssessmentRef"] == str(assessment.id)
    assert projected_outcome["confirmedByUserId"] == str(actor)
    assert projected_outcome["confirmedAt"]

    history = next(row for row in view["responsibilityObligations"] if row["id"] == str(obligation.id))
    assert history["status"] == "discharged"
    assert history["dischargedAt"] is not None
    assert history["recordedByUserId"] == str(actor)
    assert all(row["id"] != str(obligation.id) for row in view["openResponsibility"])


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
    candidate = CatalogChangeCandidate(
        source_refs=["feedback:c19"],
        model_id="catalog-assistant-c19",
        evidence={"refs": ["feedback:c19"]},
        proposal_json={"title": "C19 derived title"},
        status=CatalogCandidateStatus.FROZEN,
    )
    db.add(candidate)
    db.flush()
    revision = CatalogRevision(
        candidate_id=candidate.id,
        sku="SKU-C19",
        title="C19 listing",
        status=CatalogRevisionStatus.APPROVED,
        proposed={"title": "C19 listing"},
    )
    db.add(revision)
    db.flush()
    qualification_context = {
        "purpose": "publish",
        "policy_version": "policy-c19",
        "adapter_version": "shopify-c19",
        "environment_ref": "prod-c19",
    }
    append_publication_qualification_assessment(
        db,
        catalog_revision_id=revision.id,
        channel="shopify",
        purpose="publish",
        policy_version="policy-c19",
        adapter_version="shopify-c19",
        environment_ref="prod-c19",
        assessment_status=PublicationQualificationStatus.QUALIFIED,
        assessed_by_user_id=user_id,
        evidence_refs=["qualification:c19"],
    )
    listing = ListingPublication(
        sku="SKU-C19",
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
        title="Approve C19 listing",
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
    projection_id = f"projection_c19_{listing.id}"
    claim = Assertion(
        id=f"claim_c19_{listing.id}",
        statement="C19 listing experience remains supported",
        epistemic_status="supported",
        lifecycle_status="current",
    )
    epistemic_judgment = Assertion(
        id=f"epistemic_c19_{listing.id}",
        statement="C19 evidence supports publication reuse",
        epistemic_status="supported",
        lifecycle_status="current",
        metadata={
            "epistemic_role": "epistemic-judgment",
            "judgment_for_refs": [claim.id],
        },
    )
    evidence = EvidenceArtifact(
        id=f"evidence_c19_{listing.id}",
        kind="feedback-evaluation",
        lifecycle_status="current",
    )
    scope = ChangeObjectRecord(
        id=f"scope_c19_{listing.id}",
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
        title="C19 listing experience",
        lifecycle_status="official",
        current_assertion_refs=[claim.id],
        evidence_summary_refs=[evidence.id],
        epistemic_judgment_refs=[epistemic_judgment.id],
        authorization_refs=[grant.id],
        scope_version_refs=[scope.id],
        validity_scope={"purpose": "publish"},
        environment_bindings={"commerce": "prod-c19"},
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
        environment_bindings={"commerce": "prod-c19"},
        use_context={"workflow": "listing-publication"},
    )
    admission = evaluate_experience_use_contract(store, requirement)
    assert admission.status == "allowed"
    judgment = Assertion(
        id=f"judgment_c19_{listing.id}",
        statement="publish this C19 listing",
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
    return projection, binding


def test_listing_current_explanation_uses_scope_aware_c17_composition(db, make_user):
    actor = make_user(["catalog_owner"])
    run, listing, item, decision = _listing_workflow(db, actor)
    projection, binding = _bind_listing_experience(db, run, listing)
    issue_listing_execution_authorization(
        db,
        decision=decision,
        item=item,
        actor_user_id=actor,
        scope={"purpose": "publish", "channel": "shopify"},
    )

    baseline = workflow_responsibility_view(db, run.id)
    current = baseline["currentResponsibility"]
    assert current["eligible"] is True
    assert current["status"] == "allowed"
    assert current["portableStatus"] == "allowed"
    assert current["historicalUseRef"] == binding.historical_use_ref
    assert current["requirementDigest"] == binding.requirement_digest
    assert current["applicableObligationRefs"] == []

    unrelated = open_responsibility_obligation(
        db,
        workflow_ref=run.id,
        subject_ref=str(listing.id),
        source_kind="reconciliation_resolution",
        source_ref="resolution:c19:amazon",
        reason="Amazon-only obligation",
        scope={"channel": "amazon"},
        projection_refs=[projection.id],
        recorded_by_user_id=actor,
    )
    still_allowed = workflow_responsibility_view(db, run.id)["currentResponsibility"]
    assert still_allowed["eligible"] is True
    assert str(unrelated.id) not in still_allowed["applicableObligationRefs"]

    matching = open_responsibility_obligation(
        db,
        workflow_ref=run.id,
        subject_ref=str(listing.id),
        source_kind="reconciliation_resolution",
        source_ref="resolution:c19:shopify",
        reason="Shopify publication requires revalidation",
        scope={"channel": "shopify"},
        projection_refs=[projection.id],
        recorded_by_user_id=actor,
    )
    blocked_view = workflow_responsibility_view(db, run.id)
    blocked = blocked_view["currentResponsibility"]
    assert blocked["eligible"] is False
    assert blocked["status"] == "blocked"
    assert blocked["portableStatus"] == "allowed"
    assert blocked["historicalUseRef"] == binding.historical_use_ref
    assert blocked["applicableObligationRefs"] == [str(matching.id)]
    assert blocked_view["historical"][0]["historicalUseRef"] == binding.historical_use_ref

    discharge_responsibility_obligation(
        db,
        obligation_id=matching.id,
        actor_user_id=actor,
    )
    restored = workflow_responsibility_view(db, run.id)
    assert restored["currentResponsibility"]["eligible"] is True
    assert restored["currentResponsibility"]["applicableObligationRefs"] == []
    matching_history = next(
        row
        for row in restored["responsibilityObligations"]
        if row["id"] == str(matching.id)
    )
    assert matching_history["status"] == "discharged"
    assert all(row["id"] != str(matching.id) for row in restored["openResponsibility"])


def test_console_inspector_source_has_no_authority_or_scope_inference() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "console"
        / "components"
        / "ResponsibilityInspector.tsx"
    ).read_text(encoding="utf-8")
    forbidden = (
        "allowedOperations.includes(",
        "projectionRefs.some(",
        "projectionRefs.find(",
        "scope.channel",
        "portableStatus ===",
    )
    for marker in forbidden:
        assert marker not in source

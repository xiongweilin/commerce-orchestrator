from __future__ import annotations

import uuid

from app.models.catalog import CatalogRevision, CatalogRevisionStatus
from app.models.effect import EffectLedgerEntry, EffectStatus
from app.models.listing import ListingPublication, ListingStatus
from app.models.workflow import (
    WorkflowRun,
    WorkflowRunStatus,
    WorkItem,
    WorkItemKind,
    WorkItemStatus,
)
from app.services.publication_qualification import append_publication_qualification_assessment
from app.services.realization_resolution import append_effect_realization_assessment
from app.services.responsibility import confirm_effect_outcome
from app.services.workflow_completion import assess_workflow_completion, completion_satisfied


def _ready_listing_run(db, actor: uuid.UUID):
    context = {
        "purpose": "publish",
        "policy_version": "policy-c16",
        "adapter_version": "shopify-c16",
        "environment_ref": "prod-c16",
    }
    run = WorkflowRun(
        workflow_type="listing-publication",
        workflow_version=2,
        orchestration_engine="dbos",
        status=WorkflowRunStatus.RUNNING,
        input_json={},
    )
    db.add(run)
    db.flush()

    revision = CatalogRevision(
        sku="SKU-C16",
        title="C16 listing",
        status=CatalogRevisionStatus.APPROVED,
        proposed={"title": "C16 listing"},
    )
    db.add(revision)
    db.flush()
    append_publication_qualification_assessment(
        db,
        catalog_revision_id=revision.id,
        channel="shopify",
        purpose=context["purpose"],
        policy_version=context["policy_version"],
        adapter_version=context["adapter_version"],
        environment_ref=context["environment_ref"],
        assessment_status="qualified",
        assessed_by_user_id=actor,
        evidence_refs=["shopify-readback:qualification:c16"],
    )

    listing = ListingPublication(
        sku=revision.sku,
        channel="shopify",
        status=ListingStatus.ACTIVE,
        shopify_product_gid="gid://shopify/Product/1616",
        remote_reference="gid://shopify/Product/1616",
        payload={
            "catalog_revision_id": str(revision.id),
            "qualification_context": context,
        },
    )
    db.add(listing)
    db.flush()
    db.add(
        WorkItem(
            workflow_id=run.id,
            kind=WorkItemKind.APPROVAL,
            title="settled C16 listing approval",
            payload_json={"listing_id": str(listing.id)},
            status=WorkItemStatus.APPROVED,
            expected_version=run.version,
        )
    )
    effect = EffectLedgerEntry(
        intent_id=uuid.uuid4(),
        target_system="shopify",
        operation="product_publish",
        approval_ref=run.id,
        status=EffectStatus.SUCCEEDED,
        remote_reference=listing.shopify_product_gid,
    )
    db.add(effect)
    db.flush()
    realization = append_effect_realization_assessment(
        db,
        effect_id=effect.id,
        realization_status="verified",
        assessed_by_user_id=actor,
        evidence_refs=["shopify-readback:product:1616"],
    )
    return run, effect, realization


def test_listing_verified_realization_still_needs_confirmed_outcome(db, make_user):
    actor = make_user(["system_admin"])
    run, effect, realization = _ready_listing_run(db, actor)

    before = assess_workflow_completion(db, run)
    requirement = f"effect:{effect.intent_id}:confirmed_outcome"
    assert f"effect:{effect.intent_id}:realization_verified" in before.covered_requirements
    assert requirement in before.missing_requirements
    assert not before.confirmed_outcome_refs
    assert not completion_satisfied(before)

    outcome = confirm_effect_outcome(
        db,
        effect_id=effect.id,
        realization_assessment_id=realization.id,
        outcome_type="listing_published",
        confirmed_by_user_id=actor,
    )
    after = assess_workflow_completion(db, run)
    assert requirement in after.covered_requirements
    assert f"confirmed_outcome:{outcome.id}" in after.confirmed_outcome_refs
    assert completion_satisfied(after)


def test_confirmed_outcome_does_not_replace_current_realization(db, make_user):
    actor = make_user(["system_admin"])
    run, effect, realization = _ready_listing_run(db, actor)
    confirm_effect_outcome(
        db,
        effect_id=effect.id,
        realization_assessment_id=realization.id,
        outcome_type="listing_published",
        confirmed_by_user_id=actor,
    )
    append_effect_realization_assessment(
        db,
        effect_id=effect.id,
        realization_status="unverified",
        assessed_by_user_id=actor,
        evidence_refs=[],
    )

    assessment = assess_workflow_completion(db, run)
    assert f"effect:{effect.intent_id}:confirmed_outcome" in assessment.covered_requirements
    assert f"effect:{effect.intent_id}:realization_verified" in assessment.missing_requirements
    assert not completion_satisfied(assessment)

"""Commerce-native bounded workflow completion assessment.

The gate proves only the finite requirements declared for one workflow kind.
It never claims universal completeness: ``coverage_claim`` is permanently
``declared-scope-only``.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.catalog import CatalogRevision, CatalogRevisionStatus
from app.models.effect import EffectLedgerEntry
from app.models.effect_realization import EffectRealizationStatus
from app.models.listing import ListingPublication, ListingStatus
from app.models.order import SalesOrder, SalesOrderStatus
from app.models.procurement import ProcurementOrder, ProcurementStatus
from app.models.publication_qualification import PublicationQualificationStatus
from app.models.reconciliation import (
    ReconciliationDiff,
    ReconciliationDiffStatus,
    ReconciliationRun,
    ReconciliationRunStatus,
)
from app.models.returns import ReturnCase, ReturnStatus
from app.models.workflow import WorkflowRun, WorkItem, WorkItemStatus
from app.services.publication_qualification import latest_applicable_assessment
from app.services.realization_resolution import latest_effect_realization_assessment

COVERAGE_CLAIM: Literal["declared-scope-only"] = "declared-scope-only"


class WorkflowCompletionContract(BaseModel):
    workflow_kind: str
    domain_terminal: str
    publication_qualification_required: bool = False
    required_effects: bool = True


WORKFLOW_COMPLETION_CONTRACTS: dict[str, WorkflowCompletionContract] = {
    "catalog-revision": WorkflowCompletionContract(
        workflow_kind="catalog-revision",
        domain_terminal="catalog_revision:official",
        publication_qualification_required=True,
    ),
    "listing-publication": WorkflowCompletionContract(
        workflow_kind="listing-publication",
        domain_terminal="listing_publication:active",
        publication_qualification_required=True,
    ),
    "procurement": WorkflowCompletionContract(
        workflow_kind="procurement",
        domain_terminal="procurement_order:closed",
    ),
    "return": WorkflowCompletionContract(
        workflow_kind="return",
        domain_terminal="return_case:closed",
    ),
    "order-to-cash": WorkflowCompletionContract(
        workflow_kind="order-to-cash",
        domain_terminal="sales_order:closed",
    ),
    "return-to-refund": WorkflowCompletionContract(
        workflow_kind="return-to-refund",
        domain_terminal="return_case:closed",
    ),
    "reconciliation": WorkflowCompletionContract(
        workflow_kind="reconciliation",
        domain_terminal="reconciliation_run:finished",
        required_effects=False,
    ),
}


class CompletionAssessment(BaseModel):
    workflow_kind: str
    declared_requirements: list[str] = Field(default_factory=list)
    covered_requirements: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    blocking_reconciliation_refs: list[str] = Field(default_factory=list)
    qualification_refs: list[str] = Field(default_factory=list)
    realization_refs: list[str] = Field(default_factory=list)
    required_effect_refs: list[str] = Field(default_factory=list)
    required_effect_classes: list[str] = Field(default_factory=list)
    coverage_claim: Literal["declared-scope-only"] = COVERAGE_CLAIM


def completion_satisfied(assessment: CompletionAssessment) -> bool:
    return not assessment.missing_requirements and not assessment.blocking_reconciliation_refs


def _items(db, run: WorkflowRun) -> list[WorkItem]:
    return (
        db.execute(
            select(WorkItem)
            .where(WorkItem.workflow_id == run.id)
            .order_by(WorkItem.created_at, WorkItem.id)
        )
        .scalars()
        .all()
    )


def _payload_value(items: list[WorkItem], key: str):
    for item in items:
        value = (item.payload_json or {}).get(key)
        if value is not None:
            return value
    return None


def _uuid(value) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _record_requirement(
    declared: list[str], covered: list[str], missing: list[str], requirement: str, ok: bool
) -> None:
    declared.append(requirement)
    (covered if ok else missing).append(requirement)


def _domain_terminal(db, run: WorkflowRun, items: list[WorkItem]) -> bool:
    kind = run.workflow_type
    if kind == "catalog-revision":
        revision_id = _uuid(_payload_value(items, "revision_id"))
        revision = db.get(CatalogRevision, revision_id) if revision_id else None
        return bool(revision and revision.status is CatalogRevisionStatus.OFFICIAL)
    if kind == "listing-publication":
        listing_id = _uuid(_payload_value(items, "listing_id"))
        listing = db.get(ListingPublication, listing_id) if listing_id else None
        return bool(listing and listing.status is ListingStatus.ACTIVE)
    if kind == "procurement":
        po_id = _uuid(_payload_value(items, "po_id"))
        order = db.get(ProcurementOrder, po_id) if po_id else None
        return bool(order and order.status is ProcurementStatus.CLOSED)
    if kind in {"return", "return-to-refund"}:
        case_id = _uuid(_payload_value(items, "case_id"))
        case = db.get(ReturnCase, case_id) if case_id else None
        return bool(case and case.status is ReturnStatus.CLOSED)
    if kind == "order-to-cash":
        order_ref = _payload_value(items, "order_ref")
        if order_ref is None:
            order_ref = (run.input_json or {}).get("order_ref")
        order = (
            db.execute(select(SalesOrder).where(SalesOrder.order_ref == str(order_ref)))
            .scalars()
            .first()
            if order_ref
            else None
        )
        if order is None:
            entity_id = _uuid((run.input_json or {}).get("entity_id"))
            order = db.get(SalesOrder, entity_id) if entity_id else None
        return bool(order and order.status is SalesOrderStatus.CLOSED)
    if kind == "reconciliation":
        rec_id = _uuid((run.result_json or {}).get("reconciliationRunId"))
        rec_run = db.get(ReconciliationRun, rec_id) if rec_id else None
        return bool(
            rec_run
            and rec_run.status
            in {
                ReconciliationRunStatus.COMPLETED,
                ReconciliationRunStatus.COMPLETED_WITH_DIFFS,
            }
        )
    return False


def _publication_qualification(
    db, run: WorkflowRun, items: list[WorkItem]
) -> tuple[bool, list[str]]:
    payload = run.input_json or {}
    revision: CatalogRevision | None = None
    channel: str | None = None
    context = payload.get("qualification_context")

    if run.workflow_type == "catalog-revision":
        revision_id = _uuid(_payload_value(items, "revision_id"))
        revision = db.get(CatalogRevision, revision_id) if revision_id else None
        channel = "shopify"
    elif run.workflow_type == "listing-publication":
        listing_id = _uuid(_payload_value(items, "listing_id"))
        listing = db.get(ListingPublication, listing_id) if listing_id else None
        listing_payload = (listing.payload or {}) if listing else {}
        revision_id = _uuid(payload.get("catalog_revision_id") or listing_payload.get("catalog_revision_id"))
        revision = db.get(CatalogRevision, revision_id) if revision_id else None
        channel = (listing.channel if listing else None) or str(payload.get("channel") or "")
        if not isinstance(context, dict):
            context = listing_payload.get("qualification_context")
    else:
        return True, []

    if revision is None or not channel or not isinstance(context, dict):
        return False, []
    required = ("purpose", "policy_version", "adapter_version", "environment_ref")
    if any(not str(context.get(key) or "").strip() for key in required):
        return False, []
    assessment = latest_applicable_assessment(
        db,
        revision=revision,
        channel=channel,
        purpose=str(context["purpose"]),
        policy_version=str(context["policy_version"]),
        adapter_version=str(context["adapter_version"]),
        environment_ref=str(context["environment_ref"]),
    )
    if assessment is None or assessment.assessment_status is not PublicationQualificationStatus.QUALIFIED:
        return False, []
    return True, [f"publication_qualification:{assessment.id}"]


def assess_workflow_completion(db, run: WorkflowRun) -> CompletionAssessment:
    """Assess only the declared finite completion scope for ``run``."""
    declared: list[str] = []
    covered: list[str] = []
    missing: list[str] = []
    qualification_refs: list[str] = []
    realization_refs: list[str] = []
    required_effect_refs: list[str] = []
    required_effect_classes: list[str] = []
    blocking_refs: list[str] = []

    contract = WORKFLOW_COMPLETION_CONTRACTS.get(run.workflow_type)
    if contract is None:
        _record_requirement(declared, covered, missing, "workflow_contract:known", False)
        return CompletionAssessment(
            workflow_kind=run.workflow_type,
            declared_requirements=declared,
            covered_requirements=covered,
            missing_requirements=missing,
        )

    items = _items(db, run)
    _record_requirement(
        declared,
        covered,
        missing,
        "work_items:settled",
        not any(item.status is WorkItemStatus.PENDING for item in items),
    )
    _record_requirement(
        declared,
        covered,
        missing,
        f"domain_terminal:{contract.domain_terminal}",
        _domain_terminal(db, run, items),
    )

    if contract.publication_qualification_required:
        ok, refs = _publication_qualification(db, run, items)
        qualification_refs.extend(refs)
        _record_requirement(
            declared,
            covered,
            missing,
            "publication_qualification:current",
            ok,
        )

    effects = (
        db.execute(
            select(EffectLedgerEntry)
            .where(EffectLedgerEntry.approval_ref == run.id)
            .order_by(EffectLedgerEntry.id)
        )
        .scalars()
        .all()
    )
    if contract.required_effects:
        _record_requirement(declared, covered, missing, "effects:present", bool(effects))

    for effect in effects:
        effect_ref = f"effect:{effect.intent_id}"
        operation = f"{effect.target_system}.{effect.operation}"
        required_effect_refs.append(effect_ref)
        required_effect_classes.append(operation)
        requirement = f"{effect_ref}:realization_verified"
        latest = latest_effect_realization_assessment(db, effect.id)
        verified = bool(latest and latest.realization_status is EffectRealizationStatus.VERIFIED)
        if verified and latest is not None:
            realization_refs.append(f"effect_realization:{latest.id}")
        _record_requirement(declared, covered, missing, requirement, verified)

    blocker_requirement = "reconciliation:no_blocking_diff_for_required_effects"
    if effects:
        intent_ids = [str(effect.intent_id) for effect in effects]
        blockers = (
            db.execute(
                select(ReconciliationDiff).where(
                    ReconciliationDiff.domain == "effect",
                    ReconciliationDiff.entity_id.in_(intent_ids),
                    ReconciliationDiff.status.in_(
                        [
                            ReconciliationDiffStatus.OPEN,
                            ReconciliationDiffStatus.MANUAL_RECONCILIATION,
                        ]
                    ),
                )
            )
            .scalars()
            .all()
        )
        blocking_refs.extend(f"reconciliation_diff:{diff.id}" for diff in blockers)
    _record_requirement(
        declared,
        covered,
        missing,
        blocker_requirement,
        not blocking_refs,
    )

    return CompletionAssessment(
        workflow_kind=run.workflow_type,
        declared_requirements=declared,
        covered_requirements=covered,
        missing_requirements=missing,
        blocking_reconciliation_refs=blocking_refs,
        qualification_refs=qualification_refs,
        realization_refs=realization_refs,
        required_effect_refs=required_effect_refs,
        required_effect_classes=required_effect_classes,
    )


__all__ = [
    "COVERAGE_CLAIM",
    "CompletionAssessment",
    "WORKFLOW_COMPLETION_CONTRACTS",
    "WorkflowCompletionContract",
    "assess_workflow_completion",
    "completion_satisfied",
]

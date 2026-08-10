"""Reconciliation: compare the effect ledger / local state with connectors.

Connectors are injected as callables: ``connector(domain) -> list[dict]`` where
each remote row has at least ``{"entity_type", "entity_id", "state"}``.
Differences are written as :class:`ReconciliationDiff` rows.  Diffs always go
``OPEN -> MANUAL_RECONCILIATION``; they are never auto-resolved (auto-smoothing
is forbidden).  Only a human can move a diff to ``RESOLVED``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import func, select

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.time import utc_now
from app.models.catalog import CatalogRevision
from app.models.effect import EffectLedgerEntry
from app.models.listing import ListingPublication
from app.models.order import SalesOrder
from app.models.procurement import ProcurementOrder
from app.models.reconciliation import (
    ReconciliationDiff,
    ReconciliationDiffStatus,
    ReconciliationRun,
    ReconciliationRunStatus,
)
from app.models.returns import ReturnCase
from app.services.audit import record_audit

logger = get_logger("commerce.reconciliation")

SUPPORTED_DOMAINS = frozenset(
    ("effect", "order", "procurement", "return", "catalog", "listing", "shopify")
)


def _local_rows(db, domain: str) -> list[dict[str, Any]]:
    """Local source-of-truth snapshot for a reconciliation domain."""
    if domain == "effect":
        rows = db.execute(select(EffectLedgerEntry).order_by(EffectLedgerEntry.intent_id)).scalars()
        return [
            {
                "entity_type": "effect",
                "entity_id": str(row.intent_id),
                "state": row.status.value,
                "expected": {
                    "status": row.status.value,
                    "operation": f"{row.target_system}.{row.operation}",
                    "remote_reference": row.remote_reference,
                },
            }
            for row in rows
        ]
    if domain == "order":
        rows = db.execute(select(SalesOrder).order_by(SalesOrder.order_ref)).scalars()
        return [
            {
                "entity_type": "sales_order",
                "entity_id": row.order_ref,
                "state": row.status.value,
                "expected": {
                    "status": row.status.value,
                    "shopify_order_id": row.shopify_order_id,
                    "odoo_sale_order_id": row.odoo_sale_order_id,
                    "total": str(row.total),
                },
            }
            for row in rows
        ]
    if domain == "procurement":
        rows = db.execute(select(ProcurementOrder).order_by(ProcurementOrder.id)).scalars()
        return [
            {
                "entity_type": "procurement_order",
                "entity_id": str(row.id),
                "state": row.status.value,
                "expected": {
                    "status": row.status.value,
                    "sku": row.sku,
                    "odoo_po_id": row.odoo_po_id,
                    "qty": str(row.qty),
                },
            }
            for row in rows
        ]
    if domain == "return":
        rows = db.execute(select(ReturnCase).order_by(ReturnCase.return_ref)).scalars()
        return [
            {
                "entity_type": "return_case",
                "entity_id": row.return_ref,
                "state": row.status.value,
                "expected": {
                    "status": row.status.value,
                    "shopify_refund_gid": row.shopify_refund_gid,
                    "refund_amount": str(row.refund_amount) if row.refund_amount else None,
                },
            }
            for row in rows
        ]
    if domain == "catalog":
        rows = db.execute(select(CatalogRevision).order_by(CatalogRevision.id)).scalars()
        return [
            {
                "entity_type": "catalog_revision",
                "entity_id": str(row.id),
                "state": row.status.value,
                "expected": {"status": row.status.value, "sku": row.sku},
            }
            for row in rows
        ]
    if domain == "listing":
        rows = db.execute(select(ListingPublication).order_by(ListingPublication.id)).scalars()
        return [
            {
                "entity_type": "listing_publication",
                "entity_id": str(row.id),
                "state": row.status.value,
                "expected": {
                    "status": row.status.value,
                    "sku": row.sku,
                    "shopify_product_gid": row.shopify_product_gid,
                },
            }
            for row in rows
        ]
    if domain == "shopify":
        # Shopify-side view of local state: every mirrored sales order,
        # keyed by its Shopify order name so it pairs with the connector's
        # ``orders.name`` rows.  The state vocabulary is the local O2C
        # pipeline state; Shopify's ``displayFinancialStatus`` (e.g. PAID)
        # is compared as-is, so the semantic difference surfaces as a
        # MANUAL_RECONCILIATION diff rather than being auto-smoothed.
        rows = db.execute(select(SalesOrder).order_by(SalesOrder.order_ref)).scalars()
        return [
            {
                "entity_type": "sales_order",
                "entity_id": row.order_ref,
                "state": row.status.value,
                "expected": {
                    "status": row.status.value,
                    "shopify_order_id": row.shopify_order_id,
                    "total": str(row.total),
                },
            }
            for row in rows
        ]
    raise ValidationError(f"unsupported reconciliation domain: {domain}")


def _normalize_remote(row: Mapping[str, Any]) -> dict[str, Any]:
    missing = {"entity_type", "entity_id", "state"} - set(row.keys())
    if missing:
        raise ValidationError(f"connector row missing fields: {sorted(missing)}")
    return {
        "entity_type": str(row["entity_type"]),
        "entity_id": str(row["entity_id"]),
        "state": str(row["state"]),
        "actual": {k: v for k, v in row.items() if k not in {"entity_type", "entity_id", "state"}},
    }


def _compare(exp: dict[str, Any] | None, act: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a difference dict, or None when both sides agree."""
    if exp is None and act is None:
        return None
    if exp is None:
        return {"expected_state": None, "actual_state": act["state"], "missing_in_local": True}
    if act is None:
        return {"expected_state": exp["state"], "actual_state": None, "missing_in_remote": True}
    if exp["state"] == act["state"]:
        return None
    return {"expected_state": exp["state"], "actual_state": act["state"]}


def run_reconciliation(
    db,
    *,
    run_type: str,
    domains: list[str],
    connectors: Mapping[str, Callable[[str], list[dict[str, Any]]]] | None = None,
) -> ReconciliationRun:
    """Run a reconciliation comparing local state with connector read state.

    Domains without a connector are skipped and reported in the summary.  Diffs
    are created ``OPEN`` and immediately escalated to
    ``MANUAL_RECONCILIATION`` -- never auto-resolved.
    """
    if not run_type:
        raise ValidationError("run_type is required")
    unknown = set(domains) - SUPPORTED_DOMAINS
    if unknown:
        raise ValidationError(f"unsupported reconciliation domains: {sorted(unknown)}")
    if not domains:
        raise ValidationError("at least one domain is required")

    run = ReconciliationRun(
        run_type=run_type,
        status=ReconciliationRunStatus.RUNNING,
        started_at=utc_now(),
    )
    db.add(run)
    db.flush()

    connectors = connectors or {}
    by_domain: dict[str, dict[str, Any]] = {}
    total_checked = 0
    total_diffs = 0

    for domain in domains:
        connector = connectors.get(domain)
        if connector is None:
            by_domain[domain] = {
                "status": "skipped",
                "reason": "no connector provided",
                "checked": 0,
                "diffs": 0,
            }
            continue
        expected_rows = _local_rows(db, domain)
        remote_rows = [_normalize_remote(row) for row in connector(domain)]
        keyed_expected = {(r["entity_type"], r["entity_id"]): r for r in expected_rows}
        keyed_actual = {(r["entity_type"], r["entity_id"]): r for r in remote_rows}
        checked = 0
        diffs = 0
        for key in sorted(set(keyed_expected) | set(keyed_actual)):
            checked += 1
            difference = _compare(keyed_expected.get(key), keyed_actual.get(key))
            if difference is None:
                continue
            exp_row = keyed_expected.get(key)
            act_row = keyed_actual.get(key)
            diff = ReconciliationDiff(
                run_id=run.id,
                domain=domain,
                entity_type=key[0],
                entity_id=key[1],
                expected=(exp_row or {}).get("expected") or (exp_row or {}),
                actual=(act_row or {}).get("actual") or (act_row or {}),
                difference=difference,
                status=ReconciliationDiffStatus.OPEN,
            )
            db.add(diff)
            # Escalate for manual handling; never auto-resolve.
            diff.status = ReconciliationDiffStatus.MANUAL_RECONCILIATION
            diffs += 1
        total_checked += checked
        total_diffs += diffs
        by_domain[domain] = {"status": "completed", "checked": checked, "diffs": diffs}

    finished_at = utc_now()
    run.finished_at = finished_at
    run.status = (
        ReconciliationRunStatus.COMPLETED_WITH_DIFFS
        if total_diffs
        else ReconciliationRunStatus.COMPLETED
    )
    run.summary = {
        "run_type": run_type,
        "domains": domains,
        "started_at": run.started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "checked": total_checked,
        "diffs": total_diffs,
        "auto_resolved": 0,
        "by_domain": by_domain,
    }
    db.flush()
    return run


def mark_diff_manual_reconciliation(db, diff_id: uuid.UUID) -> ReconciliationDiff:
    """Escalate an OPEN diff to MANUAL_RECONCILIATION (human attention)."""
    diff = db.get(ReconciliationDiff, diff_id)
    if diff is None:
        raise NotFoundError(f"reconciliation diff {diff_id} not found")
    if diff.status != ReconciliationDiffStatus.OPEN:
        raise ConflictError(f"diff {diff_id} is not OPEN (status={diff.status.value})")
    diff.status = ReconciliationDiffStatus.MANUAL_RECONCILIATION
    db.flush()
    return diff


def resolve_diff(
    db,
    *,
    diff_id: uuid.UUID,
    note: str,
    resolver_user_id: uuid.UUID | str | None = None,
) -> ReconciliationDiff:
    """Manually resolve a diff (only from MANUAL_RECONCILIATION, never auto)."""
    if not note or not note.strip():
        raise ValidationError("a resolution note is required")
    diff = db.get(ReconciliationDiff, diff_id)
    if diff is None:
        raise NotFoundError(f"reconciliation diff {diff_id} not found")
    if diff.status != ReconciliationDiffStatus.MANUAL_RECONCILIATION:
        raise ConflictError(
            f"diff {diff_id} must be MANUAL_RECONCILIATION before resolution "
            f"(status={diff.status.value})"
        )
    diff.status = ReconciliationDiffStatus.RESOLVED
    diff.resolution_note = note
    diff.resolved_at = utc_now()
    record_audit(
        db,
        actor_user_id=resolver_user_id,
        action="reconciliation_diff.resolve",
        resource_type="reconciliation_diff",
        resource_id=str(diff.id),
        changes={"domain": diff.domain, "note": note, "run_id": str(diff.run_id)},
    )
    db.flush()
    return diff


def list_reconciliation_runs(
    db,
    *,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return a page of reconciliation runs (newest first)."""
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    total = db.execute(select(func.count()).select_from(ReconciliationRun)).scalar_one()
    runs = (
        db.execute(
            select(ReconciliationRun)
            .order_by(ReconciliationRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "runId": str(run.id),
                "runType": run.run_type,
                "status": run.status.value,
                "startedAt": run.started_at.isoformat() if run.started_at else None,
                "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
                "summary": run.summary or {},
                "createdAt": run.created_at.isoformat(),
            }
            for run in runs
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_reconciliation_run(db, run_id: uuid.UUID | str) -> dict[str, Any]:
    """Return a reconciliation run with its diffs (console detail view)."""
    run = db.get(ReconciliationRun, uuid.UUID(str(run_id)))
    if run is None:
        raise NotFoundError(f"reconciliation run {run_id} not found")
    diffs = (
        db.execute(
            select(ReconciliationDiff)
            .where(ReconciliationDiff.run_id == run.id)
            .order_by(ReconciliationDiff.created_at)
        )
        .scalars()
        .all()
    )
    return {
        "runId": str(run.id),
        "runType": run.run_type,
        "status": run.status.value,
        "startedAt": run.started_at.isoformat() if run.started_at else None,
        "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
        "summary": run.summary or {},
        "createdAt": run.created_at.isoformat(),
        "diffs": [
            {
                "diffId": str(diff.id),
                "domain": diff.domain,
                "entityType": diff.entity_type,
                "entityId": diff.entity_id,
                "expected": diff.expected,
                "actual": diff.actual,
                "difference": diff.difference,
                "status": diff.status.value,
                "resolutionNote": diff.resolution_note,
                "resolvedAt": diff.resolved_at.isoformat() if diff.resolved_at else None,
                "createdAt": diff.created_at.isoformat(),
            }
            for diff in diffs
        ],
    }


__all__ = [
    "SUPPORTED_DOMAINS",
    "get_reconciliation_run",
    "list_reconciliation_runs",
    "mark_diff_manual_reconciliation",
    "resolve_diff",
    "run_reconciliation",
]

"""P7 WP5 canonical reconciliation: readers, field comparison, strict semantics.

Covers the plan 二.5 contract:

- ``ReconciliationReader.read_actual(domain, scope) -> CanonicalExternalState``
  with an in-memory reader for tests.
- Per-domain canonical field comparison (``COMPARE_FIELDS``).
- A requested domain without a reader fails the whole run
  (``reconciliation_incomplete``); scheduled runs never skip.
- "0 diffs" only holds with ``checked > 0`` or ``provenEmpty`` per domain.
- Legacy ``"shopify"`` domain expands to listing+order+return with a warning.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.connectors.base import payload_hash
from app.core.errors import ValidationError
from app.models.listing import ListingPublication
from app.models.order import SalesOrder
from app.models.reconciliation import (
    ReconciliationDiff,
    ReconciliationDiffStatus,
    ReconciliationRunStatus,
)
from app.services.effect_ledger import mark_effect, record_effect
from app.services.reconciliation import (
    COMPARE_FIELDS,
    CanonicalExternalState,
    InMemoryReconciliationReader,
    ReconciliationReader,
    run_reconciliation,
)


def _listing_state(*, sku: str, published: bool, gid: str | None = None) -> CanonicalExternalState:
    gid = gid or f"gid://shopify/Product/{sku}"
    return CanonicalExternalState(
        domain="listing",
        entity_type="shopify_product",
        entity_id=sku,
        facts={
            "sku": sku,
            "shopify_product_gid": gid,
            "published": published,
            "content_hash": payload_hash({"sku": sku, "name": "Widget"}),
        },
    )


def _add_listing(db, *, sku: str, published: bool = True) -> None:
    db.add(
        ListingPublication(
            sku=sku,
            channel="shopify",
            shopify_product_gid=f"gid://shopify/Product/{sku}",
            status="active" if published else "draft",
            payload={"title": "Widget"},
        )
    )
    db.flush()


def _effect_reader(present: bool) -> InMemoryReconciliationReader:
    """Reader that echoes expected effects with the requested presence."""

    def _read(scope):
        expected = (scope or {}).get("expected_states") or []
        return [
            CanonicalExternalState(
                domain="effect",
                entity_type="effect",
                entity_id=state.entity_id,
                facts={
                    "operation": state.facts["operation"],
                    "intent_id": state.facts["intent_id"],
                    "remote_reference": state.facts["remote_reference"],
                    "status": "exists" if present else "missing",
                    "remote_present": present,
                },
            )
            for state in expected
        ]

    return InMemoryReconciliationReader({"effect": _read})


def test_in_memory_reader_satisfies_protocol() -> None:
    reader = InMemoryReconciliationReader(
        {"listing": [_listing_state(sku="SKU-1", published=True)]}
    )
    assert isinstance(reader, ReconciliationReader)
    states = reader.read_actual("listing")
    assert len(states) == 1
    assert states[0].facts["published"] is True
    assert reader.read_actual("order") == []


def test_canonical_state_rejects_extra_fields() -> None:
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        CanonicalExternalState(
            domain="listing",
            entity_type="x",
            entity_id="y",
            facts={},
            unexpected=1,
        )


def test_no_drift_when_facts_agree(db) -> None:
    _add_listing(db, sku="SKU-1")
    reader = InMemoryReconciliationReader(
        {"listing": [_listing_state(sku="SKU-1", published=True)]}
    )
    run = run_reconciliation(
        db,
        run_type="daily",
        domains=["listing"],
        readers={"listing": reader},
    )
    assert run.status == ReconciliationRunStatus.COMPLETED
    assert run.summary["checked"] == 1
    assert run.summary["diffs"] == 0
    assert run.summary["failedDomains"] == []
    assert run.summary["skippedDomains"] == []
    assert run.summary["byDomain"]["listing"]["verified"] is True
    assert run.summary["byDomain"]["listing"]["provenEmpty"] is False


def test_drift_creates_field_level_diff(db) -> None:
    _add_listing(db, sku="SKU-1", published=True)
    reader = InMemoryReconciliationReader(
        {"listing": [_listing_state(sku="SKU-1", published=False)]}
    )
    run = run_reconciliation(
        db,
        run_type="daily",
        domains=["listing"],
        readers={"listing": reader},
    )
    assert run.status == ReconciliationRunStatus.COMPLETED_WITH_DIFFS
    assert run.summary["diffs"] == 1
    diff = db.execute(select(ReconciliationDiff)).scalar_one()
    assert diff.status == ReconciliationDiffStatus.MANUAL_RECONCILIATION
    assert diff.domain == "listing"
    differences = diff.difference["field_differences"]
    assert any(d["field"] == "published" for d in differences)


def test_missing_reader_fails_whole_run(db) -> None:
    reader = InMemoryReconciliationReader({})
    run = run_reconciliation(
        db,
        run_type="daily",
        domains=["effect"],
        readers={"listing": reader},
    )
    assert run.status == ReconciliationRunStatus.FAILED
    assert run.summary["errorCode"] == "reconciliation_incomplete"
    assert run.summary["failedDomains"] == ["effect"]
    assert run.summary["skippedDomains"] == []


def test_scheduled_run_never_skips(db) -> None:
    run = run_reconciliation(
        db,
        run_type="scheduled",
        domains=["catalog"],
        readers={},
        scheduled=True,
    )
    assert run.status == ReconciliationRunStatus.FAILED
    assert run.summary["failedDomains"] == ["catalog"]
    assert run.summary["skippedDomains"] == []


def test_proven_empty_domain_completes(db) -> None:
    reader = InMemoryReconciliationReader({"listing": []})
    run = run_reconciliation(
        db,
        run_type="daily",
        domains=["listing"],
        readers={"listing": reader},
    )
    assert run.status == ReconciliationRunStatus.COMPLETED
    domain_summary = run.summary["byDomain"]["listing"]
    assert domain_summary["checked"] == 0
    assert domain_summary["provenEmpty"] is True
    assert domain_summary["verified"] is True


def test_legacy_shopify_domain_expands_with_warning(db) -> None:
    reader = InMemoryReconciliationReader({"listing": [], "order": [], "return": []})
    run = run_reconciliation(
        db,
        run_type="daily",
        domains=["shopify"],
        readers={
            "listing": reader,
            "order": reader,
            "return": reader,
        },
    )
    assert run.status == ReconciliationRunStatus.COMPLETED
    assert run.summary["domains"] == ["listing", "order", "return"]
    assert run.summary["deprecationWarnings"]


def test_effect_succeeded_readback_no_drift(db) -> None:
    entry = record_effect(
        db,
        target_system="shopify",
        operation="product_publish",
    )
    mark_effect(db, entry.intent_id, status="dispatched")
    mark_effect(
        db,
        entry.intent_id,
        status="succeeded",
        remote_reference="gid://shopify/Product/1",
    )
    run = run_reconciliation(
        db,
        run_type="daily",
        domains=["effect"],
        readers={"effect": _effect_reader(present=True)},
    )
    assert run.status == ReconciliationRunStatus.COMPLETED
    assert run.summary["checked"] == 1


def test_effect_outcome_unknown_where_remote_exists_is_diff(db) -> None:
    entry = record_effect(
        db,
        target_system="shopify",
        operation="refund_create",
    )
    mark_effect(db, entry.intent_id, status="dispatched")
    mark_effect(
        db,
        entry.intent_id,
        status="outcome_unknown",
        error_detail="ambiguous transport failure",
    )
    # Remote refund actually exists: the reconciliation must surface the drift.
    run = run_reconciliation(
        db,
        run_type="daily",
        domains=["effect"],
        readers={"effect": _effect_reader(present=True)},
    )
    assert run.status == ReconciliationRunStatus.COMPLETED_WITH_DIFFS
    diff = db.execute(select(ReconciliationDiff)).scalar_one()
    assert diff.difference["field_differences"]


def test_order_domain_pairs_by_shopify_order_id(db) -> None:
    db.add(
        SalesOrder(
            order_ref="CO-1",
            shopify_order_id="gid://shopify/Order/1",
            currency="USD",
            total=Decimal("100.00"),
        )
    )
    db.flush()
    reader = InMemoryReconciliationReader(
        {
            "order": [
                CanonicalExternalState(
                    domain="order",
                    entity_type="shopify_order",
                    entity_id="gid://shopify/Order/1",
                    facts={
                        "shopify_order_id": "gid://shopify/Order/1",
                        "currency": "USD",
                        "total": "100.00",
                    },
                )
            ]
        }
    )
    run = run_reconciliation(
        db,
        run_type="daily",
        domains=["order"],
        readers={"order": reader},
    )
    assert run.status == ReconciliationRunStatus.COMPLETED
    assert run.summary["diffs"] == 0


def test_order_domain_drift_on_total(db) -> None:
    db.add(
        SalesOrder(
            order_ref="CO-1",
            shopify_order_id="gid://shopify/Order/1",
            currency="USD",
            total=Decimal("100.00"),
        )
    )
    db.flush()
    reader = InMemoryReconciliationReader(
        {
            "order": [
                CanonicalExternalState(
                    domain="order",
                    entity_type="shopify_order",
                    entity_id="gid://shopify/Order/1",
                    facts={
                        "shopify_order_id": "gid://shopify/Order/1",
                        "currency": "USD",
                        "total": "99.99",
                    },
                )
            ]
        }
    )
    run = run_reconciliation(
        db,
        run_type="daily",
        domains=["order"],
        readers={"order": reader},
    )
    assert run.status == ReconciliationRunStatus.COMPLETED_WITH_DIFFS
    diff = db.execute(select(ReconciliationDiff)).scalar_one()
    assert any(d["field"] == "total" for d in diff.difference["field_differences"])


def test_reader_error_fails_domain(db) -> None:
    def _boom(_scope) -> list[CanonicalExternalState]:
        raise RuntimeError("remote read failed")

    reader = InMemoryReconciliationReader({"listing": _boom})
    run = run_reconciliation(
        db,
        run_type="daily",
        domains=["listing"],
        readers={"listing": reader},
    )
    assert run.status == ReconciliationRunStatus.FAILED
    assert run.summary["failedDomains"] == ["listing"]
    assert "reader error" in run.summary["byDomain"]["listing"]["reason"]


def test_readers_and_connectors_are_mutually_exclusive(db) -> None:
    with pytest.raises(ValidationError, match="either readers or connectors"):
        run_reconciliation(
            db,
            run_type="daily",
            domains=["effect"],
            readers={},
            connectors={},
        )


def test_legacy_connector_path_still_skips_missing(db) -> None:
    run = run_reconciliation(db, run_type="daily", domains=["effect"])
    assert run.status == ReconciliationRunStatus.COMPLETED
    assert run.summary["by_domain"]["effect"]["status"] == "skipped"


def test_compare_fields_cover_all_canonical_domains() -> None:
    assert {"listing", "order", "procurement", "return", "catalog", "effect"} <= set(
        COMPARE_FIELDS
    )

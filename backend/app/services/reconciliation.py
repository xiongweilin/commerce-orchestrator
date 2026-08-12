"""Canonical reconciliation: compare local facts with external system facts.

Plan 二.5 replaces string-state comparison with domain canonical facts:

- :class:`ReconciliationReader` reads ``list[CanonicalExternalState]`` per
  domain; production readers wrap the Shopify/Odoo connectors, and
  :class:`InMemoryReconciliationReader` serves tests.
- :func:`run_reconciliation` compares per-domain fields (plan 二.5). A
  requested domain without a reader fails the whole run
  (``reconciliation_incomplete``); scheduled runs never skip domains.
- "0 differences" only holds when every domain has ``checked > 0`` or is
  explicitly proven empty (``provenEmpty``).
- Diffs always go ``OPEN -> MANUAL_RECONCILIATION``; they are never
  auto-resolved (auto-smoothing is forbidden).  Only a human can move a diff
  to ``RESOLVED``, and only a later re-run that agrees completes it.

Backward compatibility: the legacy ``connectors={domain: callable}`` path is
preserved inside :func:`run_reconciliation` when no ``readers`` are given
(missing connectors are skipped, old summary shape).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.connectors.base import payload_hash
from app.connectors.odoo import OdooConnector
from app.connectors.shopify import ShopifyConnector
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.time import utc_now
from app.models.catalog import CatalogRevision
from app.models.effect import EffectLedgerEntry
from app.models.listing import ExternalIdMapping, ListingPublication
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

DEPRECATED_DOMAINS = {"shopify": ("listing", "order", "return")}
"""Legacy domain names expanded to canonical domains (with a warning)."""

# Domain comparison fields (plan 二.5). Status strings that use different
# vocabularies across systems (e.g. local pipeline states vs Shopify
# displayFinancialStatus) are deliberately NOT compared; they stay in
# ``facts`` as informational context for manual review.
COMPARE_FIELDS: dict[str, tuple[str, ...]] = {
    "listing": ("sku", "shopify_product_gid", "published", "content_hash"),
    "order": ("currency", "total", "shopify_order_id", "odoo_sale_order_id"),
    "procurement": ("po_id", "sku", "qty", "currency"),
    "return": ("refund_id", "refund_amount", "refund_currency", "credit_note_id"),
    "catalog": ("sku", "odoo_product_id", "content_hash"),
    "effect": ("operation", "intent_id", "remote_reference", "remote_present"),
}

# Fields used to pair an expected state with remote actual states (join keys).
_LINK_FIELDS = (
    "shopify_order_id",
    "odoo_sale_order_id",
    "po_id",
    "refund_id",
    "credit_note_id",
    "odoo_product_id",
)


class CanonicalExternalState(BaseModel):
    """One external entity's canonical facts for a reconciliation domain."""

    model_config = ConfigDict(extra="forbid")

    domain: str
    entity_type: str
    entity_id: str
    facts: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class ReconciliationReader(Protocol):
    """Reads canonical external facts for one or more domains.

    ``read_actual(domain, scope)`` returns the remote system's current
    canonical facts for the domain. ``scope`` is a free-form mapping
    (``updated_after``, expected states for read-back domains, ...); readers
    must tolerate ``None``. Read failures (transport/5xx) propagate so the
    run fails loudly instead of pretending the domain was verified.
    """

    name: str

    def read_actual(
        self,
        domain: str,
        scope: Mapping[str, Any] | None = None,
    ) -> list[CanonicalExternalState]: ...


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


# ---------------------------------------------------------------------------
# Canonical facts (plan 二.5)
# ---------------------------------------------------------------------------


def _listing_content_hash(row: ListingPublication) -> str | None:
    """Canonical content digest of a listing publication.

    The digest covers ``sku`` + remote ``title`` so it stays comparable with
    the Shopify reader's hash of the same fields. ``None`` when the local
    listing carries no payload (drift surfaces as a missing-field diff).
    """
    if not row.payload:
        return None
    canonical: dict[str, Any] = {"sku": row.sku}
    title = row.payload.get("title")
    if title is not None:
        canonical["name"] = title
    return payload_hash(canonical)


def _catalog_content_hash(row: CatalogRevision) -> str | None:
    """Canonical content digest of a catalog revision (sku + title)."""
    return payload_hash({"sku": row.sku, "name": row.title})


def _local_canonical_facts(db, domain: str) -> list[CanonicalExternalState]:
    """Local source-of-truth canonical facts for a domain."""
    if domain == "listing":
        rows = db.execute(select(ListingPublication).order_by(ListingPublication.sku)).scalars()
        return [
            CanonicalExternalState(
                domain="listing",
                entity_type="listing_publication",
                entity_id=row.sku,
                facts={
                    "sku": row.sku,
                    "shopify_product_gid": row.shopify_product_gid,
                    "published": row.status.value in {"active", "publishing"},
                    "content_hash": _listing_content_hash(row),
                },
            )
            for row in rows
        ]
    if domain == "order":
        rows = db.execute(select(SalesOrder).order_by(SalesOrder.order_ref)).scalars()
        return [
            CanonicalExternalState(
                domain="order",
                entity_type="sales_order",
                entity_id=row.order_ref,
                facts={
                    "order_ref": row.order_ref,
                    "shopify_order_id": row.shopify_order_id,
                    "odoo_sale_order_id": row.odoo_sale_order_id,
                    "currency": row.currency,
                    "total": str(row.total),
                    "status": row.status.value,
                },
            )
            for row in rows
        ]
    if domain == "procurement":
        rows = db.execute(select(ProcurementOrder).order_by(ProcurementOrder.id)).scalars()
        return [
            CanonicalExternalState(
                domain="procurement",
                entity_type="procurement_order",
                entity_id=str(row.id),
                facts={
                    "po_id": row.odoo_po_id,
                    "sku": row.sku,
                    "qty": str(row.qty),
                    "currency": row.currency,
                    "status": row.status.value,
                },
            )
            for row in rows
        ]
    if domain == "return":
        rows = db.execute(select(ReturnCase).order_by(ReturnCase.return_ref)).scalars()
        return [
            CanonicalExternalState(
                domain="return",
                entity_type="return_case",
                entity_id=row.return_ref,
                facts={
                    "return_ref": row.return_ref,
                    "refund_id": row.shopify_refund_gid,
                    "refund_amount": (
                        str(row.refund_amount) if row.refund_amount is not None else None
                    ),
                    "refund_currency": row.currency,
                    "credit_note_id": row.credit_note_id,
                    "status": row.status.value,
                },
            )
            for row in rows
        ]
    if domain == "catalog":
        rows = db.execute(select(CatalogRevision).order_by(CatalogRevision.sku)).scalars()
        states: list[CanonicalExternalState] = []
        for row in rows:
            mapping = db.execute(
                select(ExternalIdMapping).where(
                    ExternalIdMapping.sku == row.sku,
                    ExternalIdMapping.channel == "odoo",
                )
            ).scalar_one_or_none()
            states.append(
                CanonicalExternalState(
                    domain="catalog",
                    entity_type="catalog_revision",
                    entity_id=row.sku,
                    facts={
                        "sku": row.sku,
                        "odoo_product_id": mapping.external_id if mapping else None,
                        "content_hash": _catalog_content_hash(row),
                        "status": row.status.value,
                    },
                )
            )
        return states
    if domain == "effect":
        rows = db.execute(select(EffectLedgerEntry).order_by(EffectLedgerEntry.intent_id)).scalars()
        return [
            CanonicalExternalState(
                domain="effect",
                entity_type="effect",
                entity_id=str(row.intent_id),
                facts={
                    "operation": f"{row.target_system}.{row.operation}",
                    "intent_id": str(row.intent_id),
                    "remote_reference": row.remote_reference,
                    "status": row.status.value,
                    # A succeeded effect expects the remote entity to exist.
                    "remote_present": bool(
                        row.remote_reference and row.status.value == "succeeded"
                    ),
                },
            )
            for row in rows
        ]
    raise ValidationError(f"unsupported reconciliation domain: {domain}")


def _pair_candidates(state: CanonicalExternalState) -> set[str]:
    """Key candidates used to pair expected and actual states."""
    keys = {state.entity_id}
    for field in _LINK_FIELDS:
        value = state.facts.get(field)
        if value is not None:
            keys.add(str(value))
    return keys


def _compare_canonical_fields(
    domain: str,
    exp_facts: dict[str, Any] | None,
    act_facts: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Field-level differences for one entity, or an empty list when in sync."""
    fields = COMPARE_FIELDS.get(domain, ())
    if exp_facts is None:
        return [{"field": "*", "expected": None, "actual": act_facts, "missing_in_local": True}]
    if act_facts is None:
        return [{"field": "*", "expected": exp_facts, "actual": None, "missing_in_remote": True}]
    differences: list[dict[str, Any]] = []
    for field in fields:
        exp = exp_facts.get(field)
        act = act_facts.get(field)
        if exp is None and act is None:
            continue
        if exp is None:
            differences.append(
                {"field": field, "expected": None, "actual": act, "missing_in_expected": True}
            )
        elif act is None:
            differences.append(
                {"field": field, "expected": exp, "actual": None, "missing_in_actual": True}
            )
        elif str(exp) != str(act):
            differences.append({"field": field, "expected": exp, "actual": act})
    return differences


def _compare_domain(
    db,
    run_id: uuid.UUID,
    domain: str,
    expected: Sequence[CanonicalExternalState],
    actual: Sequence[CanonicalExternalState],
) -> tuple[int, int, bool]:
    """Compare one domain; returns ``(checked, diffs, proven_empty)``."""
    used = [False] * len(actual)
    checked = 0
    diffs = 0

    def _add_diff(
        *,
        entity_type: str,
        entity_id: str,
        exp_facts: dict[str, Any] | None,
        act_facts: dict[str, Any] | None,
        differences: list[dict[str, Any]],
    ) -> None:
        nonlocal diffs
        diffs += 1
        diff = ReconciliationDiff(
            run_id=run_id,
            domain=domain,
            entity_type=entity_type,
            entity_id=entity_id,
            expected=exp_facts,
            actual=act_facts,
            difference={"field_differences": differences},
            status=ReconciliationDiffStatus.OPEN,
        )
        db.add(diff)
        # Escalate for manual handling; never auto-resolve.
        diff.status = ReconciliationDiffStatus.MANUAL_RECONCILIATION

    for exp_state in expected:
        checked += 1
        exp_keys = _pair_candidates(exp_state)
        matched: list[CanonicalExternalState] = []
        for i, act_state in enumerate(actual):
            if used[i]:
                continue
            if _pair_candidates(act_state) & exp_keys:
                used[i] = True
                matched.append(act_state)
        merged: dict[str, Any] = {}
        for act_state in matched:
            merged.update(act_state.facts)
        differences = _compare_canonical_fields(
            domain, exp_state.facts, merged if matched else None
        )
        if differences:
            _add_diff(
                entity_type=exp_state.entity_type,
                entity_id=exp_state.entity_id,
                exp_facts=dict(exp_state.facts),
                act_facts=merged if matched else None,
                differences=differences,
            )

    for i, act_state in enumerate(actual):
        if used[i]:
            continue
        checked += 1
        _add_diff(
            entity_type=act_state.entity_type,
            entity_id=act_state.entity_id,
            exp_facts=None,
            act_facts=dict(act_state.facts),
            differences=[{"field": "*", "expected": None, "actual": act_state.facts}],
        )

    proven_empty = not expected and not actual
    return checked, diffs, proven_empty


# ---------------------------------------------------------------------------
# Reconciliation readers
# ---------------------------------------------------------------------------


class InMemoryReconciliationReader:
    """Test/fixture reader backed by an in-memory state map.

    ``states`` maps a domain to either a list of
    :class:`CanonicalExternalState` or a callable
    ``(scope) -> list[CanonicalExternalState]``.
    """

    name = "in-memory"

    def __init__(
        self,
        states: Mapping[
            str,
            list[CanonicalExternalState]
            | Callable[[Mapping[str, Any] | None], list[CanonicalExternalState]],
        ],
    ) -> None:
        self._states = dict(states)

    def read_actual(
        self,
        domain: str,
        scope: Mapping[str, Any] | None = None,
    ) -> list[CanonicalExternalState]:
        value = self._states.get(domain)
        if value is None:
            return []
        if callable(value):
            return list(value(scope))
        return [state for state in value if state.domain == domain]


class CompositeReconciliationReader:
    """Merge canonical states from several readers (e.g. order: shopify+odoo)."""

    name = "composite"

    def __init__(self, readers: Sequence[ReconciliationReader]) -> None:
        self._readers = list(readers)

    def read_actual(
        self,
        domain: str,
        scope: Mapping[str, Any] | None = None,
    ) -> list[CanonicalExternalState]:
        states: list[CanonicalExternalState] = []
        for reader in self._readers:
            states.extend(reader.read_actual(domain, scope or {}))
        return states


class ShopifyReconciliationReader:
    """Canonical external facts for Shopify: listing / order / return / effect."""

    name = "shopify"

    def __init__(self, connector: ShopifyConnector) -> None:
        self.connector = connector

    def read_actual(
        self,
        domain: str,
        scope: Mapping[str, Any] | None = None,
    ) -> list[CanonicalExternalState]:
        if domain == "listing":
            return self._listing()
        if domain == "order":
            return self._orders(scope)
        if domain == "return":
            return self._refunds(scope)
        if domain == "effect":
            return self._effects(scope)
        raise ValidationError(f"shopify reader does not support domain {domain!r}")

    def _listing(self) -> list[CanonicalExternalState]:
        nodes: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page, cursor = self.connector.list_products(first=25, cursor=cursor)
            nodes.extend(page)
            if cursor is None:
                break
        states: list[CanonicalExternalState] = []
        for product in nodes:
            for sku in product.get("skus") or []:
                states.append(
                    CanonicalExternalState(
                        domain="listing",
                        entity_type="shopify_product",
                        entity_id=str(sku),
                        facts={
                            "sku": str(sku),
                            "shopify_product_gid": product.get("id"),
                            "published": bool(product.get("published")),
                            "content_hash": payload_hash(
                                {"sku": str(sku), "name": str(product.get("title") or "")}
                            ),
                            "publication_ids": product.get("publication_ids") or [],
                        },
                    )
                )
        return states

    def _orders(self, scope: Mapping[str, Any] | None) -> list[CanonicalExternalState]:
        updated_after = (scope or {}).get("updated_after")
        nodes: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page, cursor = self.connector.list_orders(updated_after, first=50, cursor=cursor)
            nodes.extend(page)
            if cursor is None:
                break
        states: list[CanonicalExternalState] = []
        for order in nodes:
            money = (order.get("totalPriceSet") or {}).get("presentmentMoney") or {}
            states.append(
                CanonicalExternalState(
                    domain="order",
                    entity_type="shopify_order",
                    entity_id=str(order.get("id") or order.get("legacyResourceId") or ""),
                    facts={
                        "shopify_order_id": order.get("id"),
                        "name": order.get("name"),
                        "currency": money.get("currencyCode"),
                        "total": money.get("amount"),
                        "financial_status": order.get("displayFinancialStatus"),
                        "fulfillment_status": order.get("displayFulfillmentStatus"),
                    },
                )
            )
        return states

    def _refunds(self, scope: Mapping[str, Any] | None) -> list[CanonicalExternalState]:
        expected = (scope or {}).get("expected_states") or []
        states: list[CanonicalExternalState] = []
        for state in expected:
            refund_gid = state.facts.get("refund_id")
            if not refund_gid:
                continue
            refund = self.connector.get_refund(str(refund_gid))
            money = ((refund or {}).get("totalRefundSet") or {}).get("presentmentMoney") or {}
            states.append(
                CanonicalExternalState(
                    domain="return",
                    entity_type="shopify_refund",
                    entity_id=str(refund_gid),
                    facts={
                        "refund_id": str(refund_gid),
                        "shopify_order_id": state.facts.get("shopify_order_id"),
                        "refund_status": (refund or {}).get("status"),
                        "refund_amount": money.get("amount"),
                        "refund_currency": money.get("currencyCode"),
                    },
                )
            )
        return states

    def _effects(self, scope: Mapping[str, Any] | None) -> list[CanonicalExternalState]:
        expected = (scope or {}).get("expected_states") or []
        states: list[CanonicalExternalState] = []
        for state in expected:
            facts = state.facts
            operation = str(facts.get("operation") or "")
            ref = facts.get("remote_reference")
            intent_id = str(facts.get("intent_id") or state.entity_id)
            remote_status: Any = None
            if operation == "shopify.product_publish" and ref:
                product = self.connector.get_product_publish_status(str(ref))
                if product is None:
                    remote_status = "missing"
                else:
                    remote_status = "published" if product.get("isPublished") else "unpublished"
            elif operation == "shopify.product_update" and ref:
                product = self.connector.get_product(str(ref))
                remote_status = "exists" if product else "missing"
            elif operation == "shopify.fulfillment_create" and ref:
                fulfillment = self.connector.get_fulfillment(str(ref))
                remote_status = (
                    (fulfillment or {}).get("status")
                    if fulfillment is not None
                    else "missing"
                )
            elif operation == "shopify.refund_create" and ref:
                refund = self.connector.get_refund(str(ref))
                remote_status = (refund or {}).get("status") if refund is not None else "missing"
            states.append(
                CanonicalExternalState(
                    domain="effect",
                    entity_type="effect",
                    entity_id=intent_id,
                    facts={
                        "operation": operation,
                        "intent_id": intent_id,
                        "remote_reference": ref,
                        "status": remote_status,
                        "remote_present": remote_status not in (None, "missing"),
                    },
                )
            )
        return states


_ODOO_EFFECT_MODEL: dict[str, str] = {
    "odoo.product_create": "product.template",
    "odoo.product_update": "product.template",
    "odoo.sale_order_create": "sale.order",
    "odoo.sale_order_confirm": "sale.order",
    "odoo.stock_move_create": "stock.move",
    "odoo.picking_create": "stock.picking",
    "odoo.picking_validate": "stock.picking",
    "odoo.receive_transfer": "stock.picking",
    "odoo.invoice_create": "account.move",
    "odoo.invoice_validate": "account.move",
    "odoo.credit_note_create": "account.move",
    "odoo.credit_note_validate": "account.move",
    "odoo.po_create": "purchase.order",
    "odoo.po_confirm": "purchase.order",
    "odoo.bill_create": "account.move",
}


def _currency_code(value: Any) -> str | None:
    """Normalize an Odoo currency_id value (``[id, code]`` or plain code)."""
    if isinstance(value, list) and len(value) >= 2:
        return str(value[1])
    if isinstance(value, str):
        return value
    return None


class OdooReconciliationReader:
    """Canonical external facts for Odoo: catalog/order/procurement/etc."""

    name = "odoo"

    def __init__(self, connector: OdooConnector) -> None:
        self.connector = connector

    def read_actual(
        self,
        domain: str,
        scope: Mapping[str, Any] | None = None,
    ) -> list[CanonicalExternalState]:
        if domain == "catalog":
            return self._catalog()
        if domain == "order":
            return self._sale_orders()
        if domain == "procurement":
            return self._purchase_orders()
        if domain == "inventory":
            return self._inventory()
        if domain == "return":
            return self._credit_notes()
        if domain == "effect":
            return self._effects(scope)
        raise ValidationError(f"odoo reader does not support domain {domain!r}")

    def _catalog(self) -> list[CanonicalExternalState]:
        states: list[CanonicalExternalState] = []
        offset = 0
        while True:
            rows = self.connector.list_products(offset=offset, limit=500)
            for row in rows:
                sku = str(row.get("default_code") or "")
                if not sku:
                    continue
                states.append(
                    CanonicalExternalState(
                        domain="catalog",
                        entity_type="odoo_product",
                        entity_id=sku,
                        facts={
                            "sku": sku,
                            "odoo_product_id": str(row.get("id")),
                            "content_hash": payload_hash(
                                {"sku": sku, "name": str(row.get("name") or "")}
                            ),
                        },
                    )
                )
            if len(rows) < 500:
                break
            offset += len(rows)
        return states

    def _sale_orders(self) -> list[CanonicalExternalState]:
        states: list[CanonicalExternalState] = []
        for row in self.connector.list_sale_orders():
            states.append(
                CanonicalExternalState(
                    domain="order",
                    entity_type="odoo_sale_order",
                    entity_id=str(row.get("id")),
                    facts={
                        "odoo_sale_order_id": str(row.get("id")),
                        "name": row.get("name"),
                        "client_order_ref": row.get("client_order_ref"),
                        "currency": _currency_code(row.get("currency_id")),
                        "total": str(row.get("amount_total") or ""),
                        "state": row.get("state"),
                    },
                )
            )
        return states

    def _purchase_orders(self) -> list[CanonicalExternalState]:
        states: list[CanonicalExternalState] = []
        for row in self.connector.list_purchase_orders():
            states.append(
                CanonicalExternalState(
                    domain="procurement",
                    entity_type="odoo_po",
                    entity_id=str(row.get("id")),
                    facts={
                        "po_id": str(row.get("id")),
                        "name": row.get("name"),
                        "partner_ref": row.get("partner_ref"),
                        "currency": _currency_code(row.get("currency_id")),
                        "total": str(row.get("amount_total") or ""),
                        "state": row.get("state"),
                    },
                )
            )
        return states

    def _inventory(self) -> list[CanonicalExternalState]:
        states: list[CanonicalExternalState] = []
        for row in self.connector.list_quants():
            product_id = row.get("product_id")
            location_id = row.get("location_id")
            if isinstance(product_id, list):
                product_id = product_id[0] if product_id else None
            if isinstance(location_id, list):
                location_id = location_id[0] if location_id else None
            states.append(
                CanonicalExternalState(
                    domain="inventory",
                    entity_type="stock_quant",
                    entity_id=f"{product_id}@{location_id}",
                    facts={
                        "product_id": str(product_id),
                        "location_id": str(location_id),
                        "quantity": str(row.get("quantity") or ""),
                    },
                )
            )
        return states

    def _credit_notes(self) -> list[CanonicalExternalState]:
        states: list[CanonicalExternalState] = []
        for row in self.connector.list_account_moves("out_refund"):
            states.append(
                CanonicalExternalState(
                    domain="return",
                    entity_type="odoo_credit_note",
                    entity_id=str(row.get("id")),
                    facts={
                        "credit_note_id": str(row.get("id")),
                        "credit_note_ref": row.get("ref"),
                        "credit_note_status": row.get("state"),
                        "credit_note_amount": str(row.get("amount_total") or ""),
                        "credit_note_currency": _currency_code(row.get("currency_id")),
                    },
                )
            )
        return states

    def _effects(self, scope: Mapping[str, Any] | None) -> list[CanonicalExternalState]:
        expected = (scope or {}).get("expected_states") or []
        states: list[CanonicalExternalState] = []
        for state in expected:
            facts = state.facts
            operation = str(facts.get("operation") or "")
            ref = facts.get("remote_reference")
            intent_id = str(facts.get("intent_id") or state.entity_id)
            model = _ODOO_EFFECT_MODEL.get(operation)
            remote_status: Any = None
            remote_present = False
            if model and ref:
                try:
                    odoo_id = int(ref)
                except (TypeError, ValueError):
                    odoo_id = None
                if odoo_id is not None:
                    rows = self.connector.read_records(model, [odoo_id], fields=["id", "state"])
                    if rows:
                        remote_status = rows[0].get("state")
                        remote_present = remote_status is not None
                    else:
                        remote_status = "missing"
            states.append(
                CanonicalExternalState(
                    domain="effect",
                    entity_type="effect",
                    entity_id=intent_id,
                    facts={
                        "operation": operation,
                        "intent_id": intent_id,
                        "remote_reference": ref,
                        "status": remote_status,
                        "remote_present": remote_present,
                    },
                )
            )
        return states


class EffectReconciliationReader:
    """Reads back every ledger effect via its target-system reader.

    ``scope["expected_states"]`` (filled by :func:`run_reconciliation`) carries
    the canonical states produced from the effect ledger; each effect is
    dispatched to the reader of its target system. An effect whose target
    system has no reader is reported unverifiable (``remote_present=False``)
    so the drift surfaces as a diff instead of being silently skipped.
    """

    name = "effect"

    def __init__(self, target_readers: Mapping[str, ReconciliationReader]) -> None:
        self._target_readers = dict(target_readers)

    def read_actual(
        self,
        domain: str,
        scope: Mapping[str, Any] | None = None,
    ) -> list[CanonicalExternalState]:
        if domain != "effect":
            return []
        expected = (scope or {}).get("expected_states") or []
        states: list[CanonicalExternalState] = []
        for state in expected:
            operation = str(state.facts.get("operation") or "")
            system = operation.split(".", 1)[0]
            reader = self._target_readers.get(system)
            if reader is None:
                intent_id = str(state.facts.get("intent_id") or state.entity_id)
                states.append(
                    CanonicalExternalState(
                        domain="effect",
                        entity_type="effect",
                        entity_id=intent_id,
                        facts={
                            "operation": operation,
                            "intent_id": intent_id,
                            "remote_reference": state.facts.get("remote_reference"),
                            "status": None,
                            "remote_present": False,
                        },
                    )
                )
                continue
            states.extend(reader.read_actual("effect", {"expected_states": [state]}))
        return states


def shopify_reader(connector: ShopifyConnector | None = None) -> ShopifyReconciliationReader:
    """Build the Shopify reconciliation reader (inject the connector)."""
    return ShopifyReconciliationReader(connector or ShopifyConnector())


def odoo_reader(connector: OdooConnector | None = None) -> OdooReconciliationReader:
    """Build the Odoo reconciliation reader (inject the connector)."""
    return OdooReconciliationReader(connector or OdooConnector())


def default_readers(
    *,
    shopify: ShopifyConnector | None = None,
    odoo: OdooConnector | None = None,
) -> dict[str, ReconciliationReader]:
    """Reader map for the six canonical domains (plan 二.5).

    ``order`` reads both Shopify orders and Odoo sale orders; ``return``
    reads Shopify refunds and Odoo credit notes. ``effect`` reads back each
    ledger effect through the target-system readers.
    """
    shop = shopify_reader(shopify)
    odo = odoo_reader(odoo)
    return {
        "listing": shop,
        "order": CompositeReconciliationReader([shop, odo]),
        "procurement": odo,
        "return": CompositeReconciliationReader([shop, odo]),
        "catalog": odo,
        "effect": EffectReconciliationReader({"shopify": shop, "odoo": odo}),
    }


def run_reconciliation(
    db,
    *,
    run_type: str,
    domains: list[str],
    readers: Mapping[str, ReconciliationReader] | None = None,
    scope: Mapping[str, Any] | None = None,
    scheduled: bool = False,
    connectors: Mapping[str, Callable[[str], list[dict[str, Any]]]] | None = None,
) -> ReconciliationRun:
    """Run a reconciliation comparing local state with external state.

    Two paths (plan 二.5):

    - ``readers`` (new): canonical facts comparison with strict semantics —
      a requested domain without a reader fails the whole run
      (``reconciliation_incomplete``); scheduled runs never skip; "0 diffs"
      only holds when every domain has ``checked > 0`` or ``provenEmpty``.
    - ``connectors`` (legacy): ``{domain: callable}`` read rows; domains
      without a connector are skipped (old summary shape, kept for backward
      compatibility).

    ``readers`` and ``connectors`` are mutually exclusive. Diffs are created
    ``OPEN`` and immediately escalated to ``MANUAL_RECONCILIATION`` — never
    auto-resolved.
    """
    if not run_type:
        raise ValidationError("run_type is required")
    unknown = set(domains) - SUPPORTED_DOMAINS
    if unknown:
        raise ValidationError(f"unsupported reconciliation domains: {sorted(unknown)}")
    if not domains:
        raise ValidationError("at least one domain is required")
    if readers is not None and connectors is not None:
        raise ValidationError("provide either readers or connectors, not both")

    run = ReconciliationRun(
        run_type=run_type,
        status=ReconciliationRunStatus.RUNNING,
        started_at=utc_now(),
    )
    db.add(run)
    db.flush()

    if readers is None:
        return _run_legacy(
            db, run, run_type=run_type, domains=domains, connectors=connectors or {}
        )
    return _run_canonical(
        db,
        run,
        run_type=run_type,
        domains=domains,
        readers=readers,
        scope=scope,
        scheduled=scheduled,
    )


def _run_legacy(
    db,
    run: ReconciliationRun,
    *,
    run_type: str,
    domains: list[str],
    connectors: Mapping[str, Callable[[str], list[dict[str, Any]]]],
) -> ReconciliationRun:
    """Legacy connector-callable reconciliation (backward compatible)."""
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


def _run_canonical(
    db,
    run: ReconciliationRun,
    *,
    run_type: str,
    domains: list[str],
    readers: Mapping[str, ReconciliationReader],
    scope: Mapping[str, Any] | None,
    scheduled: bool,
) -> ReconciliationRun:
    """Canonical reconciliation (plan 二.5): domain facts, fail-closed."""
    expanded: list[str] = []
    deprecations: list[str] = []
    for domain in domains:
        legacy = DEPRECATED_DOMAINS.get(domain)
        if legacy:
            expanded.extend(legacy)
            deprecations.append(
                f"domain {domain!r} is deprecated; expanded to {', '.join(legacy)}"
            )
        else:
            expanded.append(domain)

    by_domain: dict[str, dict[str, Any]] = {}
    failed_domains: list[str] = []
    skipped_domains: list[str] = []
    total_checked = 0
    total_diffs = 0
    all_verified = True

    for domain in expanded:
        reader = readers.get(domain)
        if reader is None:
            failed_domains.append(domain)
            by_domain[domain] = {
                "status": "failed",
                "reason": "no reconciliation reader",
                "checked": 0,
                "diffs": 0,
                "verified": False,
            }
            continue
        expected = _local_canonical_facts(db, domain)
        domain_scope = dict(scope or {})
        if domain in {"effect", "return"}:
            # Read-back domains need the expected entities to know what to
            # re-read on the external side.
            domain_scope["expected_states"] = expected
        try:
            actual = list(reader.read_actual(domain, domain_scope))
        except Exception as exc:
            logger.warning(
                "reconciliation_reader_failed",
                domain=domain,
                error_type=type(exc).__name__,
            )
            failed_domains.append(domain)
            by_domain[domain] = {
                "status": "failed",
                "reason": f"reader error: {type(exc).__name__}",
                "checked": 0,
                "diffs": 0,
                "verified": False,
            }
            continue
        checked, diffs, proven_empty = _compare_domain(db, run.id, domain, expected, actual)
        total_checked += checked
        total_diffs += diffs
        verified = checked > 0 or proven_empty
        all_verified = all_verified and verified
        by_domain[domain] = {
            "status": "completed",
            "checked": checked,
            "diffs": diffs,
            "provenEmpty": proven_empty,
            "verified": verified,
        }

    if scheduled and skipped_domains:
        # Scheduled reconciliation must never skip: escalate to failed.
        failed_domains.extend(skipped_domains)
        skipped_domains = []

    finished_at = utc_now()
    run.finished_at = finished_at
    incomplete = bool(failed_domains) or not all_verified
    if incomplete:
        run.status = ReconciliationRunStatus.FAILED
    elif total_diffs:
        run.status = ReconciliationRunStatus.COMPLETED_WITH_DIFFS
    else:
        run.status = ReconciliationRunStatus.COMPLETED
    run.summary = {
        "runType": run_type,
        "domains": expanded,
        "scheduled": scheduled,
        "startedAt": run.started_at.isoformat(),
        "finishedAt": finished_at.isoformat(),
        "checked": total_checked,
        "diffs": total_diffs,
        "failedDomains": failed_domains,
        "skippedDomains": skipped_domains,
        "byDomain": by_domain,
        "verified": all_verified,
        "deprecationWarnings": deprecations,
    }
    if incomplete:
        run.summary["errorCode"] = "reconciliation_incomplete"
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
    "COMPARE_FIELDS",
    "CanonicalExternalState",
    "CompositeReconciliationReader",
    "EffectReconciliationReader",
    "InMemoryReconciliationReader",
    "OdooReconciliationReader",
    "ReconciliationReader",
    "ShopifyReconciliationReader",
    "SUPPORTED_DOMAINS",
    "default_readers",
    "get_reconciliation_run",
    "list_reconciliation_runs",
    "mark_diff_manual_reconciliation",
    "odoo_reader",
    "resolve_diff",
    "run_reconciliation",
    "shopify_reader",
]

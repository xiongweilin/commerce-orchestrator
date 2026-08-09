"""Tests for the pure state-machine module (``app.services.state_machines``).

The expected transition map below is the documented vocabulary: it mirrors the
state vocabularies in ``app.schemas.events`` and the models' status enums and
must match the implementation EXACTLY.  Any drift fails loudly here.
"""

from __future__ import annotations

import pytest

from app.services.state_machines import (
    CATALOG_REVISION_STATES,
    EFFECT_LEDGER_STATES,
    FEEDBACK_STATES,
    LISTING_PUBLICATION_STATES,
    PRICE_OFFER_STATES,
    PROCUREMENT_ORDER_STATES,
    RETURN_CASE_STATES,
    SALES_ORDER_STATES,
    STATE_MACHINES,
    allowed_transitions,
    can_transition,
    check_money_invariants,
    four_eyes_area_for,
    required_roles_for,
    state_machine_states,
)

EXPECTED_VOCABULARY: dict[str, tuple[str, ...]] = {
    "Feedback": FEEDBACK_STATES,
    "CatalogRevision": CATALOG_REVISION_STATES,
    "ListingPublication": LISTING_PUBLICATION_STATES,
    "SalesOrder": SALES_ORDER_STATES,
    "ProcurementOrder": PROCUREMENT_ORDER_STATES,
    "ReturnCase": RETURN_CASE_STATES,
    "EffectLedgerEntry": EFFECT_LEDGER_STATES,
    "PriceOffer": PRICE_OFFER_STATES,
}

EXPECTED_TRANSITIONS: dict[str, dict[str, set[str]]] = {
    "Feedback": {
        "observed": {"clustered"},
        "clustered": {"candidate"},
        "candidate": {"reviewed"},
        "reviewed": {"promoted_to_sop", "promoted_to_catalog_change", "rejected"},
        "promoted_to_sop": set(),
        "promoted_to_catalog_change": set(),
        "rejected": set(),
    },
    "CatalogRevision": {
        "draft": {"normalized"},
        "normalized": {"validated"},
        "validated": {"pending_approval"},
        "pending_approval": {"approved"},
        "approved": {"official"},
        "official": {"superseded"},
        "superseded": set(),
    },
    "ListingPublication": {
        "draft": {"validated"},
        "validated": {"pending_approval"},
        "pending_approval": {"publishing", "suspended"},
        "publishing": {"active", "publish_failed"},
        "publish_failed": {"publishing"},
        "active": {"suspended", "retired"},
        "suspended": {"active", "retired"},
        "retired": set(),
    },
    "SalesOrder": {
        "received": {"validated"},
        "validated": {"accepted"},
        "accepted": {"odo_drafted"},
        "odo_drafted": {"confirmed"},
        "confirmed": {"reserved"},
        "reserved": {"picking"},
        "picking": {"shipped"},
        "shipped": {"invoiced"},
        "invoiced": {"in_payment"},
        "in_payment": {"reconciled"},
        "reconciled": {"closed"},
        "closed": set(),
    },
    "ProcurementOrder": {
        "demand_detected": {"rfq_draft"},
        "rfq_draft": {"pending_approval"},
        "pending_approval": {"po_confirmed"},
        "po_confirmed": {"partially_received", "received"},
        "partially_received": {"received"},
        "received": {"bill_posted"},
        "bill_posted": {"in_payment"},
        "in_payment": {"reconciled"},
        "reconciled": {"closed"},
        "closed": set(),
    },
    "ReturnCase": {
        "requested": {"eligibility_review"},
        "eligibility_review": {"authorized"},
        "authorized": {"received"},
        "received": {"inspected"},
        "inspected": {"disposition_approved"},
        "disposition_approved": {"credit_note_posted"},
        "credit_note_posted": {"refund_pending"},
        "refund_pending": {"refund_succeeded"},
        "refund_succeeded": {"reconciled"},
        "reconciled": {"closed"},
        "closed": set(),
    },
    "EffectLedgerEntry": {
        "planned": {"dispatched"},
        "dispatched": {"succeeded", "failed", "outcome_unknown"},
        "succeeded": {"reconciled"},
        "failed": {"dispatched", "manual_reconciliation"},
        "outcome_unknown": {"reconciled", "manual_reconciliation"},
        "manual_reconciliation": {"reconciled"},
        "reconciled": set(),
    },
    "PriceOffer": {
        "draft": {"pending_approval"},
        "pending_approval": {"approved", "rejected"},
        "approved": {"superseded"},
        "rejected": set(),
        "superseded": set(),
    },
}


def test_state_machine_vocabulary_exact() -> None:
    assert frozenset(EXPECTED_VOCABULARY) == STATE_MACHINES
    for machine, states in EXPECTED_VOCABULARY.items():
        assert tuple(state_machine_states(machine)) == states


@pytest.mark.parametrize("machine", sorted(EXPECTED_TRANSITIONS))
def test_legal_transitions_exact(machine: str) -> None:
    for from_state, expected in EXPECTED_TRANSITIONS[machine].items():
        assert allowed_transitions(machine, from_state) == expected, (
            f"{machine} legal transitions from {from_state!r} drifted"
        )


@pytest.mark.parametrize("machine", sorted(EXPECTED_TRANSITIONS))
def test_every_state_is_covered_by_transition_map(machine: str) -> None:
    mapped = set(EXPECTED_TRANSITIONS[machine])
    assert mapped == set(state_machine_states(machine))


@pytest.mark.parametrize(
    ("machine", "from_state", "to_state"),
    [
        ("Feedback", "observed", "reviewed"),
        ("Feedback", "rejected", "candidate"),
        ("CatalogRevision", "draft", "approved"),
        ("CatalogRevision", "official", "approved"),
        ("ListingPublication", "draft", "publishing"),
        ("ListingPublication", "retired", "active"),
        ("SalesOrder", "received", "confirmed"),
        ("SalesOrder", "closed", "picking"),
        ("ProcurementOrder", "demand_detected", "po_confirmed"),
        ("ProcurementOrder", "closed", "received"),
        ("ReturnCase", "requested", "authorized"),
        ("ReturnCase", "closed", "refund_pending"),
        ("EffectLedgerEntry", "planned", "succeeded"),
        ("EffectLedgerEntry", "reconciled", "dispatched"),
        ("PriceOffer", "draft", "approved"),
        ("PriceOffer", "approved", "rejected"),
    ],
)
def test_illegal_transitions_rejected_with_reason(
    machine: str, from_state: str, to_state: str
) -> None:
    ok, reason = can_transition(machine, from_state, to_state)
    assert ok is False
    assert reason


def test_unknown_machine_and_states_rejected() -> None:
    ok, reason = can_transition("NoSuchMachine", "a", "b")
    assert ok is False and "unknown state machine" in reason
    ok, reason = can_transition("Feedback", "not-a-state", "clustered")
    assert ok is False and "unknown state" in reason
    ok, reason = can_transition("Feedback", "observed", "not-a-state")
    assert ok is False and "unknown state" in reason


# ---------------------------------------------------------------------------
# Approval boundary matrix
# ---------------------------------------------------------------------------

APPROVAL_MATRIX: list[tuple[str, str, str, list[str]]] = [
    # Catalog content approval -> catalog_owner.
    ("CatalogRevision", "pending_approval", "approved", ["catalog_owner"]),
    # Listing gates -> catalog_owner.
    ("ListingPublication", "pending_approval", "publishing", ["catalog_owner"]),
    ("ListingPublication", "active", "suspended", ["catalog_owner"]),
    ("ListingPublication", "suspended", "retired", ["catalog_owner"]),
    # Pricing -> commerce_lead (finance_approver is conditional on margin).
    ("PriceOffer", "pending_approval", "approved", ["commerce_lead"]),
    # PO: procurement_lead proposes, budget_owner approves.
    ("ProcurementOrder", "demand_detected", "rfq_draft", ["procurement_lead"]),
    ("ProcurementOrder", "rfq_draft", "pending_approval", ["procurement_lead"]),
    ("ProcurementOrder", "pending_approval", "po_confirmed", ["budget_owner"]),
    # Warehouse confirmations -> warehouse_staff.
    ("ProcurementOrder", "po_confirmed", "partially_received", ["warehouse_staff"]),
    ("ProcurementOrder", "po_confirmed", "received", ["warehouse_staff"]),
    ("ProcurementOrder", "partially_received", "received", ["warehouse_staff"]),
    ("SalesOrder", "reserved", "picking", ["warehouse_staff"]),
    ("SalesOrder", "picking", "shipped", ["warehouse_staff"]),
    # Inventory reservation -> inventory_supervisor.
    ("SalesOrder", "confirmed", "reserved", ["inventory_supervisor"]),
    # Invoices -> accountant.
    ("SalesOrder", "shipped", "invoiced", ["accountant"]),
    ("SalesOrder", "invoiced", "in_payment", ["accountant"]),
    ("SalesOrder", "in_payment", "reconciled", ["accountant"]),
    ("ProcurementOrder", "received", "bill_posted", ["accountant"]),
    ("ProcurementOrder", "bill_posted", "in_payment", ["accountant"]),
    ("ProcurementOrder", "in_payment", "reconciled", ["accountant"]),
    # Refund chain: customer_service -> warehouse_staff -> accountant ->
    # finance_approver.
    ("ReturnCase", "requested", "eligibility_review", ["customer_service"]),
    ("ReturnCase", "eligibility_review", "authorized", ["customer_service"]),
    ("ReturnCase", "authorized", "received", ["warehouse_staff"]),
    ("ReturnCase", "received", "inspected", ["warehouse_staff"]),
    ("ReturnCase", "inspected", "disposition_approved", ["warehouse_staff"]),
    ("ReturnCase", "disposition_approved", "credit_note_posted", ["accountant"]),
    ("ReturnCase", "credit_note_posted", "refund_pending", ["finance_approver"]),
    ("ReturnCase", "refund_succeeded", "reconciled", ["finance_approver"]),
    # Feedback promotion decisions.
    ("Feedback", "reviewed", "promoted_to_sop", ["commerce_lead"]),
    ("Feedback", "reviewed", "promoted_to_catalog_change", ["catalog_owner"]),
]


@pytest.mark.parametrize(("machine", "from_state", "to_state", "expected_roles"), APPROVAL_MATRIX)
def test_required_roles_match_approval_matrix(
    machine: str, from_state: str, to_state: str, expected_roles: list[str]
) -> None:
    assert set(required_roles_for(machine, from_state, to_state)) == set(expected_roles)


def test_no_approval_roles_for_plain_transitions() -> None:
    assert required_roles_for("CatalogRevision", "draft", "normalized") == []
    assert required_roles_for("SalesOrder", "received", "validated") == []
    assert required_roles_for("ReturnCase", "refund_pending", "refund_succeeded") == []


# ---------------------------------------------------------------------------
# Price offer margin constraint
# ---------------------------------------------------------------------------


def test_price_offer_margin_requires_finance_approval() -> None:
    ok, reason = can_transition("PriceOffer", "pending_approval", "approved", {"margin_ok": False})
    assert ok is False and "margin" in reason
    ok, reason = can_transition(
        "PriceOffer",
        "pending_approval",
        "approved",
        {"margin_ok": False, "finance_approval": True},
    )
    assert ok is True and reason == "ok"
    ok, reason = can_transition("PriceOffer", "pending_approval", "approved", {"margin_ok": True})
    assert ok is True


# ---------------------------------------------------------------------------
# Money / inventory invariants and no-auto-approval
# ---------------------------------------------------------------------------


def test_credit_note_only_after_posted_invoice() -> None:
    # ReturnCase gate.
    ok, reason = can_transition("ReturnCase", "disposition_approved", "credit_note_posted")
    assert ok is False and "credit notes" in reason
    ok, reason = can_transition(
        "ReturnCase", "disposition_approved", "credit_note_posted", {"invoice_posted": True}
    )
    assert ok is True
    # Effect ledger credit-note operations.
    for op in ("odoo.credit_note_create", "odoo.credit_note_validate"):
        ok, reason = check_money_invariants(
            "EffectLedgerEntry", "planned", "dispatched", {"operation": op}
        )
        assert ok is False and "credit notes" in reason
        ok, reason = check_money_invariants(
            "EffectLedgerEntry",
            "planned",
            "dispatched",
            {"operation": op, "invoice_posted": True},
        )
        assert ok is True


def test_inventory_only_changes_via_moves_or_adjustments() -> None:
    for op in ("odoo.stock_move_create", "odoo.picking_validate", "odoo.receive_transfer"):
        ok, reason = check_money_invariants(
            "EffectLedgerEntry", "planned", "dispatched", {"operation": op}
        )
        assert ok is False and "inventory" in reason
        ok, reason = check_money_invariants(
            "EffectLedgerEntry",
            "planned",
            "dispatched",
            {"operation": op, "inventory_change_source": "stock_move"},
        )
        assert ok is True
        ok, reason = check_money_invariants(
            "EffectLedgerEntry",
            "planned",
            "dispatched",
            {"operation": op, "inventory_change_source": "inventory_adjustment"},
        )
        assert ok is True
    ok, reason = check_money_invariants(
        "EffectLedgerEntry",
        "planned",
        "dispatched",
        {"operation": "odoo.stock_move_create", "inventory_change_source": "import"},
    )
    assert ok is False


def test_sales_order_reservation_needs_inventory_source() -> None:
    ok, reason = can_transition("SalesOrder", "confirmed", "reserved")
    assert ok is False and "inventory" in reason
    ok, reason = can_transition(
        "SalesOrder", "confirmed", "reserved", {"reservation_source": "stock_move"}
    )
    assert ok is True


@pytest.mark.parametrize(("machine", "from_state", "to_state", "_roles"), APPROVAL_MATRIX)
def test_no_auto_approval_of_human_gates(
    machine: str, from_state: str, to_state: str, _roles: list[str]
) -> None:
    # Provide the money/inventory guards so the auto-approval guard is the
    # single reason for rejection.
    context: dict[str, object] = {"auto": True}
    if machine == "SalesOrder" and to_state == "reserved":
        context["reservation_source"] = "stock_move"
    if machine == "ReturnCase" and to_state == "credit_note_posted":
        context["invoice_posted"] = True
    ok, reason = can_transition(machine, from_state, to_state, context)
    assert ok is False
    assert "cannot be auto-approved" in reason


def test_catalog_official_requires_listing_published() -> None:
    ok, reason = can_transition("CatalogRevision", "approved", "official")
    assert ok is False and "listing publish" in reason
    ok, reason = can_transition(
        "CatalogRevision", "approved", "official", {"listing_published": True}
    )
    assert ok is True


# ---------------------------------------------------------------------------
# Effect ledger retry / reconciliation guards
# ---------------------------------------------------------------------------


def test_effect_dispatch_must_reach_outcome_before_reconciliation() -> None:
    ok, reason = can_transition("EffectLedgerEntry", "dispatched", "reconciled")
    assert ok is False and "illegal transition" in reason
    ok, reason = can_transition("EffectLedgerEntry", "dispatched", "manual_reconciliation")
    assert ok is False


def test_outcome_unknown_requires_manual_review() -> None:
    ok, reason = can_transition("EffectLedgerEntry", "outcome_unknown", "reconciled")
    assert ok is False and "manual reconciliation" in reason
    ok, reason = can_transition(
        "EffectLedgerEntry", "outcome_unknown", "reconciled", {"manual_reviewed": True}
    )
    assert ok is True
    ok, _ = can_transition("EffectLedgerEntry", "outcome_unknown", "manual_reconciliation")
    assert ok is True
    ok, _ = can_transition("EffectLedgerEntry", "manual_reconciliation", "reconciled")
    assert ok is True


def test_failed_effect_retry_is_bounded() -> None:
    ok, reason = can_transition(
        "EffectLedgerEntry", "failed", "dispatched", {"attempts": 3, "max_attempts": 3}
    )
    assert ok is False and "retry attempts" in reason
    ok, reason = can_transition(
        "EffectLedgerEntry", "failed", "dispatched", {"attempts": 2, "max_attempts": 3}
    )
    assert ok is True and reason == "ok"
    ok, _ = can_transition("EffectLedgerEntry", "failed", "manual_reconciliation")
    assert ok is True


# ---------------------------------------------------------------------------
# Four-eyes areas
# ---------------------------------------------------------------------------

FOUR_EYES_EXPECTED: list[tuple[str, str, str, str]] = [
    ("ProcurementOrder", "pending_approval", "po_confirmed", "po"),
    ("SalesOrder", "confirmed", "reserved", "inventory"),
    ("SalesOrder", "shipped", "invoiced", "accounting"),
    ("SalesOrder", "invoiced", "in_payment", "accounting"),
    ("SalesOrder", "in_payment", "reconciled", "accounting"),
    ("ProcurementOrder", "received", "bill_posted", "accounting"),
    ("ProcurementOrder", "bill_posted", "in_payment", "accounting"),
    ("ProcurementOrder", "in_payment", "reconciled", "accounting"),
    ("ReturnCase", "disposition_approved", "credit_note_posted", "accounting"),
    ("ReturnCase", "credit_note_posted", "refund_pending", "refund"),
]


@pytest.mark.parametrize(("machine", "from_state", "to_state", "area"), FOUR_EYES_EXPECTED)
def test_four_eyes_area(machine: str, from_state: str, to_state: str, area: str) -> None:
    assert four_eyes_area_for(machine, from_state, to_state) == area


def test_four_eyes_area_is_none_outside_refund_po_inventory_accounting() -> None:
    assert four_eyes_area_for("CatalogRevision", "pending_approval", "approved") is None
    assert four_eyes_area_for("ReturnCase", "requested", "eligibility_review") is None
    assert four_eyes_area_for("PriceOffer", "pending_approval", "approved") is None

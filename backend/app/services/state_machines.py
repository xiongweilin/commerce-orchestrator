"""Pure state machines: legal transitions, guard rules and approval roles.

This module has no database or DBOS imports and is fully unit-testable.
The state names below are the exact strings used by the domain models and are
the single source of truth for the workflow/service layer.  Approval roles
encode the approval-boundary matrix from ``docs/contracts/data-ownership.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# ---------------------------------------------------------------------------
# State vocabularies (exact strings)
# ---------------------------------------------------------------------------

FEEDBACK_STATES = (
    "observed",
    "clustered",
    "candidate",
    "reviewed",
    "promoted_to_sop",
    "promoted_to_catalog_change",
    "rejected",
)

CATALOG_REVISION_STATES = (
    "draft",
    "normalized",
    "validated",
    "pending_approval",
    "approved",
    "official",
    "superseded",
)

LISTING_PUBLICATION_STATES = (
    "draft",
    "validated",
    "pending_approval",
    "publishing",
    "active",
    "publish_failed",
    "suspended",
    "retired",
)

SALES_ORDER_STATES = (
    "received",
    "validated",
    "accepted",
    "odo_drafted",
    "confirmed",
    "reserved",
    "picking",
    "shipped",
    "invoiced",
    "in_payment",
    "reconciled",
    "closed",
)

PROCUREMENT_ORDER_STATES = (
    "demand_detected",
    "rfq_draft",
    "pending_approval",
    "po_confirmed",
    "partially_received",
    "received",
    "bill_posted",
    "in_payment",
    "reconciled",
    "closed",
)

RETURN_CASE_STATES = (
    "requested",
    "eligibility_review",
    "authorized",
    "received",
    "inspected",
    "disposition_approved",
    "credit_note_posted",
    "refund_pending",
    "refund_succeeded",
    "reconciled",
    "closed",
)

EFFECT_LEDGER_STATES = (
    "planned",
    "dispatched",
    "succeeded",
    "failed",
    "outcome_unknown",
    "reconciled",
    "manual_reconciliation",
)

PRICE_OFFER_STATES = (
    "draft",
    "pending_approval",
    "approved",
    "rejected",
    "superseded",
)

STATE_MACHINES = frozenset(
    (
        "Feedback",
        "CatalogRevision",
        "ListingPublication",
        "SalesOrder",
        "ProcurementOrder",
        "ReturnCase",
        "EffectLedgerEntry",
        "PriceOffer",
    )
)

_STATES: dict[str, tuple[str, ...]] = {
    "Feedback": FEEDBACK_STATES,
    "CatalogRevision": CATALOG_REVISION_STATES,
    "ListingPublication": LISTING_PUBLICATION_STATES,
    "SalesOrder": SALES_ORDER_STATES,
    "ProcurementOrder": PROCUREMENT_ORDER_STATES,
    "ReturnCase": RETURN_CASE_STATES,
    "EffectLedgerEntry": EFFECT_LEDGER_STATES,
    "PriceOffer": PRICE_OFFER_STATES,
}

# ---------------------------------------------------------------------------
# Legal transitions
# ---------------------------------------------------------------------------

_TRANSITIONS: dict[str, dict[str, set[str]]] = {
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
        # failed effects may be retried (dispatched) a limited number of times,
        # otherwise they escalate to manual reconciliation.
        "failed": {"dispatched", "manual_reconciliation"},
        # outcome_unknown must go through the reconciliation path: it may never
        # be blindly re-dispatched (no blind retry) or auto-resolved.
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

# ---------------------------------------------------------------------------
# Approval boundary matrix (state_machine, from_state, to_state) -> roles
# ---------------------------------------------------------------------------

_APPROVAL_MATRIX: dict[tuple[str, str, str], tuple[str, ...]] = {
    ("Feedback", "reviewed", "promoted_to_sop"): ("commerce_lead",),
    ("Feedback", "reviewed", "promoted_to_catalog_change"): ("catalog_owner",),
    ("CatalogRevision", "pending_approval", "approved"): ("catalog_owner",),
    ("ListingPublication", "pending_approval", "publishing"): ("catalog_owner",),
    ("ListingPublication", "active", "suspended"): ("catalog_owner",),
    ("ListingPublication", "suspended", "retired"): ("catalog_owner",),
    # Price changes: commerce_lead approves; when the proposed price violates
    # the gross-margin constraint finance_approver must also approve (enforced
    # in can_transition via context["margin_ok"] / context["finance_approval"]).
    ("PriceOffer", "pending_approval", "approved"): ("commerce_lead",),
    ("SalesOrder", "confirmed", "reserved"): ("inventory_supervisor",),
    ("SalesOrder", "reserved", "picking"): ("warehouse_staff",),
    ("SalesOrder", "picking", "shipped"): ("warehouse_staff",),
    ("SalesOrder", "shipped", "invoiced"): ("accountant",),
    ("SalesOrder", "invoiced", "in_payment"): ("accountant",),
    ("SalesOrder", "in_payment", "reconciled"): ("accountant",),
    ("ProcurementOrder", "demand_detected", "rfq_draft"): ("procurement_lead",),
    ("ProcurementOrder", "rfq_draft", "pending_approval"): ("procurement_lead",),
    # PO: procurement_lead proposes, budget_owner approves (four-eyes).
    ("ProcurementOrder", "pending_approval", "po_confirmed"): ("budget_owner",),
    ("ProcurementOrder", "po_confirmed", "partially_received"): ("warehouse_staff",),
    ("ProcurementOrder", "po_confirmed", "received"): ("warehouse_staff",),
    ("ProcurementOrder", "partially_received", "received"): ("warehouse_staff",),
    ("ProcurementOrder", "received", "bill_posted"): ("accountant",),
    ("ProcurementOrder", "bill_posted", "in_payment"): ("accountant",),
    ("ProcurementOrder", "in_payment", "reconciled"): ("accountant",),
    ("ReturnCase", "requested", "eligibility_review"): ("customer_service",),
    ("ReturnCase", "eligibility_review", "authorized"): ("customer_service",),
    # Refund chain: customer_service proposes -> warehouse confirms physical
    # goods -> finance_approver approves the amount (four-eyes) -> channel
    # adapter executes.
    ("ReturnCase", "authorized", "received"): ("warehouse_staff",),
    ("ReturnCase", "received", "inspected"): ("warehouse_staff",),
    ("ReturnCase", "inspected", "disposition_approved"): ("warehouse_staff",),
    ("ReturnCase", "disposition_approved", "credit_note_posted"): ("accountant",),
    ("ReturnCase", "credit_note_posted", "refund_pending"): ("finance_approver",),
    ("ReturnCase", "refund_succeeded", "reconciled"): ("finance_approver",),
}

# ---------------------------------------------------------------------------
# Four-eyes areas per approval transition (refund / PO / inventory / accounting)
# ---------------------------------------------------------------------------

_FOUR_EYES_AREAS: dict[tuple[str, str, str], str] = {
    ("ProcurementOrder", "pending_approval", "po_confirmed"): "po",
    ("SalesOrder", "confirmed", "reserved"): "inventory",
    ("SalesOrder", "shipped", "invoiced"): "accounting",
    ("SalesOrder", "invoiced", "in_payment"): "accounting",
    ("SalesOrder", "in_payment", "reconciled"): "accounting",
    ("ProcurementOrder", "received", "bill_posted"): "accounting",
    ("ProcurementOrder", "bill_posted", "in_payment"): "accounting",
    ("ProcurementOrder", "in_payment", "reconciled"): "accounting",
    ("ReturnCase", "disposition_approved", "credit_note_posted"): "accounting",
    ("ReturnCase", "credit_note_posted", "refund_pending"): "refund",
}

# Effect operations that touch money/inventory invariants.
_CREDIT_NOTE_EFFECT_OPS = frozenset({"odoo.credit_note_create", "odoo.credit_note_validate"})
_INVENTORY_EFFECT_OPS = frozenset(
    {"odoo.stock_move_create", "odoo.picking_validate", "odoo.receive_transfer"}
)

_DEFAULT_MAX_RETRY_ATTEMPTS = 3


def state_machine_states(state_machine: str) -> tuple[str, ...]:
    """Return the ordered state vocabulary for a machine (or ``()``)."""
    return _STATES.get(state_machine, ())


def allowed_transitions(state_machine: str, from_state: str) -> set[str]:
    """Return the set of states legally reachable from ``from_state``."""
    return set(_TRANSITIONS.get(state_machine, {}).get(from_state, set()))


def required_roles_for(state_machine: str, from_state: str, to_state: str) -> list[str]:
    """Return the human roles required to perform this transition.

    The returned roles are the static approval-boundary roles.  Conditional
    requirements (e.g. finance_approver for a margin-violating price offer)
    are enforced by :func:`can_transition` via the ``context`` mapping.
    """
    return list(_APPROVAL_MATRIX.get((state_machine, from_state, to_state), ()))


def four_eyes_area_for(state_machine: str, from_state: str, to_state: str) -> str | None:
    """Return the four-eyes area (refund/po/inventory/accounting) for a transition."""
    return _FOUR_EYES_AREAS.get((state_machine, from_state, to_state))


def check_money_invariants(
    state_machine: str,
    from_state: str,
    to_state: str,
    context: Mapping[str, Any] | None = None,
) -> tuple[bool, str]:
    """Money and inventory invariant helpers.

    - Credit notes may only be issued against posted invoices.
    - Inventory may only change through stock moves / inventory adjustments.
    - No auto-approvals: a transition that requires human roles may never be
      performed automatically (context["auto"] must not be true).
    """
    ctx = dict(context or {})
    operation = ctx.get("operation")
    if (
        state_machine == "ReturnCase"
        and to_state == "credit_note_posted"
        and ctx.get("invoice_posted") is not True
    ):
        return False, "credit notes can only be issued against posted invoices"
    if (
        state_machine == "EffectLedgerEntry"
        and operation in _CREDIT_NOTE_EFFECT_OPS
        and ctx.get("invoice_posted") is not True
    ):
        return False, "credit notes can only be issued against posted invoices"
    if (
        state_machine == "EffectLedgerEntry"
        and operation in _INVENTORY_EFFECT_OPS
        and ctx.get("inventory_change_source") not in {"stock_move", "inventory_adjustment"}
    ):
        return False, "inventory can only change through stock moves or adjustments"
    if (
        state_machine == "SalesOrder"
        and to_state == "reserved"
        and ctx.get("reservation_source") not in {"stock_move", "inventory_adjustment"}
    ):
        return False, "inventory can only change through stock moves or adjustments"
    roles = required_roles_for(state_machine, from_state, to_state)
    if roles and ctx.get("auto") is True:
        return False, "transition requires human approval and cannot be auto-approved"
    return True, "ok"


def can_transition(
    state_machine: str,
    from_state: str,
    to_state: str,
    context: Mapping[str, Any] | None = None,
) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for a transition under ``context``.

    ``context`` is a free-form mapping understood by the guards:

    - ``auto``: True when an automated system is driving the transition.
    - ``listing_published``: True when the listing publish effect succeeded
      (required for CatalogRevision ``approved -> official``).
    - ``invoice_posted``: True when the source invoice is posted (credit notes).
    - ``inventory_change_source`` / ``reservation_source``: must be one of
      ``stock_move`` or ``inventory_adjustment`` when inventory is affected.
    - ``margin_ok`` / ``finance_approval``: price-offer margin constraint.
    - ``attempts`` / ``max_attempts``: limited retry of failed effects.
    """
    states = _STATES.get(state_machine)
    if states is None:
        return False, f"unknown state machine: {state_machine}"
    if from_state not in states:
        return False, f"unknown state {from_state!r} for {state_machine}"
    if to_state not in states:
        return False, f"unknown state {to_state!r} for {state_machine}"
    if to_state not in allowed_transitions(state_machine, from_state):
        return False, f"illegal transition {from_state} -> {to_state} for {state_machine}"

    ctx = dict(context or {})

    if (
        state_machine == "CatalogRevision"
        and from_state == "approved"
        and to_state == "official"
        and ctx.get("listing_published") is not True
    ):
        return (
            False,
            "catalog revision can become official only after the listing publish succeeds",
        )

    if state_machine == "EffectLedgerEntry":
        if from_state == "dispatched" and to_state in {"reconciled", "manual_reconciliation"}:
            return False, "dispatched effects must reach a definite outcome before reconciliation"
        if (
            from_state == "outcome_unknown"
            and to_state == "reconciled"
            and ctx.get("manual_reviewed") is not True
        ):
            return False, (
                "outcome_unknown effects require the manual reconciliation path: "
                "no blind retry and no auto-resolve"
            )
        if from_state == "failed" and to_state == "dispatched":
            attempts = int(ctx.get("attempts", 0))
            max_attempts = int(ctx.get("max_attempts", _DEFAULT_MAX_RETRY_ATTEMPTS))
            if attempts >= max_attempts:
                return False, "maximum retry attempts exhausted; route to manual reconciliation"

    if (
        state_machine == "PriceOffer"
        and to_state == "approved"
        and ctx.get("margin_ok") is False
        and ctx.get("finance_approval") is not True
    ):
        return False, "price below margin constraint requires finance_approver approval"

    ok, reason = check_money_invariants(state_machine, from_state, to_state, ctx)
    if not ok:
        return False, reason
    return True, "ok"


__all__ = [
    "CATALOG_REVISION_STATES",
    "EFFECT_LEDGER_STATES",
    "FEEDBACK_STATES",
    "LISTING_PUBLICATION_STATES",
    "PRICE_OFFER_STATES",
    "PROCUREMENT_ORDER_STATES",
    "RETURN_CASE_STATES",
    "SALES_ORDER_STATES",
    "STATE_MACHINES",
    "allowed_transitions",
    "can_transition",
    "check_money_invariants",
    "four_eyes_area_for",
    "required_roles_for",
    "state_machine_states",
]

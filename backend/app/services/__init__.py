"""Workflow/service layer: commands, state machines, approvals, outbox/inbox,
effect ledger, reconciliation, audit, RBAC, webhooks and workflow queries.

Nothing in this package imports ``dbos``; tests and the API run without a live
DBOS runtime.  The DBOS wrapping lives in ``app.workflows``.
"""

from app.services.approvals import (
    FOUR_EYES_AREAS,
    VALID_DECISIONS,
    create_work_item,
    get_work_item,
    register_next_step,
    submit_decision,
)
from app.services.audit import record_audit
from app.services.commands import (
    COMMAND_HANDLERS,
    advance_entity,
    canonical_hash,
    dispatch_command,
)
from app.services.effect_ledger import mark_effect, record_effect
from app.services.outbox_inbox import (
    CONSUMER_HANDLERS,
    PRODUCERS,
    deliver_outbox,
    emit_event,
    envelope_for,
    local_consumers_for,
    process_outbox,
    register_consumer,
    register_local_consumer,
)
from app.services.rbac import ensure_roles, has_role
from app.services.reconciliation import (
    SUPPORTED_DOMAINS,
    mark_diff_manual_reconciliation,
    resolve_diff,
    run_reconciliation,
)
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
from app.services.webhooks import (
    SHOPIFY_WEBHOOK_CONSUMER,
    TOPIC_EVENT_MAP,
    ingest_shopify_webhook,
)
from app.services.workflows import get_workflow

__all__ = [
    "CATALOG_REVISION_STATES",
    "COMMAND_HANDLERS",
    "CONSUMER_HANDLERS",
    "EFFECT_LEDGER_STATES",
    "FEEDBACK_STATES",
    "FOUR_EYES_AREAS",
    "LISTING_PUBLICATION_STATES",
    "PRICE_OFFER_STATES",
    "PROCUREMENT_ORDER_STATES",
    "PRODUCERS",
    "RETURN_CASE_STATES",
    "SALES_ORDER_STATES",
    "SHOPIFY_WEBHOOK_CONSUMER",
    "STATE_MACHINES",
    "SUPPORTED_DOMAINS",
    "TOPIC_EVENT_MAP",
    "VALID_DECISIONS",
    "advance_entity",
    "allowed_transitions",
    "can_transition",
    "canonical_hash",
    "check_money_invariants",
    "create_work_item",
    "deliver_outbox",
    "dispatch_command",
    "emit_event",
    "ensure_roles",
    "envelope_for",
    "four_eyes_area_for",
    "get_work_item",
    "get_workflow",
    "has_role",
    "ingest_shopify_webhook",
    "local_consumers_for",
    "mark_diff_manual_reconciliation",
    "mark_effect",
    "process_outbox",
    "record_audit",
    "record_effect",
    "register_consumer",
    "register_local_consumer",
    "register_next_step",
    "required_roles_for",
    "resolve_diff",
    "run_reconciliation",
    "state_machine_states",
    "submit_decision",
]

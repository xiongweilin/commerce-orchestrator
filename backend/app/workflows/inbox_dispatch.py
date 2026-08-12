"""Map inbox event types to DBOS start/send actions (P7 二.2 relay).

The worker relay calls :func:`dispatch_inbox_event` for every claimed inbox
row.  Planning (:func:`plan_inbox_action`) is pure and unit-testable; the
DBOS runtime is imported lazily inside :func:`execute_inbox_action` so this
module (and the worker import path) never needs a live runtime at import.

Fixed routing:

- ``workflow.accepted`` -> start the ``(workflow_type, workflow_version)``
  definition with ``SetWorkflowID(str(workflow_run_id))`` (deterministic id:
  replayed events return the original execution);
- ``workflow.decision_recorded`` -> ``DBOS.send`` to the workflow id, topic =
  work item id, idempotency key = decision id (durable approval message);
- ``order.received`` / ``return.case_requested`` -> legacy v1 slice workflows
  (webhook-driven in-flight runs; ``app.services.webhooks`` is not part of
  WP4 and keeps creating v1 runs until WP6 migrates it).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.models.messaging import OutboxEvent

logger = get_logger("commerce.worker.relay")

# Keys copied from the decision_recorded payload into the DBOS.send message.
DECISION_MESSAGE_KEYS = (
    "work_item_id",
    "decision_id",
    "decision",
    "actor_user_id",
    "reason",
    "submitted_version",
)

V1_WORKFLOW_TYPES = frozenset({"order-to-cash", "return-to-refund"})


@dataclass(frozen=True)
class InboxAction:
    """A relay action for one inbox event (serializable plan)."""

    kind: str  # "start" | "send"
    workflow_type: str | None = None
    workflow_version: int | None = None
    workflow_id: str | None = None
    destination_id: str | None = None
    topic: str | None = None
    message: dict[str, Any] | None = None
    idempotency_key: str | None = None
    start_kwargs: dict[str, Any] | None = None


def plan_inbox_action(event: OutboxEvent) -> InboxAction:
    """Build the relay action for an event (pure; raises for unknown types)."""
    event_type = event.event_type
    payload = event.payload or {}

    if event_type == "workflow.accepted":
        workflow_type = str(payload["workflow_type"])
        workflow_version = int(payload.get("workflow_version", 2))
        workflow_id = str(payload["workflow_id"])
        return InboxAction(
            kind="start",
            workflow_type=workflow_type,
            workflow_version=workflow_version,
            workflow_id=workflow_id,
            start_kwargs={"workflow_id": workflow_id},
        )

    if event_type == "workflow.decision_recorded":
        return InboxAction(
            kind="send",
            destination_id=str(payload["workflow_id"]),
            topic=str(payload["work_item_id"]),
            message={key: payload[key] for key in DECISION_MESSAGE_KEYS if key in payload},
            idempotency_key=str(payload["decision_id"]),
        )

    if event_type == "order.received":
        return InboxAction(
            kind="start",
            workflow_type="order-to-cash",
            workflow_version=1,
            start_kwargs={"payload": payload, "correlation_id": event.correlation_id},
        )
    if event_type == "return.case_requested":
        return InboxAction(
            kind="start",
            workflow_type="return-to-refund",
            workflow_version=1,
            start_kwargs={"payload": payload, "correlation_id": event.correlation_id},
        )

    raise ValueError(f"no inbox action for event type {event_type!r}")


def _resolve_definition(workflow_type: str, workflow_version: int) -> Any:
    """Resolve the workflow function for start actions."""
    if workflow_version == 1 and workflow_type in V1_WORKFLOW_TYPES:
        from app.workflows import vertical_slice

        return {
            "order-to-cash": vertical_slice.order_to_cash_workflow,
            "return-to-refund": vertical_slice.return_to_refund_workflow,
        }[workflow_type]
    from app.workflows.definitions import resolve_definition

    return resolve_definition(workflow_type, workflow_version)


def execute_inbox_action(action: InboxAction) -> Any:
    """Execute a planned action against the DBOS runtime (lazy imports)."""
    if action.kind == "start":
        from dbos import DBOS, SetWorkflowID

        definition = _resolve_definition(action.workflow_type, action.workflow_version)
        if action.workflow_id is not None:
            with SetWorkflowID(action.workflow_id):
                return DBOS.start_workflow(definition, **(action.start_kwargs or {}))
        return DBOS.start_workflow(definition, **(action.start_kwargs or {}))
    if action.kind == "send":
        from dbos import DBOS

        return DBOS.send(
            destination_id=action.destination_id,
            topic=action.topic,
            message=action.message,
            idempotency_key=action.idempotency_key,
        )
    raise ValueError(f"unknown inbox action kind: {action.kind!r}")


def dispatch_inbox_event(event: OutboxEvent) -> Any:
    """Plan and execute the relay action for one inbox event."""
    from app.workflows.metrics import record_workflow_start

    action = plan_inbox_action(event)
    if action.kind == "start":
        record_workflow_start(
            workflow_type=action.workflow_type or "unknown",
            workflow_version=action.workflow_version or 1,
        )
    return execute_inbox_action(action)


__all__ = [
    "DECISION_MESSAGE_KEYS",
    "InboxAction",
    "dispatch_inbox_event",
    "execute_inbox_action",
    "plan_inbox_action",
]

from __future__ import annotations

from pathlib import Path


def replace(path: str, old: str, new: str, count: int = 1) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing M3 anchor in {path}: {old[:180]!r}")
    p.write_text(text.replace(old, new, count), encoding="utf-8")


def main() -> None:
    # Event vocabulary: one narrow workflow control signal, no generic recovery framework.
    replace(
        "app/schemas/events.py",
        '''WORKFLOW_EVENTS = (\n    "workflow.accepted",\n    "workflow.decision_recorded",\n    "workflow.completed",\n''',
        '''WORKFLOW_EVENTS = (\n    "workflow.accepted",\n    "workflow.decision_recorded",\n    "workflow.completion_recheck_requested",\n    "workflow.completed",\n''',
    )

    # Worker relay: turn the durable outbox event into a DBOS send to the waiting run.
    path = "app/workflows/inbox_dispatch.py"
    replace(
        path,
        "from app.models.messaging import OutboxEvent\n",
        "from app.models.messaging import OutboxEvent\n"
        "from app.services.workflow_completion import COMPLETION_RECHECK_TOPIC\n",
    )
    replace(
        path,
        '''- ``workflow.decision_recorded`` -> ``DBOS.send`` to the workflow id, topic =\n  work item id, idempotency key = decision id (durable approval message).\n''',
        '''- ``workflow.decision_recorded`` -> ``DBOS.send`` to the workflow id, topic =\n  work item id, idempotency key = decision id (durable approval message);\n- ``workflow.completion_recheck_requested`` -> ``DBOS.send`` to the same\n  workflow id on the fixed ``completion-recheck`` topic.\n''',
    )
    replace(
        path,
        '''    if event_type == "workflow.decision_recorded":\n        return InboxAction(\n            kind="send",\n            destination_id=str(payload["workflow_id"]),\n            topic=str(payload["work_item_id"]),\n            message={key: payload[key] for key in DECISION_MESSAGE_KEYS if key in payload},\n            idempotency_key=str(payload["decision_id"]),\n        )\n\n    raise ValueError(f"no inbox action for event type {event_type!r}")\n''',
        '''    if event_type == "workflow.decision_recorded":\n        return InboxAction(\n            kind="send",\n            destination_id=str(payload["workflow_id"]),\n            topic=str(payload["work_item_id"]),\n            message={key: payload[key] for key in DECISION_MESSAGE_KEYS if key in payload},\n            idempotency_key=str(payload["decision_id"]),\n        )\n\n    if event_type == "workflow.completion_recheck_requested":\n        return InboxAction(\n            kind="send",\n            destination_id=str(payload["workflow_id"]),\n            topic=COMPLETION_RECHECK_TOPIC,\n            message={\n                "workflow_id": str(payload["workflow_id"]),\n                "requested_by_user_id": str(payload["requested_by_user_id"]),\n                "recheck_event_id": str(event.event_id),\n            },\n            idempotency_key=str(event.event_id),\n        )\n\n    raise ValueError(f"no inbox action for event type {event_type!r}")\n''',
    )

    # Explicit API command: it requests a recheck but does not create proof or completion.
    path = "app/api/v1/workflows.py"
    replace(
        path,
        "from app.services.workflows import get_workflow, list_workflows\n",
        "from app.services.workflow_completion import request_completion_recheck\n"
        "from app.services.workflows import get_workflow, list_workflows\n",
    )
    replace(
        path,
        '''router = APIRouter(prefix="/v1", tags=["workflows"])\n\n\n@router.get("/workflows/{workflow_id}")\n''',
        '''router = APIRouter(prefix="/v1", tags=["workflows"])\n\n\n@router.post("/workflows/{workflow_id}/completion-recheck")\ndef recheck_workflow_completion(\n    workflow_id: uuid.UUID,\n    db: Annotated[Session, Depends(get_session)],\n    user_id: Annotated[uuid.UUID, Depends(get_current_user)],\n    _authorized: Annotated[\n        bool, Depends(require_roles("catalog_owner", "accountant", "system_admin"))\n    ],\n) -> dict[str, str]:\n    \"\"\"Request re-assessment of a currently blocked completion claim.\"\"\"\n    return request_completion_recheck(\n        db,\n        workflow_id=workflow_id,\n        requested_by_user_id=user_id,\n    )\n\n\n@router.get("/workflows/{workflow_id}")\n''',
    )

    # Completion authority: gate the only terminal producer and durably wait when blocked.
    path = "app/workflows/definitions.py"
    replace(
        path,
        "from app.services.commands import COMMAND_HANDLERS\n",
        "from app.services.commands import COMMAND_HANDLERS\n"
        "from app.services.workflow_completion import (\n"
        "    COMPLETION_RECHECK_TOPIC,\n"
        "    assess_workflow_completion,\n"
        "    completion_satisfied,\n"
        ")\n",
    )
    replace(
        path,
        '''    # Delay terminal normalisation until planned effects have been executed:\n    # v1 continuations reused by the v2 driver may mark the run completed\n    # (e.g. the closing gate) while effects are still recorded as planned.\n''',
        '''    # Delay terminal normalisation until planned effects have been executed.\n    # Domain continuations may record a completion result while effects remain\n    # planned, but ``commands._complete_run`` does not terminalize the run;\n    # terminal completion is owned only by the gated ``_complete_txn`` seam.\n''',
    )

    old_complete = '''@DBOS.transaction(name="wf2_complete")\ndef _complete_txn(workflow_id: str) -> None:\n    db = DBOS.sql_session\n    run = db.get(WorkflowRun, uuid.UUID(workflow_id))\n    if run is None or run.status in FINAL_STATUSES:\n        return\n    run.status = WorkflowRunStatus.COMPLETED\n    run.result_json = {\n        "workflowId": workflow_id,\n        "status": "completed",\n        "statusUrl": f"/v1/workflows/{workflow_id}",\n    }\n    run.finished_at = utc_now()\n    run.version += 1\n    emit_event(\n        db,\n        event_type="workflow.completed",\n        aggregate_type="workflow",\n        aggregate_id=workflow_id,\n        correlation_id=run.correlation_id,\n        producer="workflow",\n        payload={"workflow_id": workflow_id, "workflow_type": run.workflow_type},\n    )\n    record_workflow_terminal(run.workflow_type, WorkflowRunStatus.COMPLETED.value)\n    db.flush()\n\n\n'''
    new_complete = '''@DBOS.transaction(name="wf2_complete")\ndef _complete_txn(workflow_id: str) -> dict[str, Any]:\n    \"\"\"Attempt terminal completion through the bounded M3 evidence gate.\n\n    Historical terminal runs are returned as-is and are never retrospectively\n    upgraded with an M3 completion assessment. New completion claims persist\n    the exact declared-scope assessment that authorized them.\n    \"\"\"\n    db = DBOS.sql_session\n    run = db.get(WorkflowRun, uuid.UUID(workflow_id))\n    if run is None:\n        return {"completed": False, "assessment": None}\n    if run.status in FINAL_STATUSES:\n        return {\n            "completed": run.status is WorkflowRunStatus.COMPLETED,\n            "assessment": (run.result_json or {}).get("completionAssessment"),\n        }\n\n    assessment = assess_workflow_completion(db, run)\n    assessment_json = assessment.model_dump(mode="json")\n    result = dict(run.result_json or {})\n    if not completion_satisfied(assessment):\n        # Insufficient/unknown proof is neither failure nor reconciliation.\n        # Keep the workflow non-terminal; the DBOS driver waits durably for\n        # an explicit completion-recheck signal before assessing again.\n        if run.status is not WorkflowRunStatus.RUNNING:\n            run.status = WorkflowRunStatus.RUNNING\n            run.version += 1\n        result.update(\n            {\n                "workflowId": workflow_id,\n                "status": run.status.value,\n                "statusUrl": f"/v1/workflows/{workflow_id}",\n                "completionBlocked": True,\n                "completionAssessment": assessment_json,\n            }\n        )\n        run.result_json = result\n        db.flush()\n        return {"completed": False, "assessment": assessment_json}\n\n    run.status = WorkflowRunStatus.COMPLETED\n    result.update(\n        {\n            "workflowId": workflow_id,\n            "status": "completed",\n            "statusUrl": f"/v1/workflows/{workflow_id}",\n            "completionBlocked": False,\n            "completionAssessment": assessment_json,\n        }\n    )\n    run.result_json = result\n    run.finished_at = utc_now()\n    run.version += 1\n    emit_event(\n        db,\n        event_type="workflow.completed",\n        aggregate_type="workflow",\n        aggregate_id=workflow_id,\n        correlation_id=run.correlation_id,\n        producer="workflow",\n        payload={"workflow_id": workflow_id, "workflow_type": run.workflow_type},\n    )\n    record_workflow_terminal(run.workflow_type, WorkflowRunStatus.COMPLETED.value)\n    db.flush()\n    return {"completed": True, "assessment": assessment_json}\n\n\n'''
    replace(path, old_complete, new_complete)

    replace(
        path,
        '''        _complete_txn(workflow_id)\n        return _final_result(workflow_id, "completed")\n''',
        '''        completion = _complete_txn(workflow_id)\n        if completion.get("completed"):\n            return _final_result(workflow_id, "completed")\n\n        # A blocked completion is a durable wait, not a returned RUNNING result.\n        # Timeouts only renew the wait; they do not manufacture failure. Each\n        # explicit recheck signal causes the same bounded assessment to run again.\n        while True:\n            recheck = DBOS.recv(\n                topic=COMPLETION_RECHECK_TOPIC,\n                timeout_seconds=APPROVAL_TIMEOUT_SECONDS,\n            )\n            if recheck is None:\n                continue\n            completion = _complete_txn(workflow_id)\n            if completion.get("completed"):\n                return _final_result(workflow_id, "completed")\n''',
    )


if __name__ == "__main__":
    main()

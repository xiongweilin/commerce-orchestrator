from __future__ import annotations

from pathlib import Path


def replace(path: str, old: str, new: str, count: int = 1) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing M3 anchor in {path}: {old[:180]!r}")
    p.write_text(text.replace(old, new, count), encoding="utf-8")


def main() -> None:
    path = "app/workflows/definitions.py"
    replace(
        path,
        "from app.services.commands import COMMAND_HANDLERS\n",
        "from app.services.commands import COMMAND_HANDLERS\n"
        "from app.services.workflow_completion import (\n"
        "    assess_workflow_completion,\n"
        "    completion_satisfied,\n"
        ")\n",
    )

    old_complete = '''@DBOS.transaction(name="wf2_complete")\ndef _complete_txn(workflow_id: str) -> None:\n    db = DBOS.sql_session\n    run = db.get(WorkflowRun, uuid.UUID(workflow_id))\n    if run is None or run.status in FINAL_STATUSES:\n        return\n    run.status = WorkflowRunStatus.COMPLETED\n    run.result_json = {\n        "workflowId": workflow_id,\n        "status": "completed",\n        "statusUrl": f"/v1/workflows/{workflow_id}",\n    }\n    run.finished_at = utc_now()\n    run.version += 1\n    emit_event(\n        db,\n        event_type="workflow.completed",\n        aggregate_type="workflow",\n        aggregate_id=workflow_id,\n        correlation_id=run.correlation_id,\n        producer="workflow",\n        payload={"workflow_id": workflow_id, "workflow_type": run.workflow_type},\n    )\n    record_workflow_terminal(run.workflow_type, WorkflowRunStatus.COMPLETED.value)\n    db.flush()\n\n\n'''
    new_complete = '''@DBOS.transaction(name="wf2_complete")\ndef _complete_txn(workflow_id: str) -> dict[str, Any]:\n    \"\"\"Attempt terminal completion through the bounded M3 evidence gate.\n\n    Historical terminal runs are returned as-is and are never retrospectively\n    upgraded with an M3 completion assessment. New completion claims persist\n    the exact declared-scope assessment that authorized them.\n    \"\"\"\n    db = DBOS.sql_session\n    run = db.get(WorkflowRun, uuid.UUID(workflow_id))\n    if run is None:\n        return {"completed": False, "assessment": None}\n    if run.status in FINAL_STATUSES:\n        return {\n            "completed": run.status is WorkflowRunStatus.COMPLETED,\n            "assessment": (run.result_json or {}).get("completionAssessment"),\n        }\n\n    assessment = assess_workflow_completion(db, run)\n    assessment_json = assessment.model_dump(mode="json")\n    result = dict(run.result_json or {})\n    if not completion_satisfied(assessment):\n        # Do not overload NEEDS_RECONCILIATION for stale qualification or\n        # other non-reconciliation proof gaps. Keep the run non-terminal and\n        # persist the exact blockers for observation/recovery tooling.\n        if run.status is not WorkflowRunStatus.RUNNING:\n            run.status = WorkflowRunStatus.RUNNING\n            run.version += 1\n        result.update(\n            {\n                "workflowId": workflow_id,\n                "status": run.status.value,\n                "statusUrl": f"/v1/workflows/{workflow_id}",\n                "completionBlocked": True,\n                "completionAssessment": assessment_json,\n            }\n        )\n        run.result_json = result\n        db.flush()\n        return {"completed": False, "assessment": assessment_json}\n\n    run.status = WorkflowRunStatus.COMPLETED\n    result.update(\n        {\n            "workflowId": workflow_id,\n            "status": "completed",\n            "statusUrl": f"/v1/workflows/{workflow_id}",\n            "completionBlocked": False,\n            "completionAssessment": assessment_json,\n        }\n    )\n    run.result_json = result\n    run.finished_at = utc_now()\n    run.version += 1\n    emit_event(\n        db,\n        event_type="workflow.completed",\n        aggregate_type="workflow",\n        aggregate_id=workflow_id,\n        correlation_id=run.correlation_id,\n        producer="workflow",\n        payload={"workflow_id": workflow_id, "workflow_type": run.workflow_type},\n    )\n    record_workflow_terminal(run.workflow_type, WorkflowRunStatus.COMPLETED.value)\n    db.flush()\n    return {"completed": True, "assessment": assessment_json}\n\n\n'''
    replace(path, old_complete, new_complete)

    replace(
        path,
        '''        _complete_txn(workflow_id)\n        return _final_result(workflow_id, "completed")\n''',
        '''        completion = _complete_txn(workflow_id)\n        if completion.get("completed"):\n            return _final_result(workflow_id, "completed")\n        # The finite gate blocked a terminal claim. Returning the persisted\n        # non-terminal state is truthful; future recovery/replay may re-run\n        # the same deterministic gate after new qualification/realization evidence.\n        return _final_result(workflow_id, "running")\n''',
    )


if __name__ == "__main__":
    main()

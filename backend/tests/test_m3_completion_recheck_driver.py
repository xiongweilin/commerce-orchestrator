from __future__ import annotations

import os
import subprocess
import sys


def test_blocked_driver_waits_and_same_invocation_completes_after_recheck() -> None:
    """Exercise the M3 driver handoff without launching a live DBOS runtime."""
    script = r'''
from app.workflows import definitions as d

complete_calls = []
recv_calls = []

d._start_txn = lambda workflow_id: None
d._snapshot_txn = lambda workflow_id: {
    "status": "running",
    "workflow_type": "procurement",
    "pending_items": [],
    "planned_effects": [],
}

def fake_complete(workflow_id):
    complete_calls.append(workflow_id)
    return {"completed": len(complete_calls) >= 2, "assessment": {"coverage_claim": "declared-scope-only"}}

d._complete_txn = fake_complete

class FakeDBOS:
    @staticmethod
    def recv(*, topic, timeout_seconds):
        recv_calls.append((topic, timeout_seconds))
        return {"kind": "completion-recheck"}

d.DBOS = FakeDBOS
result = d._drive_v2("00000000-0000-0000-0000-000000000123", max_retries=1)
assert result["status"] == "completed", result
assert len(complete_calls) == 2, complete_calls
assert len(recv_calls) == 1, recv_calls
assert recv_calls[0][0] == d.COMPLETION_RECHECK_TOPIC
print("m3-recheck-driver-pass")
'''
    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "m3-recheck-driver-pass" in proc.stdout

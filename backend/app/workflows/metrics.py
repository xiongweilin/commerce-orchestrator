"""Worker-side Prometheus metrics and the worker HTTP probe server (P7 五.4).

The worker serves its own small HTTP endpoint on
``COMMERCE_WORKER_METRICS_PORT`` (default 9101, WP3 contract):

- ``GET /metrics`` — Prometheus exposition (heartbeat, inbox, workflow
  start/recovery/terminal, effect counts, privacy cleanup);
- ``GET /livez`` — process liveness probe for the compose healthcheck.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from prometheus_client import Counter, Gauge, generate_latest

WORKER_HEARTBEAT_LAST = Gauge(
    "commerce_worker_heartbeat_last_timestamp_seconds",
    "Unix timestamp of the last worker heartbeat upsert.",
)

WORKER_INBOX_PENDING = Gauge(
    "commerce_worker_inbox_pending",
    "Inbox rows in pending status for the worker consumer.",
    ["consumer"],
)
WORKER_INBOX_PROCESSING = Gauge(
    "commerce_worker_inbox_processing",
    "Inbox rows currently processing (leased).",
    ["consumer"],
)
WORKER_INBOX_FAILED = Gauge(
    "commerce_worker_inbox_failed",
    "Inbox rows dead-lettered (terminal failed).",
    ["consumer"],
)
WORKER_INBOX_DEAD_LETTER_TOTAL = Counter(
    "commerce_worker_inbox_dead_letter_total",
    "Inbox rows moved to terminal failed.",
    ["consumer", "event_type"],
)

WORKER_WORKFLOW_STARTED_TOTAL = Counter(
    "commerce_worker_workflow_started_total",
    "DBOS workflows started by the relay.",
    ["workflow_type", "workflow_version"],
)
WORKER_WORKFLOW_RECOVERED_TOTAL = Counter(
    "commerce_worker_workflow_recovered_total",
    "DBOS workflows recovered from a replayed inbox event.",
)
WORKER_WORKFLOW_TERMINAL_TOTAL = Counter(
    "commerce_worker_workflow_terminal_total",
    "Workflow runs reaching a terminal status.",
    ["workflow_type", "status"],
)

WORKER_EFFECT_ATTEMPTS_TOTAL = Counter(
    "commerce_worker_effect_attempts_total",
    "Effect execution attempts by target system / operation / status.",
    ["target_system", "operation", "status"],
)
WORKER_EFFECT_OUTCOME_UNKNOWN_TOTAL = Counter(
    "commerce_worker_effect_outcome_unknown_total",
    "Effects with ambiguous remote state routed to reconciliation.",
    ["target_system", "operation"],
)

WORKER_PRIVACY_CLEANUP_TOTAL = Counter(
    "commerce_worker_privacy_cleanup_total",
    "Sensitive payloads cleared by the retention job.",
)
WORKER_PRIVACY_BACKFILL_TOTAL = Counter(
    "commerce_worker_privacy_backfill_total",
    "Legacy plaintext refs encrypted by the backfill job.",
)
WORKER_PRIVACY_OVERDUE_AGE = Gauge(
    "commerce_worker_privacy_oldest_overdue_age_seconds",
    "Age of the oldest overdue sensitive payload, in seconds.",
)


def record_workflow_start(workflow_type: str, workflow_version: int | str) -> None:
    WORKER_WORKFLOW_STARTED_TOTAL.labels(
        workflow_type=workflow_type,
        workflow_version=str(workflow_version),
    ).inc()


def record_workflow_recovered() -> None:
    WORKER_WORKFLOW_RECOVERED_TOTAL.inc()


def record_workflow_terminal(workflow_type: str, status: str) -> None:
    WORKER_WORKFLOW_TERMINAL_TOTAL.labels(
        workflow_type=workflow_type,
        status=status,
    ).inc()


def record_effect_attempt(
    *,
    target_system: str,
    operation: str,
    status: str,
) -> None:
    WORKER_EFFECT_ATTEMPTS_TOTAL.labels(
        target_system=target_system,
        operation=operation,
        status=status,
    ).inc()


def record_effect_outcome_unknown(target_system: str, operation: str) -> None:
    WORKER_EFFECT_OUTCOME_UNKNOWN_TOTAL.labels(
        target_system=target_system,
        operation=operation,
    ).inc()


def record_privacy_cleanup(count: int) -> None:
    WORKER_PRIVACY_CLEANUP_TOTAL.inc(count)


def record_privacy_backfill(count: int) -> None:
    WORKER_PRIVACY_BACKFILL_TOTAL.inc(count)


def set_inbox_gauges(consumer: str, pending: int, processing: int, failed: int) -> None:
    WORKER_INBOX_PENDING.labels(consumer=consumer).set(pending)
    WORKER_INBOX_PROCESSING.labels(consumer=consumer).set(processing)
    WORKER_INBOX_FAILED.labels(consumer=consumer).set(failed)


def set_heartbeat_timestamp(timestamp: float) -> None:
    WORKER_HEARTBEAT_LAST.set(timestamp)


def start_metrics_server(port: int = 9101) -> None:
    """Start the worker /metrics + /livez HTTP server on a background thread."""
    from prometheus_client import REGISTRY

    class _WorkerHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path in ("/livez", "/livez/"):
                body = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path in ("/metrics", "/metrics/"):
                data = generate_latest(REGISTRY)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            del format, args  # keep the probe endpoint quiet

    server = ThreadingHTTPServer(("0.0.0.0", port), _WorkerHandler)
    thread = threading.Thread(target=server.serve_forever, name="worker-metrics", daemon=True)
    thread.start()


__all__ = [
    "set_heartbeat_timestamp",
    "set_inbox_gauges",
    "start_metrics_server",
    "record_effect_attempt",
    "record_effect_outcome_unknown",
    "record_privacy_backfill",
    "record_privacy_cleanup",
    "record_workflow_recovered",
    "record_workflow_start",
    "record_workflow_terminal",
]

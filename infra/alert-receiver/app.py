"""本地 alert receiver：接收 Alertmanager webhook，仅记录告警元数据。

记录字段限定为：receipt_time、status（firing/resolved）、alertname、severity、
runbook_url、starts_at。绝不记录业务 payload（labels/annotations 中的业务数据
一律丢弃）。

端点：
  POST /alert   Alertmanager webhook（兼容 v4：data.alerts[]）
  GET  /healthz 存活探针
  GET  /        简述

每条告警写一行 JSON 到 stdout（compose logs 可审计）并追加到
/var/lib/alerts/alerts.jsonl（命名卷 alert-logs）。
"""

from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ALERTS_FILE = Path("/var/lib/alerts/alerts.jsonl")


def _record(entry: dict) -> None:
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    print(line, flush=True)
    try:
        with ALERTS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:  # 只读卷等场景不阻断接收
        print(f"alert_receiver: cannot persist alert log: {exc}", flush=True)


def _handle_webhook(payload: dict) -> None:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    alerts = data.get("alerts") if data else payload.get("alerts")
    if alerts is None:
        alerts = [payload]  # 兼容直接转发单条告警
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for alert in alerts or []:
        labels = alert.get("labels", {}) if isinstance(alert.get("labels"), dict) else {}
        annotations = (
            alert.get("annotations", {}) if isinstance(alert.get("annotations"), dict) else {}
        )
        _record(
            {
                "receipt_time": now,
                "status": alert.get("status", "unknown"),
                "alertname": labels.get("alertname", ""),
                "severity": labels.get("severity", ""),
                "runbook_url": annotations.get("runbook_url", annotations.get("runbookUrl", "")),
                "starts_at": alert.get("startsAt", ""),
            }
        )


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send(200, b'{"status":"ok"}')
        else:
            self._send(200, b"commerce-orchestrator local alert receiver\n", "text/plain")

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            _handle_webhook(payload)
            self._send(200, b'{"received":true}')
        except Exception as exc:  # noqa: BLE001 — receiver 永不因坏 payload 崩溃
            print(f"alert_receiver: bad webhook: {exc!r}", flush=True)
            self._send(
                400,
                json.dumps({"received": False, "error": str(exc)}).encode("utf-8"),
            )

    def log_message(self, fmt: str, *args) -> None:
        # 只记录请求行（无业务字段）；静默 /healthz 探针噪声
        if self.path != "/healthz":
            super().log_message(fmt, *args)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9116
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"alert_receiver listening on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

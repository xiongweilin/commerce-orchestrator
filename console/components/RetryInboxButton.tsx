"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";

const NOT_READY_CODES = new Set([
  "not_found",
  "method_not_allowed",
  "not_implemented",
  "http_404",
  "http_405",
  "http_501",
]);

/**
 * 失败 inbox 重试按钮：POST /v1/ops/inbox/{id}/retry（必须携带 Idempotency-Key，计划 §2.2）。
 * 后端 ops 接口已实现（WP6 落地）；未就绪（404/405/501）时提示接口错误。
 */
export default function RetryInboxButton({ eventId }: { eventId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "success" | "error" | "not_ready"; text: string } | null>(null);

  const handleRetry = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await api.post(`/v1/ops/inbox/${encodeURIComponent(eventId)}/retry`, undefined, {
        idempotencyKey: newIdempotencyKey(),
      });
      setMessage({ kind: "success", text: "已提交重试，列表已刷新。" });
      window.setTimeout(() => router.refresh(), 500);
    } catch (err) {
      if (err instanceof ApiError) {
        if (
          err.status === 404 ||
          err.status === 405 ||
          err.status === 501 ||
          NOT_READY_CODES.has(err.code)
        ) {
          setMessage({ kind: "not_ready", text: `接口未就绪：后端返回 ${err.message}。` });
        } else {
          setMessage({
            kind: "error",
            text: err.correlationId ? `${err.message}（关联ID：${err.correlationId}）` : err.message,
          });
        }
      } else {
        setMessage({ kind: "error", text: "重试提交失败，请稍后重试。" });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <button type="button" className="btn btn-secondary" onClick={handleRetry} disabled={busy}>
        {busy ? "重试中…" : "重试"}
      </button>
      {message && message.kind === "success" && <div className="success-box">{message.text}</div>}
      {message && message.kind === "not_ready" && <div className="error-box warning">{message.text}</div>}
      {message && message.kind === "error" && <div className="error-box">{message.text}</div>}
    </div>
  );
}

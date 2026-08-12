"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import type { AcceptedResponse } from "@/lib/types";

/** 发起对账按钮：POST /v1/reconciliations（携带新的 Idempotency-Key）。 */
export default function TriggerReconciliation() {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<AcceptedResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleClick = async () => {
    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.post<AcceptedResponse>(
        "/v1/reconciliations",
        { scope: "reconciliation" },
        { idempotencyKey: newIdempotencyKey() },
      );
      setResult(res);
      window.setTimeout(() => router.refresh(), 700);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.correlationId ? `${err.message}（关联ID：${err.correlationId}）` : err.message);
      } else {
        setError("发起失败，请稍后重试。");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="trigger-reconcile">
      <button type="button" className="btn btn-primary" onClick={handleClick} disabled={submitting}>
        {submitting ? "发起中…" : "发起对账"}
      </button>
      {error && <div className="error-box">{error}</div>}
      {result && (
        <div className="success-box">
          已受理：工作流 {result.workflowId}（{result.status}）
          <a
            href={`/workflows/${encodeURIComponent(result.workflowId)}`}
            style={{ marginLeft: 8 }}
          >
            查看工作流
          </a>
        </div>
      )}
    </div>
  );
}

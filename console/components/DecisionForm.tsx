"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import type { WorkItemDecisionResponse } from "@/lib/types";

/**
 * 工作项决策表单：批准 / 拒绝 + 原因。
 * POST /v1/work-items/{id}/decisions，成功后刷新页面（列表项消失、工作流状态更新）。
 */
export default function DecisionForm({
  workItemId,
  expectedWorkflowVersion,
}: {
  workItemId: string;
  expectedWorkflowVersion: number;
}) {
  const router = useRouter();
  const [decision, setDecision] = useState<"approve" | "reject">("approve");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setSuccess(null);
    try {
      const result = await api.post<WorkItemDecisionResponse>(`/v1/work-items/${workItemId}/decisions`, {
        decision,
        reason: reason.trim() ? reason.trim() : undefined,
        expectedWorkflowVersion,
      }, { idempotencyKey: newIdempotencyKey() });
      setSuccess(`已${decision === "approve" ? "批准" : "拒绝"}（状态：${result.status}）`);
      // 稍等片刻让用户看到结果提示，再刷新列表
      window.setTimeout(() => router.refresh(), 700);
    } catch (err) {
      if (err instanceof ApiError && err.code === "workflow_version_conflict") {
        setError("版本冲突（409）：该工作流已被其他操作更新，请刷新页面后重试。");
      } else if (err instanceof ApiError) {
        setError(err.correlationId ? `${err.message}（关联ID：${err.correlationId}）` : err.message);
      } else {
        setError("提交失败，请稍后重试。");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="decision-form" onSubmit={handleSubmit}>
      <input type="hidden" name="expectedWorkflowVersion" value={expectedWorkflowVersion} />
      <div className="decision-actions">
        <label className="radio">
          <input
            type="radio"
            name="decision"
            value="approve"
            checked={decision === "approve"}
            onChange={() => setDecision("approve")}
          />
          批准
        </label>
        <label className="radio">
          <input
            type="radio"
            name="decision"
            value="reject"
            checked={decision === "reject"}
            onChange={() => setDecision("reject")}
          />
          拒绝
        </label>
      </div>
      <textarea
        className="input textarea"
        placeholder="原因（可选）"
        rows={2}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
      />
      <div className="form-footer">
        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting ? "提交中…" : "提交决策"}
        </button>
      </div>
      {error && <div className="error-box">{error}</div>}
      {success && <div className="success-box">{success}</div>}
    </form>
  );
}

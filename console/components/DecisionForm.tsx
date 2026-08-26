"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import type { WorkItemDecisionResponse } from "@/lib/types";

interface AuthorizedDecisionResponse extends WorkItemDecisionResponse {
  authorizationId: string;
}

/**
 * 工作项决策表单。listing-publication 的批准动作显式调用 authorized-decision；
 * 普通 Decision 不会自动产生 ExecutionAuthorization。
 */
export default function DecisionForm({
  workItemId,
  expectedWorkflowVersion,
  requiresExecutionAuthorization = false,
}: {
  workItemId: string;
  expectedWorkflowVersion: number;
  requiresExecutionAuthorization?: boolean;
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
      const authorized = requiresExecutionAuthorization && decision === "approve";
      const path = authorized
        ? `/v1/work-items/${workItemId}/authorized-decisions`
        : `/v1/work-items/${workItemId}/decisions`;
      const body = authorized
        ? {
            decision: "approve" as const,
            reason: reason.trim() ? reason.trim() : undefined,
            expectedWorkflowVersion,
            scope: { purpose: "publish", channel: "shopify" },
          }
        : {
            decision,
            reason: reason.trim() ? reason.trim() : undefined,
            expectedWorkflowVersion,
          };
      const result = await api.post<WorkItemDecisionResponse | AuthorizedDecisionResponse>(
        path,
        body,
        { idempotencyKey: newIdempotencyKey() },
      );
      const authId = "authorizationId" in result ? `，授权 ${result.authorizationId}` : "";
      setSuccess(`已${decision === "approve" ? "批准" : "拒绝"}（状态：${result.status}${authId}）`);
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
          {requiresExecutionAuthorization ? "批准并显式授权发布" : "批准"}
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
      {requiresExecutionAuthorization && decision === "approve" ? (
        <p className="muted">
          此动作会同时记录 Decision 与独立的 exact-scope ExecutionAuthorization；二者不是同一事实。
        </p>
      ) : null}
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
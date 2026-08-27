"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import type { WorkItemDecisionResponse } from "@/lib/types";

type AuthorizationProfile =
  | "listing-publication-v1"
  | "return-credit-note-v1"
  | "return-refund-v1";

interface AuthorizedDecisionResponse extends WorkItemDecisionResponse {
  authorizationId: string;
  authorizationProfile: AuthorizationProfile;
}

interface WorkItemProjection {
  workItemId: string;
  authorizationProfile: AuthorizationProfile | null;
}

interface WorkItemPage {
  items: WorkItemProjection[];
}

const profileLabel: Record<AuthorizationProfile, string> = {
  "listing-publication-v1": "批准并显式授权发布",
  "return-credit-note-v1": "批准并授权 Odoo 贷项通知单",
  "return-refund-v1": "批准并授权 Shopify 退款",
};

export default function DecisionForm({
  workItemId,
  expectedWorkflowVersion,
  authorizationProfile = null,
  requiresExecutionAuthorization = false,
}: {
  workItemId: string;
  expectedWorkflowVersion: number;
  authorizationProfile?: AuthorizationProfile | null;
  /** Transitional compatibility for the listing workflow detail page. */
  requiresExecutionAuthorization?: boolean;
}) {
  const router = useRouter();
  const explicitProfile =
    authorizationProfile ??
    (requiresExecutionAuthorization ? ("listing-publication-v1" as const) : null);
  const [resolvedProfile, setResolvedProfile] = useState<AuthorizationProfile | null>(
    explicitProfile,
  );
  const [profileResolved, setProfileResolved] = useState(explicitProfile !== null);
  const [decision, setDecision] = useState<"approve" | "reject">("approve");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (explicitProfile !== null) {
      setResolvedProfile(explicitProfile);
      setProfileResolved(true);
      return;
    }
    let cancelled = false;
    setProfileResolved(false);
    api
      .get<WorkItemPage>("/v1/work-items?status=pending&limit=500")
      .then((page) => {
        if (cancelled) return;
        const item = page.items.find((candidate) => candidate.workItemId === workItemId);
        setResolvedProfile(item?.authorizationProfile ?? null);
        setProfileResolved(true);
      })
      .catch(() => {
        if (cancelled) return;
        setError("无法确认该工作项的执行授权配置；为避免绕过授权要求，审批已暂停。");
        setProfileResolved(false);
      });
    return () => {
      cancelled = true;
    };
  }, [explicitProfile, workItemId]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!profileResolved) {
      setError("执行授权配置尚未确认，不能提交决策。");
      return;
    }
    setSubmitting(true);
    setError(null);
    setSuccess(null);
    try {
      const authorized = resolvedProfile !== null && decision === "approve";
      const path = authorized
        ? `/v1/work-items/${workItemId}/authorized-decisions`
        : `/v1/work-items/${workItemId}/decisions`;
      const body = authorized
        ? {
            decision: "approve" as const,
            reason: reason.trim() ? reason.trim() : undefined,
            expectedWorkflowVersion,
            ...(resolvedProfile === "listing-publication-v1"
              ? { scope: { purpose: "publish", channel: "shopify" } }
              : {}),
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
          {resolvedProfile ? profileLabel[resolvedProfile] : "批准"}
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
      {resolvedProfile && decision === "approve" ? (
        <p className="muted">
          此动作记录 Decision 与独立的 exact-scope ExecutionAuthorization；允许的系统、操作与业务范围由服务器计算。
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
        <button
          type="submit"
          className="btn btn-primary"
          disabled={submitting || !profileResolved}
        >
          {submitting ? "提交中…" : profileResolved ? "提交决策" : "确认授权配置中…"}
        </button>
      </div>
      {error && <div className="error-box">{error}</div>}
      {success && <div className="success-box">{success}</div>}
    </form>
  );
}

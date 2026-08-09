import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { getServerToken } from "@/lib/server-auth";
import type { PageEnvelope, WorkItem } from "@/lib/types";
import StatusBadge from "@/components/StatusBadge";
import ErrorBox from "@/components/ErrorBox";
import RefreshButton from "@/components/RefreshButton";
import DecisionForm from "@/components/DecisionForm";
import { formatTime, shortId } from "@/lib/format";

export const dynamic = "force-dynamic";

const LIMIT = 50;

export default async function ApprovalsPage() {
  let data: PageEnvelope<WorkItem> | null = null;
  let error: string | null = null;
  try {
    data = await api.get<PageEnvelope<WorkItem>>(`/v1/work-items?status=pending&limit=${LIMIT}`, {
      token: await getServerToken(),
    });
  } catch (err) {
    error =
      err instanceof ApiError
        ? err.correlationId
          ? `${err.message}（关联ID：${err.correlationId}）`
          : err.message
        : "无法连接后端服务";
  }

  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>审批收件箱</h1>
          <p className="page-sub">
            待审批工作项（{total}）· GET /v1/work-items?status=pending&amp;limit=
            {LIMIT}
          </p>
        </div>
        <div className="page-actions">
          <RefreshButton />
        </div>
      </div>

      {error && <ErrorBox error={error} title="加载失败" />}

      <div className="approvals">
        {items.length === 0 && !error && (
          <div className="card">
            <p className="empty">收件箱为空，暂无待审批工作项。</p>
          </div>
        )}
        {items.map((item) => (
          <div key={item.workItemId} className="card approval-card">
            <div className="approval-head">
              <div>
                <strong className="approval-title">{item.title || item.workItemId}</strong>
                <div className="approval-meta">
                  <span className="detail-label">类型</span>
                  <span>{item.kind}</span>
                  <span className="detail-label">工作流</span>
                  <Link
                    className="mono"
                    href={`/workflows/${encodeURIComponent(item.workflowId)}`}
                    title={item.workflowId}
                  >
                    {shortId(item.workflowId)}
                  </Link>
                  <span className="detail-label">到期时间</span>
                  <span>{formatTime(item.expiresAt)}</span>
                </div>
              </div>
              <StatusBadge status={item.status} />
            </div>
            <DecisionForm
              workItemId={item.workItemId}
              expectedWorkflowVersion={item.expectedWorkflowVersion}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

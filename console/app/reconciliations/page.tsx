import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { getServerToken } from "@/lib/server-auth";
import type { PageEnvelope, ReconciliationRun } from "@/lib/types";
import StatusBadge from "@/components/StatusBadge";
import ErrorBox from "@/components/ErrorBox";
import RefreshButton from "@/components/RefreshButton";
import TriggerReconciliation from "@/components/TriggerReconciliation";
import { formatTime, shortId } from "@/lib/format";

export const dynamic = "force-dynamic";

/** summary 的差异数字段名后端尚未固定，兼容几种常见命名。 */
function diffCountOf(summary: Record<string, unknown> | null | undefined): number | null {
  if (!summary) return null;
  for (const key of ["diffCount", "diff_count", "unmatched", "mismatchCount", "unresolved"]) {
    const value = summary[key];
    if (typeof value === "number") return value;
  }
  return null;
}

export default async function ReconciliationsPage() {
  let data: PageEnvelope<ReconciliationRun> | null = null;
  let error: string | null = null;
  try {
    data = await api.get<PageEnvelope<ReconciliationRun>>("/v1/reconciliations?limit=50", {
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

  const runs = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>对账</h1>
          <p className="page-sub">对账运行与差异处理（{total}）· GET /v1/reconciliations</p>
        </div>
        <div className="page-actions">
          <TriggerReconciliation />
          <RefreshButton />
        </div>
      </div>

      {error && <ErrorBox error={error} title="加载失败" />}

      <div className="card table-card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>对账ID</th>
                <th>类型</th>
                <th>状态</th>
                <th>开始时间</th>
                <th>结束时间</th>
                <th>差异数</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => {
                const diffCount = diffCountOf(run.summary);
                return (
                  <tr key={run.runId}>
                    <td>
                      <Link
                        className="mono"
                        href={`/reconciliations/${encodeURIComponent(run.runId)}`}
                        title={run.runId}
                      >
                        {shortId(run.runId, 10)}
                      </Link>
                    </td>
                    <td>{run.runType}</td>
                    <td>
                      <StatusBadge status={run.status} />
                    </td>
                    <td>{formatTime(run.startedAt)}</td>
                    <td>{formatTime(run.finishedAt)}</td>
                    <td>{diffCount !== null ? diffCount : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {runs.length === 0 && !error && <p className="empty">暂无对账运行，点击“发起对账”创建。</p>}
      </div>
    </div>
  );
}

import Link from "next/link";
import { notFound } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { getServerToken } from "@/lib/server-auth";
import type { ReconciliationDetail } from "@/lib/types";
import StatusBadge from "@/components/StatusBadge";
import ErrorBox from "@/components/ErrorBox";
import RefreshButton from "@/components/RefreshButton";
import ResolveDiffForm from "@/components/ResolveDiffForm";
import { formatTime, jsonText } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function ReconciliationDetailPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = await params;

  let run: ReconciliationDetail | null = null;
  let error: string | null = null;
  try {
    run = await api.get<ReconciliationDetail>(`/v1/reconciliations/${encodeURIComponent(runId)}`, {
      token: await getServerToken(),
    });
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      notFound();
    }
    error =
      err instanceof ApiError
        ? err.correlationId
          ? `${err.message}（关联ID：${err.correlationId}）`
          : err.message
        : "无法连接后端服务";
  }

  if (!run) {
    return (
      <div className="page">
        <h1>对账详情</h1>
        <ErrorBox error={error ?? "未知错误"} title="加载失败" />
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>对账详情</h1>
          <p className="mono">Run：{run.runId}</p>
        </div>
        <div className="page-actions">
          <Link className="btn btn-secondary" href="/reconciliations">
            返回对账列表
          </Link>
          <RefreshButton />
        </div>
      </div>

      <div className="card">
        <div className="detail-header">
          <StatusBadge status={run.status} />
          <span className="detail-label">类型</span>
          <span>{run.runType}</span>
          <span className="detail-label">开始</span>
          <span>{formatTime(run.startedAt)}</span>
          <span className="detail-label">结束</span>
          <span>{formatTime(run.finishedAt)}</span>
          <span className="detail-label">差异数</span>
          <span>{run.diffs.length}</span>
        </div>
        {run.summary && (
          <details>
            <summary>摘要（summary）</summary>
            <pre>{jsonText(run.summary)}</pre>
          </details>
        )}
      </div>

      <div className="card table-card">
        <h2>差异明细（{run.diffs.length}）</h2>
        {run.diffs.length === 0 ? (
          <p className="empty">本次对账无差异。</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>域</th>
                  <th>实体</th>
                  <th>期望值（expected）</th>
                  <th>实际值（actual）</th>
                  <th>状态</th>
                  <th>备注</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {run.diffs.map((diff) => (
                  <tr key={diff.diffId}>
                    <td>{diff.domain}</td>
                    <td>
                      <span>{diff.entityType}</span>
                      <span className="mono block">{diff.entityId}</span>
                    </td>
                    <td>
                      <pre className="cell-pre">{jsonText(diff.expected)}</pre>
                    </td>
                    <td>
                      <pre className="cell-pre">{jsonText(diff.actual)}</pre>
                    </td>
                    <td>
                      <StatusBadge status={diff.status} />
                    </td>
                    <td>{diff.resolutionNote ?? "—"}</td>
                    <td>
                      <ResolveDiffForm runId={run.runId} diffId={diff.diffId} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

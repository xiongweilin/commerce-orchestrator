import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { getServerToken } from "@/lib/server-auth";
import type { PageEnvelope, WorkflowSummary } from "@/lib/types";
import StatusBadge from "@/components/StatusBadge";
import ErrorBox from "@/components/ErrorBox";
import RefreshButton from "@/components/RefreshButton";
import WorkflowStatusFilter from "@/components/WorkflowStatusFilter";
import { formatTime, shortId } from "@/lib/format";

export const dynamic = "force-dynamic";

const DEFAULT_LIMIT = 20;

export default async function WorkflowsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const status = typeof sp.status === "string" ? sp.status : "";
  const limit = Math.min(100, Math.max(1, Number(typeof sp.limit === "string" ? sp.limit : "") || DEFAULT_LIMIT));
  const offset = Math.max(0, Number(typeof sp.offset === "string" ? sp.offset : "") || 0);

  const query = new URLSearchParams();
  if (status) query.set("status", status);
  query.set("limit", String(limit));
  query.set("offset", String(offset));

  let data: PageEnvelope<WorkflowSummary> | null = null;
  let error: string | null = null;
  try {
    data = await api.get<PageEnvelope<WorkflowSummary>>(`/v1/workflows?${query.toString()}`, {
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
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));

  const hrefFor = (nextOffset: number) => {
    const q = new URLSearchParams();
    if (status) q.set("status", status);
    q.set("limit", String(limit));
    q.set("offset", String(nextOffset));
    return `/workflows?${q.toString()}`;
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>工作流</h1>
          <p className="page-sub">共 {total} 条 · GET /v1/workflows?status=&amp;limit=&amp;offset=</p>
        </div>
        <div className="page-actions">
          <WorkflowStatusFilter current={status} />
          <RefreshButton />
        </div>
      </div>

      {error && <ErrorBox error={error} title="加载失败" />}

      <div className="card table-card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>工作流ID</th>
                <th>类型</th>
                <th>状态</th>
                <th>当前步骤</th>
                <th>更新时间</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.workflowId}>
                  <td>
                    <Link
                      className="mono"
                      href={`/workflows/${encodeURIComponent(item.workflowId)}`}
                      title={item.workflowId}
                    >
                      {shortId(item.workflowId)}
                    </Link>
                  </td>
                  <td>{item.type}</td>
                  <td>
                    <StatusBadge status={item.status} />
                  </td>
                  <td>{item.currentStep ?? "—"}</td>
                  <td>{formatTime(item.updatedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {items.length === 0 && !error && <p className="empty">暂无工作流</p>}
        <div className="pagination">
          <span>
            第 {page} / {pages} 页（每页 {limit} 条）
          </span>
          <div>
            <Link
              className={`btn btn-secondary${offset <= 0 ? " disabled" : ""}`}
              aria-disabled={offset <= 0}
              href={offset > 0 ? hrefFor(Math.max(0, offset - limit)) : "#"}
            >
              上一页
            </Link>
            <Link
              className={`btn btn-secondary${offset + limit >= total ? " disabled" : ""}`}
              aria-disabled={offset + limit >= total}
              href={offset + limit < total ? hrefFor(offset + limit) : "#"}
            >
              下一页
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

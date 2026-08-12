import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { getServerToken, getServerUser } from "@/lib/server-auth";
import type { OpsInboxEvent, PageEnvelope } from "@/lib/types";
import StatusBadge from "@/components/StatusBadge";
import ErrorBox from "@/components/ErrorBox";
import RefreshButton from "@/components/RefreshButton";
import RetryInboxButton from "@/components/RetryInboxButton";
import { formatTime, shortId } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * Failed inbox 查看 + retry 页面（仅 system_admin，计划 §2.2 / §四.2）。
 * GET /v1/ops/inbox?status=failed；retry 必须携带 Idempotency-Key。
 * 后端 ops 接口已实现（WP6 落地）；未就绪时列表显示错误提示。
 */
export default async function OpsInboxPage() {
  const user = await getServerUser();
  if (!user?.roles?.includes("system_admin")) {
    return (
      <div className="page">
        <h1>运维收件箱</h1>
        <div className="card">
          <p className="empty">无权访问：本页面仅对 system_admin 开放。</p>
        </div>
      </div>
    );
  }

  const token = await getServerToken();
  let data: PageEnvelope<OpsInboxEvent> | null = null;
  let error: string | null = null;
  try {
    data = await api.get<PageEnvelope<OpsInboxEvent>>("/v1/ops/inbox?status=failed", { token });
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 501 || err.status === 405)) {
      error = `接口未就绪：${err.message}`;
    } else {
      error =
        err instanceof ApiError
          ? err.correlationId
            ? `${err.message}（关联ID：${err.correlationId}）`
            : err.message
          : "无法连接后端服务";
    }
  }

  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>运维收件箱</h1>
          <p className="page-sub">
            Failed inbox（{total}）· GET /v1/ops/inbox?status=failed · 仅 system_admin
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn btn-secondary" href="/">
            返回概览
          </Link>
          <RefreshButton />
        </div>
      </div>

      {error && <ErrorBox error={error} title="加载失败" />}

      <div className="card table-card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>事件ID</th>
                <th>Consumer</th>
                <th>状态</th>
                <th>尝试次数</th>
                <th>下次重试</th>
                <th>Lease 截止</th>
                <th>最后错误</th>
                <th>接收时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((event) => (
                <tr key={event.eventId}>
                  <td className="mono" title={event.eventId}>
                    {shortId(event.eventId)}
                  </td>
                  <td className="mono">{event.consumer}</td>
                  <td>
                    <StatusBadge status={event.status} />
                  </td>
                  <td>{event.attempts ?? "—"}</td>
                  <td>{formatTime(event.nextAttemptAt)}</td>
                  <td>{formatTime(event.leaseUntil)}</td>
                  <td title={event.lastError ?? undefined}>{event.lastError ?? "—"}</td>
                  <td>{formatTime(event.receivedAt)}</td>
                  <td>
                    <RetryInboxButton eventId={event.eventId} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {items.length === 0 && !error && <p className="empty">暂无 failed inbox 事件。</p>}
      </div>
    </div>
  );
}

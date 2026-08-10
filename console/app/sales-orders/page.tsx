import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { getServerToken } from "@/lib/server-auth";
import type { PageEnvelope, SalesOrderSummary } from "@/lib/types";
import StatusBadge from "@/components/StatusBadge";
import ErrorBox from "@/components/ErrorBox";
import RefreshButton from "@/components/RefreshButton";
import { formatTime } from "@/lib/format";

export const dynamic = "force-dynamic";

const DEFAULT_LIMIT = 20;

export default async function SalesOrdersPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const status = typeof sp.status === "string" ? sp.status : "";
  const limit = Math.min(
    100,
    Math.max(1, Number(typeof sp.limit === "string" ? sp.limit : "") || DEFAULT_LIMIT),
  );
  const offset = Math.max(0, Number(typeof sp.offset === "string" ? sp.offset : "") || 0);

  const query = new URLSearchParams();
  if (status) query.set("status", status);
  query.set("limit", String(limit));
  query.set("offset", String(offset));

  let data: PageEnvelope<SalesOrderSummary> | null = null;
  let error: string | null = null;
  try {
    data = await api.get<PageEnvelope<SalesOrderSummary>>(
      `/v1/sales-orders?${query.toString()}`,
      { token: await getServerToken() },
    );
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
    return `/sales-orders?${q.toString()}`;
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>销售订单</h1>
          <p className="page-sub">
            共 {total} 条 · GET /v1/sales-orders?status=&amp;limit=&amp;offset=
          </p>
        </div>
        <div className="page-actions">
          <RefreshButton />
        </div>
      </div>

      {error && <ErrorBox error={error} title="加载失败" />}

      <div className="card table-card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>订单号</th>
                <th>Shopify 订单</th>
                <th>客户</th>
                <th>状态</th>
                <th>金额</th>
                <th>创建时间</th>
                <th>更新时间</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.orderRef}>
                  <td className="mono">{item.orderRef}</td>
                  <td className="mono">{item.shopifyOrderId ?? "—"}</td>
                  <td>{item.customerRef ?? "—"}</td>
                  <td>
                    <StatusBadge status={item.status} />
                  </td>
                  <td>
                    {item.total ? `${item.currency} ${item.total}` : "—"}
                  </td>
                  <td>{formatTime(item.createdAt)}</td>
                  <td>{formatTime(item.updatedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {items.length === 0 && !error && <p className="empty">暂无销售订单</p>}
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

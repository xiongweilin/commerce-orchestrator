import Link from "next/link";
import { api } from "@/lib/api";
import { getServerToken } from "@/lib/server-auth";
import type { PageEnvelope, WorkflowSummary } from "@/lib/types";
import ErrorBox, { apiErrorMessage } from "@/components/ErrorBox";
import HealthCards from "@/components/HealthCards";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  let total: number | null = null;
  let error: string | null = null;
  try {
    const data = await api.get<PageEnvelope<WorkflowSummary>>("/v1/workflows?limit=1", {
      token: await getServerToken(),
    });
    total = data.total;
  } catch (err) {
    error = apiErrorMessage(err);
  }

  return (
    <div className="page">
      <h1>概览</h1>
      <p className="page-sub">电商运营控制塔 · 内部运营控制台（API v1）</p>

      <div className="cards">
        <div className="card stat-card">
          <div className="stat-label">工作流总数</div>
          <div className="stat-value">{total !== null ? total : "—"}</div>
          <div className="stat-note">
            {total !== null
              ? "来自 GET /v1/workflows?limit=1（读取 total）"
              : error
                ? "后端暂不可达，详见下方错误提示"
                : "加载中"}
          </div>
          <Link className="btn btn-secondary" href="/workflows">
            查看工作流列表
          </Link>
        </div>
        <div className="card stat-card">
          <div className="stat-label">审批收件箱</div>
          <p className="stat-note">处理待审批工作项：批准 / 拒绝 + 原因</p>
          <Link className="btn btn-secondary" href="/approvals">
            前往审批
          </Link>
        </div>
        <div className="card stat-card">
          <div className="stat-label">对账</div>
          <p className="stat-note">发起对账运行，查看并处理差异（人工对账）</p>
          <Link className="btn btn-secondary" href="/reconciliations">
            前往对账
          </Link>
        </div>
        <div className="card stat-card">
          <div className="stat-label">命令发起</div>
          <p className="stat-note">商品修订 / 渠道上架 / 采购 / 退货 / 对账</p>
          <Link className="btn btn-secondary" href="/commands">
            发起命令
          </Link>
        </div>
        <div className="card stat-card">
          <div className="stat-label">销售订单</div>
          <p className="stat-note">渠道订单与 O2C 状态（GET /v1/sales-orders）</p>
          <Link className="btn btn-secondary" href="/sales-orders">
            查看订单
          </Link>
        </div>
        <div className="card stat-card">
          <div className="stat-label">退货</div>
          <p className="stat-note">退货案例与退款进度（GET /v1/return-cases）</p>
          <Link className="btn btn-secondary" href="/return-cases">
            查看退货
          </Link>
        </div>
        <div className="card stat-card">
          <div className="stat-label">采购</div>
          <p className="stat-note">采购订单与收货/账单状态（GET /v1/procurements）</p>
          <Link className="btn btn-secondary" href="/procurements">
            查看采购
          </Link>
        </div>
      </div>

      {error && <ErrorBox error={error} title="后端不可达" />}

      <div className="card">
        <h2>系统状态</h2>
        <HealthCards />
        <p className="muted">
          数据源：GET /readyz 与 GET /v1/ops/runtime（worker/inbox/effect/reconciliation 健康卡片）。
          后端 /readyz、/livez、/v1/ops/* 由 WP6 在 Wave 2 实现；联调前对应卡片显示「待 WP6 联调」。
          认证采用同源 BFF HttpOnly 会话（右上角登录），不再使用 localStorage。
        </p>
      </div>
    </div>
  );
}

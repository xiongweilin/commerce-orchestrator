import { api } from "@/lib/api";
import { getServerToken } from "@/lib/server-auth";
import type { OpsRuntimeResponse, ReadyzResponse } from "@/lib/types";

/**
 * 概览页健康卡片（服务端组件）：worker / inbox / effect / reconciliation。
 * 数据源：GET /readyz（数据库、迁移、adapter 配置、worker heartbeat）与
 * GET /v1/ops/runtime（运行信息）。两者均已实现（WP6 落地）；
 * 后端不可达或字段缺失时卡片显示「未知」。
 */

const CARD_DEFS = [
  { key: "worker", label: "Worker", hint: "heartbeat / 存活" },
  { key: "inbox", label: "Inbox", hint: "pending / processing / failed" },
  { key: "effect", label: "Effect", hint: "attempt / outcome" },
  { key: "reconciliation", label: "Reconciliation", hint: "checked / diffs / failed" },
] as const;

const OK_VALUES = new Set(["ok", "healthy", "up", "ready", "pass"]);
const BAD_VALUES = new Set(["fail", "failed", "error", "down", "unhealthy", "not_ready"]);

function toneOf(status: string | null | undefined): "ok" | "warn" | "down" | "unknown" {
  if (!status) return "unknown";
  const normalized = status.toLowerCase();
  if (OK_VALUES.has(normalized)) return "ok";
  if (BAD_VALUES.has(normalized)) return "down";
  return "warn";
}

function countText(value: unknown): string | null {
  if (typeof value === "number") return String(value);
  if (typeof value === "string" && value !== "") return value;
  return null;
}

export default async function HealthCards() {
  const token = await getServerToken();
  const [readyz, livez, runtime] = await Promise.all([
    api.get<ReadyzResponse>("/readyz", { token }).catch(() => null),
    api.get<{ status?: string }>("/livez", { token }).catch(() => null),
    api.get<OpsRuntimeResponse>("/v1/ops/runtime", { token }).catch(() => null),
  ]);

  return (
    <div className="cards health-cards">
      {CARD_DEFS.map(({ key, label, hint }) => {
        const check = readyz?.checks?.[key];
        const rt = runtime?.[key];
        const rtStatus = typeof rt?.status === "string" ? rt.status : null;
        const status = check?.status ?? rtStatus ?? null;
        const tone = toneOf(status);
        const detail =
          check?.message && typeof check.message === "string"
            ? check.message
            : (["attempts", "pending", "processing", "failed", "checked", "diffs"] as const)
                .map((field) => {
                  const value = rt?.[field];
                  const text = countText(value);
                  return text ? `${field}: ${text}` : null;
                })
                .filter((part): part is string => part !== null)
                .join(" · ");
        return (
          <div className="card stat-card" key={key}>
            <div className="stat-label">{label}</div>
            <div>
              <span
                className={`badge ${
                  tone === "ok"
                    ? "badge-success"
                    : tone === "down"
                      ? "badge-danger"
                      : tone === "warn"
                        ? "badge-warning"
                        : "badge-neutral"
                }`}
              >
                {tone === "ok" ? "正常" : tone === "down" ? "异常" : tone === "warn" ? "告警" : "未知"}
              </span>
            </div>
            <div className="stat-note">{hint}</div>
            {detail && <div className="stat-note mono">{detail}</div>}
          </div>
        );
      })}
      <div className="card stat-card">
        <div className="stat-label">进程存活（/livez）</div>
        <div>
          <span
            className={`badge ${
              livez?.status === "ok" ? "badge-success" : "badge-neutral"
            }`}
          >
            {livez?.status === "ok" ? "正常" : "未知"}
          </span>
        </div>
        <div className="stat-note">/healthz 保留为 /livez 兼容别名（计划 §四.1）</div>
      </div>
    </div>
  );
}

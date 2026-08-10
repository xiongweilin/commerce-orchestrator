"use client";

import Link from "next/link";
import { useMemo, useState, type FormEvent } from "react";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import type { AcceptedCommand } from "@/lib/types";

interface CommandType {
  value: string;
  label: string;
  description: string;
  endpoint: string;
  example: Record<string, unknown>;
}

const COMMAND_TYPES: CommandType[] = [
  {
    value: "catalog-revision",
    label: "商品修订",
    description: "对商品进行价格 / 库存等修订（POST /v1/catalog-revisions）",
    endpoint: "/v1/catalog-revisions",
    example: { entityId: "SKU-1001", changes: { price: 199.0, stock: 50 } },
  },
  {
    value: "listing-publication",
    label: "渠道上架",
    description: "将商品发布 / 上架到渠道（POST /v1/listing-publications）",
    endpoint: "/v1/listing-publications",
    example: { entityId: "SKU-1001", channel: "taobao", action: "publish" },
  },
  {
    value: "procurement",
    label: "采购",
    description: "发起采购订单（POST /v1/procurements）",
    endpoint: "/v1/procurements",
    example: { supplierId: "SUP-001", items: [{ sku: "SKU-1001", quantity: 100 }] },
  },
  {
    value: "return",
    label: "退货",
    description: "发起退货（POST /v1/returns）",
    endpoint: "/v1/returns",
    example: { orderId: "ORDER-2026-0001", reason: "客户退货" },
  },
  {
    value: "reconciliation",
    label: "对账",
    description: "发起对账运行（POST /v1/reconciliations）",
    endpoint: "/v1/reconciliations",
    example: { scope: "reconciliation", channel: "all" },
  },
];

export default function CommandsPage() {
  const [type, setType] = useState<CommandType>(COMMAND_TYPES[0]);
  const [payloadText, setPayloadText] = useState(() =>
    JSON.stringify(COMMAND_TYPES[0].example, null, 2),
  );
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<AcceptedCommand | null>(null);
  const [error, setError] = useState<string | null>(null);

  const jsonValid = useMemo(() => {
    try {
      JSON.parse(payloadText);
      return true;
    } catch {
      return false;
    }
  }, [payloadText]);

  const handleTypeChange = (value: string) => {
    const next = COMMAND_TYPES.find((command) => command.value === value) ?? COMMAND_TYPES[0];
    setType(next);
    setPayloadText(JSON.stringify(next.example, null, 2));
    setResult(null);
    setError(null);
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setResult(null);

    let payload: unknown;
    try {
      payload = JSON.parse(payloadText);
    } catch {
      setError("JSON 格式错误，请检查负载内容。");
      return;
    }

    if (type.value !== "reconciliation") {
      const ok = window.confirm(
        `即将发起命令「${type.label}」。该命令会创建审批工作流并可能触发后续外部操作（上架/采购/退款），确定继续？`,
      );
      if (!ok) return;
    }

    setSubmitting(true);
    try {
      const res = await api.post<AcceptedCommand>(type.endpoint, payload, {
        idempotencyKey: newIdempotencyKey(),
      });
      setResult(res);
    } catch (err) {
      if (err instanceof ApiError && err.code === "idempotency_key_conflict") {
        setError("幂等键冲突（409）：该幂等键已被用于不同的请求体，请重试。");
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
    <div className="page">
      <div className="page-head">
        <div>
          <h1>命令发起</h1>
          <p className="page-sub">向运营控制塔发起命令，提交后返回 202 + 工作流 ID</p>
        </div>
      </div>

      <div className="card">
        <form className="command-form" onSubmit={handleSubmit}>
          <label className="field">
            <span className="field-label">命令类型</span>
            <select
              className="input"
              value={type.value}
              onChange={(event) => handleTypeChange(event.target.value)}
            >
              {COMMAND_TYPES.map((command) => (
                <option key={command.value} value={command.value}>
                  {command.label}
                </option>
              ))}
            </select>
            <span className="field-hint">{type.description}</span>
          </label>

          <label className="field">
            <span className="field-label">JSON 负载</span>
            <textarea
              className="input textarea code"
              rows={12}
              spellCheck={false}
              value={payloadText}
              onChange={(event) => setPayloadText(event.target.value)}
            />
            <span className="field-hint">
              POST {type.endpoint} · 每次提交自动附带新的 Idempotency-Key
            </span>
            <span className={`field-hint ${jsonValid ? "hint-ok" : "hint-bad"}`}>
              {jsonValid ? "JSON 语法有效" : "JSON 语法错误（提交将被拦截）"}
            </span>
          </label>

          <div className="form-footer">
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? "提交中…" : "发起命令"}
            </button>
          </div>
        </form>

        {error && <div className="error-box">{error}</div>}

        {result && (
          <div className="success-box command-result">
            <div>
              <strong>已受理（202）</strong>
            </div>
            <div className="kv-line">
              <span className="detail-label">workflowId</span>
              <span className="mono">{result.workflowId}</span>
            </div>
            <div className="kv-line">
              <span className="detail-label">status</span>
              <span>{result.status}</span>
            </div>
            <div className="kv-line">
              <span className="detail-label">statusUrl</span>
              <span className="mono">{result.statusUrl}</span>
            </div>
            <div className="kv-line">
              <Link
                className="btn btn-secondary"
                href={`/workflows/${encodeURIComponent(result.workflowId)}`}
              >
                查看工作流详情
              </Link>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

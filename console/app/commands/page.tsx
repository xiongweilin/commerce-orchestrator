"use client";

import Link from "next/link";
import { useMemo, useState, type FormEvent } from "react";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import type { AcceptedResponse } from "@/lib/types";

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
    description: "提交商品内容修订（契约 §2.1，POST /v1/catalog-revisions）",
    endpoint: "/v1/catalog-revisions",
    example: {
      sku: "SKU-1001",
      title: "示例商品",
      category: "electronics",
      proposed: { price: "199.00", stock: 50 },
      sourceRefs: [{ id: "fb-001", type: "feedback" }],
      sourceRevision: "r1",
      evidence: { reviewedBy: "ops" },
    },
  },
  {
    value: "listing-publication",
    label: "渠道上架",
    description: "创建上架发布计划（契约 §2.2，POST /v1/listing-publications）",
    endpoint: "/v1/listing-publications",
    example: {
      sku: "SKU-1001",
      channel: "shopify",
      payload: {
        shopify: {
          title: "示例商品",
          descriptionHtml: "<p>描述</p>",
          status: "active",
          tags: ["electronics"],
          publishedAt: "2026-08-11T00:00:00Z",
        },
      },
    },
  },
  {
    value: "procurement",
    label: "采购",
    description: "创建采购（契约 §2.3，POST /v1/procurements）",
    endpoint: "/v1/procurements",
    example: {
      sku: "SKU-1001",
      qty: "100",
      uom: "unit",
      supplier: "供应商 A",
      unitCost: "12.50",
      currency: "CNY",
    },
  },
  {
    value: "return",
    label: "退货",
    description: "创建退货 case（契约 §2.4，POST /v1/returns）",
    endpoint: "/v1/returns",
    example: {
      returnRef: "R-2026-0001",
      shopifyOrderId: "gid://shopify/Order/123",
      orderRef: "ORDER-2026-0001",
      customerRef: "CUST-001",
      reason: "客户退货",
    },
  },
  {
    value: "reconciliation",
    label: "对账",
    description: "触发对账运行（契约 §2.5，POST /v1/reconciliations）",
    endpoint: "/v1/reconciliations",
    example: { run_type: "daily", domains: ["shopify", "odoo", "ledger"], scope: {} },
  },
];

export default function CommandsPage() {
  const [type, setType] = useState<CommandType>(COMMAND_TYPES[0]);
  const [payloadText, setPayloadText] = useState(() =>
    JSON.stringify(COMMAND_TYPES[0].example, null, 2),
  );
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<AcceptedResponse | null>(null);
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
      const res = await api.post<AcceptedResponse>(type.endpoint, payload, {
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

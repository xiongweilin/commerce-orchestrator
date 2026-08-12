/**
 * 后端 API v1 的数据结构。
 *
 * - 命令请求/响应类型来自 OpenAPI 生成的 lib/generated/openapi.ts（唯一契约源，禁止手写重复）。
 * - 以下读模型类型当前未出现在后端 OpenAPI 响应 schema 中（后端以 additionalProperties 返回），
 *   按 docs/contracts/api-contract.md 与整改计划 §四.1 编写最小契约类型；WP6 落地后由
 *   gen-types 接管，届时删除本文件中的重复定义。
 * - 契约之外的额外字段使用 [key: string]: unknown 兼容，避免因后端扩展字段导致类型报错。
 */

export type {
  AcceptedResponse,
  CatalogRevisionCreate,
  DiffResolveRequest,
  DiffResolveResponse,
  HTTPValidationError,
  ListingPublicationCreate,
  ProcurementCreate,
  ReconciliationCreate,
  ReturnCreate,
  ValidationError,
  WebhookReceipt,
  WorkItemDecisionResponse,
  WorkItemDecisionSubmit,
} from "./generated/openapi";

export interface PageEnvelope<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** GET /v1/workflows 列表项（契约 §3.2）。 */
export interface WorkflowSummary {
  workflowId: string;
  type: string;
  status: string;
  currentStep: string | null;
  correlationId?: string | null;
  createdAt: string;
  updatedAt: string;
}

/** GET /v1/sales-orders 列表项（契约 §3.7）。 */
export interface SalesOrderSummary {
  workflowId: string | null;
  orderRef: string;
  shopifyOrderId: string | null;
  customerRef: string | null;
  status: string;
  currency: string;
  total: string;
  createdAt: string;
  updatedAt: string;
}

/** GET /v1/return-cases 列表项（契约 §3.8）。 */
export interface ReturnCaseSummary {
  returnRef: string;
  shopifyOrderId: string | null;
  orderRef: string | null;
  reason: string | null;
  status: string;
  refundAmount: string | null;
  currency: string | null;
  disposition: string | null;
  creditNoteId: string | null;
  shopifyRefundGid: string | null;
  createdAt: string;
}

/** GET /v1/procurements 列表项（契约 §3.9）。 */
export interface ProcurementOrderSummary {
  sku: string;
  qty: string;
  uom: string;
  supplier: string;
  unitCost: string;
  currency: string;
  status: string;
  odooPoId: string | null;
  createdAt: string;
}

/** 工作流事件（契约 §3.1：events[] 使用 `type` 字段，不再使用 eventType）。 */
export interface WorkflowEvent {
  eventId: string;
  type: string;
  occurredAt: string;
  [key: string]: unknown;
}

/** 效果台账条目（契约 §3.1 + 计划 §四.1：补充 remoteReference/attempt/errorDetail）。 */
export interface WorkflowEffect {
  operation: string;
  status: string;
  remoteReference?: string | null;
  attempt?: number;
  errorDetail?: string | null;
  [key: string]: unknown;
}

/**
 * 审批工作项（契约 §3.3 使用 canonical `expectedWorkflowVersion`；
 * §3.1 兼容周期内同时返回 legacy `expectedVersion`，此处保留兼容字段）。
 */
export interface WorkItem {
  workItemId: string;
  workflowId: string;
  kind: string;
  title: string;
  status: string;
  payload?: unknown;
  expectedWorkflowVersion: number;
  /** legacy 兼容字段（契约 §3.1 workItems[].expectedVersion），新数据以 expectedWorkflowVersion 为准。 */
  expectedVersion?: number | null;
  expiresAt?: string | null;
  createdAt: string;
}

/** GET /v1/workflows/{id} 详情（契约 §3.1）。 */
export interface WorkflowDetail {
  workflowId: string;
  type: string;
  status: string;
  currentStep: string | null;
  expectedWorkflowVersion: number;
  input?: unknown;
  result?: unknown;
  error?: unknown;
  events: WorkflowEvent[];
  effects: WorkflowEffect[];
  workItems: WorkItem[];
  createdAt: string;
  updatedAt: string;
}

/** GET /v1/reconciliations 列表项（契约 §3.4）。 */
export interface ReconciliationRun {
  runId: string;
  runType: string;
  status: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  summary?: Record<string, unknown> | null;
}

/** 对账差异（契约 §3.5）。 */
export interface ReconciliationDiff {
  diffId: string;
  domain: string;
  entityType: string;
  entityId: string;
  expected?: unknown;
  actual?: unknown;
  difference?: unknown;
  status: string;
  resolutionNote?: string | null;
  resolvedAt?: string | null;
  createdAt: string;
}

export interface ReconciliationDetail extends ReconciliationRun {
  diffs: ReconciliationDiff[];
}

/**
 * GET /v1/me 的最小客户端可见视图（服务端 getServerUser 解析为同形结构）。
 * 来源：整改计划 §四.1；精确字段待 WP6 联调。
 */
export interface CurrentUser {
  id: string;
  roles: string[];
  username?: string | null;
  email?: string | null;
  jwtExpiresAt?: string | null;
  [key: string]: unknown;
}

/**
 * GET /v1/ops/inbox 列表项（计划 §2.2 受保护运维接口，仅 system_admin）。
 * 字段对齐 backend/app/models/messaging.py 的 InboxEvent（event_id/consumer/status/
 * attempts/next_attempt_at/lease_until/last_error/processed_at/received_at）；
 * ops 接口的精确 JSON 由 WP6 实现，字段名待联调核对。
 */
export interface OpsInboxEvent {
  eventId: string;
  consumer: string;
  status: string;
  attempts?: number;
  lastError?: string | null;
  nextAttemptAt?: string | null;
  leaseUntil?: string | null;
  processedAt?: string | null;
  receivedAt?: string;
  [key: string]: unknown;
}

/** GET /readyz 响应（计划 §四.1：数据库、Alembic head、adapter 配置、worker heartbeat；WP6 实现）。 */
export interface ReadyzResponse {
  status: string;
  checks?: Record<string, { status?: string; message?: string; [key: string]: unknown }>;
  [key: string]: unknown;
}

/** GET /v1/ops/runtime 响应（计划 §2.2；worker/inbox/effect/reconciliation 运行信息，WP6 实现）。 */
export interface OpsRuntimeResponse {
  worker?: Record<string, unknown>;
  inbox?: Record<string, unknown>;
  effect?: Record<string, unknown>;
  reconciliation?: Record<string, unknown>;
  [key: string]: unknown;
}

/**
 * 后端 API v1 的数据结构（对齐 FastAPI 契约）。
 * 契约之外的额外字段使用 Record 兼容，避免因后端扩展字段导致类型报错。
 */

export interface PageEnvelope<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface WorkflowSummary {
  workflowId: string;
  type: string;
  status: string;
  currentStep: string | null;
  correlationId?: string | null;
  createdAt: string;
  updatedAt: string;
}

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

export interface WorkflowEvent {
  eventType: string;
  occurredAt: string;
  [key: string]: unknown;
}

export interface WorkflowEffect {
  operation: string;
  status: string;
  remoteReference?: string | null;
  [key: string]: unknown;
}

export interface WorkItem {
  workItemId: string;
  workflowId: string;
  kind: string;
  title: string;
  status: string;
  payload?: unknown;
  expectedWorkflowVersion: number;
  expiresAt?: string | null;
  createdAt: string;
}

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

export interface WorkItemDecision {
  workItemId: string;
  status: string;
  workflowId: string;
}

export interface ReconciliationRun {
  runId: string;
  runType: string;
  status: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  summary?: Record<string, unknown> | null;
}

export interface ReconciliationDiff {
  diffId: string;
  domain: string;
  entityType: string;
  entityId: string;
  expected?: unknown;
  actual?: unknown;
  difference?: string | null;
  status: string;
  resolutionNote?: string | null;
  createdAt: string;
}

export interface ReconciliationDetail extends ReconciliationRun {
  diffs: ReconciliationDiff[];
}

/** POST /v1/catalog-revisions 等命令端点返回的 202 响应体 */
export interface AcceptedCommand {
  workflowId: string;
  status: string;
  statusUrl: string;
}

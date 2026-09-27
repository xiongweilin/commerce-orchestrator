// 本文件由 scripts/gen-types.mjs 自动生成，禁止手改。
// 运行：node scripts/gen-types.mjs（或 npm run gen:types）
// 来源：http://127.0.0.1:8000/openapi.json

/** 异步受理命令的响应。 */
export interface AcceptedResponse {
  "status"?: "accepted";
  "statusUrl": string;
  "workflowId": string;
  [key: string]: unknown;
}

/** 为 SKU 创建草稿 catalog revision。 */
export interface CatalogRevisionCreate {
  "category"?: string | null;
  "description"?: string | null;
  "evidence"?:   {
      [key: string]: unknown;
    };
  "proposed"?:   {
      [key: string]: unknown;
    };
  "sku": string;
  "source_refs"?:   {
      [key: string]: unknown;
    }[];
  "source_revision"?: string | null;
  "title"?: string | null;
  [key: string]: unknown;
}

/** reconciliation diff 的人工处理说明。 */
export interface DiffResolveRequest {
  "note": string;
  [key: string]: unknown;
}

/** reconciliation diff 的处理结果。 */
export interface DiffResolveResponse {
  "diffId": string;
  "resolvedAt"?: string | null;
  "status": string;
  [key: string]: unknown;
}

export interface HTTPValidationError {
  "detail"?: ValidationError[];
  [key: string]: unknown;
}

/** 请求在销售渠道发布 SKU。 */
export interface ListingPublicationCreate {
  "channel"?: string;
  "payload"?:   {
      [key: string]: unknown;
    };
  "sku": string;
  [key: string]: unknown;
}

/** 创建采购订单（demand_detected）。 */
export interface ProcurementCreate {
  "currency"?: string;
  "qty": number | string;
  "sku": string;
  "supplier": string;
  "unit_cost": number | string;
  "uom"?: string;
  [key: string]: unknown;
}

/** 触发 reconciliation run。 */
export interface ReconciliationCreate {
  "domains"?: string[];
  "run_type": string;
  "scope"?:   {
      [key: string]: unknown;
    };
  [key: string]: unknown;
}

/** 登记客户退货 case。 */
export interface ReturnCreate {
  "customer_ref": string;
  "order_ref"?: string | null;
  "reason": string;
  "return_ref"?: string | null;
  "shopify_order_id"?: string | null;
  [key: string]: unknown;
}

export interface ValidationError {
  "ctx"?:   {
      [key: string]: unknown;
    };
  "input"?: unknown;
  "loc": string | number[];
  "msg": string;
  "type": string;
  [key: string]: unknown;
}

/** 返回给 webhook sender 的快速确认响应。 */
export interface WebhookReceipt {
  "deduplicated"?: boolean;
  "received": boolean;
  [key: string]: unknown;
}

/** 已提交 work item decision 的结果。 */
export interface WorkItemDecisionResponse {
  "status": string;
  "workItemId": string;
  "workflowId": string;
  [key: string]: unknown;
}

/** 对 pending work item 提交 decision。 */
export interface WorkItemDecisionSubmit {
  "decision": "approve" | "reject" | "confirm" | "cancel";
  "expectedWorkflowVersion"?: number | null;
  "reason"?: string | null;
  [key: string]: unknown;
}

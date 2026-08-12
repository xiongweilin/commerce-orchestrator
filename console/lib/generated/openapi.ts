// 本文件由 scripts/gen-types.mjs 自动生成，禁止手改。
// 运行：node scripts/gen-types.mjs（或 npm run gen:types）
// 来源：http://127.0.0.1:8000/openapi.json
// OpenAPI spec sha256（前 16 位）：0c7243bbd348ed22

/** Response for commands that are accepted asynchronously. */
export interface AcceptedResponse {
  "status"?: "accepted";
  "statusUrl": string;
  "workflowId": string;
  [key: string]: unknown;
}

/** Create a draft catalog revision for a SKU. */
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

/** Manual resolution note for a reconciliation diff. */
export interface DiffResolveRequest {
  "note": string;
  [key: string]: unknown;
}

/** Result of resolving a reconciliation diff. */
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

/** Request publication of a SKU on a sales channel. */
export interface ListingPublicationCreate {
  "channel"?: string;
  "payload"?:   {
      [key: string]: unknown;
    };
  "sku": string;
  [key: string]: unknown;
}

/** Create a procurement order (demand_detected). */
export interface ProcurementCreate {
  "currency"?: string;
  "qty": number | string;
  "sku": string;
  "supplier": string;
  "unit_cost": number | string;
  "uom"?: string;
  [key: string]: unknown;
}

/** Trigger a reconciliation run. */
export interface ReconciliationCreate {
  "domains"?: string[];
  "run_type": string;
  "scope"?:   {
      [key: string]: unknown;
    };
  [key: string]: unknown;
}

/** Register a customer return case. */
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

/** Fast acknowledgement returned to the webhook sender. */
export interface WebhookReceipt {
  "deduplicated"?: boolean;
  "received": boolean;
  [key: string]: unknown;
}

/** Result of a submitted work item decision. */
export interface WorkItemDecisionResponse {
  "status": string;
  "workItemId": string;
  "workflowId": string;
  [key: string]: unknown;
}

/** Submit a decision on a pending work item. */
export interface WorkItemDecisionSubmit {
  "decision": "approve" | "reject" | "confirm" | "cancel";
  "expectedWorkflowVersion"?: number | null;
  "reason"?: string | null;
  [key: string]: unknown;
}

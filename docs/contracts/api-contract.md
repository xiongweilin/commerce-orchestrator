# API 契约（唯一事实源）

> 本文档是命令 API 的唯一契约事实源。后端 `backend/app` 与控制台 `console` 必须按此实现；不一致时以本文档为准并修复实现。关联决策：ADR-0004（幂等）、ADR-0006（版本）、ADR-0007（Shopify）。

## 1. 通用约定

- Base URL：`/v1`；请求/响应均为 JSON（`application/json`）。
- 时间字段：ISO-8601 UTC；金额字段：十进制定点值，JSON 中序列化为字符串。
- 认证：`Authorization: Bearer <JWT>`；JWT subject 为用户 id（UUIDv7），claims 含角色列表。
- 角色（固定 11 类）：`catalog_owner`、`commerce_lead`、`finance_approver`、`procurement_lead`、`budget_owner`、`warehouse_staff`、`inventory_supervisor`、`accountant`、`customer_service`、`compliance`、`system_admin`。
- **所有写命令必须携带 `Idempotency-Key` header**（见第 6 节）。
- 审批边界与四眼原则在服务端强制（见 data-ownership.md）。

## 2. 命令 API 端点（写命令 → 202 Accepted）

### 2.1 POST /v1/catalog-revisions

提交商品内容修订（Catalog-PIM 域），进入 `draft → normalized → validated → pending_approval` 审批链。

```json
{
  "sku": "str",
  "title": "str?",
  "description": "str?",
  "category": "str?",
  "proposed": { "key": "value" },
  "sourceRefs": [{ "id": "str", "type": "str" }],
  "sourceRevision": "str?",
  "evidence": { "key": "value" }
}
```

审批边界：商品内容 → `catalog_owner`；`compliance` 可否决上架/SOP/敏感品类。

### 2.2 POST /v1/listing-publications

创建上架发布计划（首个渠道 `shopify`）。

```json
{
  "sku": "str",
  "channel": "shopify",
  "payload": { "shopify": { "title": "str", "descriptionHtml": "str?", "status": "active|draft", "tags": ["str"], "publishedAt": "ISO-8601?" } }
}
```

审批边界：上架 → `catalog_owner`；`compliance` 可否决。

### 2.3 POST /v1/procurements

创建采购（RFQ/PO）流程（`demand_detected`）。

```json
{
  "sku": "str",
  "qty": "decimal-str",
  "uom": "unit",
  "supplier": "str",
  "unitCost": "decimal-str",
  "currency": "CNY"
}
```

审批边界：`procurement_lead` 提出、`budget_owner` 批准。提出人与批准人不得相同（四眼原则）。

### 2.4 POST /v1/returns

创建退货 case（`requested`）。

```json
{
  "returnRef": "str?",
  "shopifyOrderId": "str?",
  "orderRef": "str?",
  "customerRef": "str",
  "reason": "str"
}
```

审批边界：`customer_service` 提出、`warehouse_staff` 确认实物、`finance_approver` 批准金额、渠道 adapter 执行退款。提出人与批准人不得相同。

### 2.5 POST /v1/reconciliations

触发一次对账运行。

```json
{
  "run_type": "daily",
  "domains": ["shopify", "odoo", "ledger"],
  "scope": {}
}
```

差异一律进入 `MANUAL_RECONCILIATION`，禁止自动抹平；处置见 3.6。

### 2.6 POST /v1/work-items/{id}/decisions（→ 200）

审批决策（work item 由工作流创建）。

```json
{
  "decision": "approve | reject | confirm | cancel",
  "reason": "str?",
  "expectedWorkflowVersion": 1
}
```

- 服务端校验：角色匹配审批边界；四眼原则（提出者 ≠ 批准者）；`compliance` 否决权；过期/已决项拒绝。
- **必须携带 `expectedWorkflowVersion`**；不匹配 → `409 workflow_version_conflict`。
- 成功 → `200 {"workItemId": "uuid", "status": "str", "workflowId": "uuid"}`。

## 3. 只读端点（→ 200）

### 3.1 GET /v1/workflows/{id}

工作流详情（轮询 statusUrl）。

```json
{
  "workflowId": "uuid",
  "type": "str",
  "status": "accepted|running|awaiting_approval|completed|failed|cancelled",
  "currentStep": "str",
  "expectedWorkflowVersion": 1,
  "input": "object?",
  "result": "object?",
  "error": "str?",
  "events": [ { "eventId": "uuid", "type": "str", "occurredAt": "ISO-8601" } ],
  "effects": [ { "effectId": "uuid", "operation": "str", "status": "str" } ],
  "workItems": [ { "workItemId": "uuid", "kind": "str", "title": "str", "status": "str", "requiredRoles": ["str"], "expectedVersion": 1, "expiresAt": "ISO-8601?", "payload": "object" } ],
  "createdAt": "ISO-8601",
  "updatedAt": "ISO-8601"
}
```

### 3.2 GET /v1/workflows?status=&type=&limit=&offset=

工作流列表（控制台概览）。

```json
{ "items": [ { "workflowId": "uuid", "type": "str", "status": "str", "currentStep": "str", "correlationId": "str?", "createdAt": "ISO-8601", "updatedAt": "ISO-8601" } ], "total": 0, "limit": 50, "offset": 0 }
```

### 3.3 GET /v1/work-items?status=&limit=&offset=

审批收件箱列表。

```json
{ "items": [ { "workItemId": "uuid", "workflowId": "uuid", "kind": "str", "title": "str", "status": "str", "payload": "object", "expectedWorkflowVersion": 1, "expiresAt": "ISO-8601?", "createdAt": "ISO-8601" } ], "total": 0, "limit": 50, "offset": 0 }
```

### 3.4 GET /v1/reconciliations?limit=&offset=

对账运行列表。

```json
{ "items": [ { "runId": "uuid", "runType": "str", "status": "str", "startedAt": "ISO-8601?", "finishedAt": "ISO-8601?", "summary": "object" } ], "total": 0, "limit": 50, "offset": 0 }
```

### 3.5 GET /v1/reconciliations/{runId}

对账详情（含差异）。

```json
{ "runId": "uuid", "runType": "str", "status": "str", "startedAt": "ISO-8601?", "finishedAt": "ISO-8601?", "summary": "object", "diffs": [ { "diffId": "uuid", "domain": "str", "entityType": "str", "entityId": "str", "expected": "object?", "actual": "object?", "difference": "object?", "status": "OPEN|MANUAL_RECONCILIATION|RESOLVED", "resolutionNote": "str?", "resolvedAt": "ISO-8601?", "createdAt": "ISO-8601" } ] }
```

### 3.6 POST /v1/reconciliations/{runId}/diffs/{diffId}/resolve（→ 200）

人工处置差异：仅允许从 `MANUAL_RECONCILIATION` 处置为 `RESOLVED`；必须携带备注。

```json
{ "note": "str" }
```

### 3.7 GET /v1/sales-orders?status=&limit=&offset=（→ 200）

销售订单列表（运营控制台订单页）。

```json
{ "items": [ { "workflowId": "uuid?", "orderRef": "str", "shopifyOrderId": "str?", "customerRef": "str?", "status": "str", "currency": "str", "total": "decimal-str", "createdAt": "ISO-8601", "updatedAt": "ISO-8601" } ], "total": 0, "limit": 50, "offset": 0 }
```

### 3.8 GET /v1/return-cases?status=&limit=&offset=（→ 200）

退货案例列表。

```json
{ "items": [ { "returnRef": "str", "shopifyOrderId": "str?", "orderRef": "str?", "reason": "str", "status": "str", "refundAmount": "decimal-str?", "currency": "str?", "disposition": "str?", "creditNoteId": "str?", "shopifyRefundGid": "str?", "createdAt": "ISO-8601" } ], "total": 0, "limit": 50, "offset": 0 }
```

### 3.9 GET /v1/procurements?status=&limit=&offset=（→ 200）

采购订单列表。

```json
{ "items": [ { "sku": "str", "qty": "decimal-str", "uom": "str", "supplier": "str", "unitCost": "decimal-str", "currency": "str", "status": "str", "odooPoId": "str?", "createdAt": "ISO-8601" } ], "total": 0, "limit": 50, "offset": 0 }
```

## 4. POST /v1/webhooks/shopify（→ 200）

Shopify webhook 入口（异步处理）。

- 校验：对原始请求体计算 Base64 HMAC-SHA256，与 `X-Shopify-Hmac-Sha256` 常量时间比较；缺失或失败 → `401 unauthenticated`。
- 去重：按 `X-Shopify-Webhook-Id` 去重（inbox 唯一键 `(consumer=shopify-webhook, eventId)`）。
- 处理：原始体加密留存（默认 30 天）、幂等入库、触发领域事件（如 `orders/create` → `order.received`、`refunds/create` → `return.case_requested`）；成功 → `200 {"received": true, "deduplicated": bool}`。
- 容忍乱序与漏投；以 `updated_at` 增量对账兜底（runbooks/reconciliation-drift.md）。

## 5. 长命令响应

所有写命令（2.1–2.5）返回 `202 Accepted`：

```json
{
  "workflowId": "uuid",
  "status": "accepted",
  "statusUrl": "/v1/workflows/{workflowId}"
}
```

- `status` 当前固定为 `"accepted"`（长流程已受理）；内部运行状态经 `GET /v1/workflows/{id}` 观察。
- 客户端通过 `statusUrl` 轮询获取进展。

## 6. Idempotency-Key 语义

- **所有写命令（2.1–2.5、3.6）必带** `Idempotency-Key` header（任意不透明字符串，建议 UUID）；缺失 → `422 validation_error`。
- 服务端保存 `(scope, key, requestHash, result)`：
  - `scope` = 端点级命令域（如 `catalog-revision`、`procurement`、`return`、`reconciliation`）；
  - `requestHash` = 规范化请求体的 SHA-256；
  - `result` = 首次执行的完整响应。
- 同 `scope+key` 且 `requestHash` 相同 → 重放已存 result（幂等重放）。
- 同 `scope+key` 且 `requestHash` 不同 → `409 idempotency_key_conflict`。
- 5xx 重试必须复用同一 key；不同 `scope` 互不影响。
- 幂等记录、业务写入与工作流启动在同一事务内提交。

## 7. 错误模型

统一错误信封（所有非 2xx 响应）：

```json
{
  "error": {
    "code": "str",
    "message": "str",
    "correlationId": "uuid",
    "details": "object|null"
  }
}
```

| HTTP | code | 说明 |
|---|---|---|
| 401 | `unauthenticated` | 未认证或 token 无效；webhook HMAC 校验失败 |
| 403 | `permission_denied` | 角色不符审批边界 / 违反四眼原则 / compliance 否决 |
| 404 | `not_found` | 资源不存在（工作流、work item、对账运行、差异） |
| 409 | `idempotency_key_conflict` | 同 key 不同 body |
| 409 | `workflow_version_conflict` | `expectedWorkflowVersion` 不匹配 |
| 409 | `state_conflict` | 状态不允许该操作（如对已决/过期 work item 再决策） |
| 422 | `validation_error` | 请求体/字段校验失败（details 含 `{"fields":[...]}`） |
| 500 | `internal_error` | 内部错误 |
| 502 | `external_system_error` | 上游系统（Shopify/Odoo）错误，可重试（复用同 key） |

## 8. 示例

**提交目录修订**

```
POST /v1/catalog-revisions
Authorization: Bearer <jwt>
Idempotency-Key: 2f8f4e6a-...
Content-Type: application/json

{ "sku": "SKU-1001", "title": "示例商品", "sourceRefs": [{ "id": "fb-001", "type": "feedback" }] }
```

```json
202
{ "workflowId": "77e0...", "status": "accepted", "statusUrl": "/v1/workflows/77e0..." }
```

**幂等冲突**

```json
409
{
  "error": {
    "code": "idempotency_key_conflict",
    "message": "Idempotency-Key 已被不同请求体使用",
    "correlationId": "uuid",
    "details": null
  }
}
```

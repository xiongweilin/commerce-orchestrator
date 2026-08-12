# API 契约（唯一事实源）

> 本文档是命令 API 的唯一契约事实源。后端 `backend/app` 与控制台 `console` 必须按此实现；不一致时以本文档为准并修复实现。关联决策：ADR-0004（幂等）、ADR-0006（版本）、ADR-0007（Shopify）、ADR-0011（DBOS v2 编排/审批）、ADR-0012（effect）、ADR-0013（canonical 对账）、ADR-0014（BFF/隐私）。

## 1. 通用约定

- Base URL：`/v1`；请求/响应均为 JSON（`application/json`）。
- 时间字段：ISO-8601 UTC；金额字段：十进制定点值，JSON 中序列化为字符串。
- 认证：`Authorization: Bearer <JWT>`；JWT subject 为用户 id（UUIDv7），claims 含角色列表。
  - JWT role claims **仅作展示**，权限以数据库 `RoleAssignment` 为准（`get_current_user` 查询 `User` 并校验 `is_active`）。
  - `system_admin` 不自动获得业务审批权；`compliance` 只能在其范围 reject/veto，不能 approve。
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
- **必须携带 `expectedWorkflowVersion`**；v2 引擎将其与**当前** `WorkflowRun.version` 比较（而非仅创建时副本），不匹配 → `409 workflow_version_conflict`。
- 两个并发审批只有一个能成功，另一个 → `409 state_conflict` / `409 workflow_version_conflict`。
- 决策落库后由 worker 经 `DBOS.send`（`idempotency_key=decision_id`）durable 送达 workflow；workflow 收到后才执行后续状态迁移。
- `Idempotency-Key` **必带**（计划 §四.1：work item decision 统一必带；缺失 → `422 validation_error`）。
- 成功 → `200 {"workItemId": "uuid", "status": "str", "workflowId": "uuid"}`。

## 3. 只读端点（→ 200）

### 3.1 GET /v1/workflows/{id}

工作流详情（轮询 statusUrl）。

```json
{
  "workflowId": "uuid",
  "type": "str",
  "status": "accepted|running|awaiting_approval|completed|needs_reconciliation|failed|cancelled",
  "currentStep": "str",
  "expectedWorkflowVersion": 1,
  "input": "object?",
  "result": "object?",
  "error": "str?",
  "events": [ { "eventId": "uuid", "type": "str", "occurredAt": "ISO-8601" } ],
  "effects": [ { "effectId": "uuid", "operation": "str", "status": "str", "remoteReference": "str?", "attempt": 0, "errorDetail": "str?" } ],
  "workItems": [ { "workItemId": "uuid", "workflowId": "uuid", "kind": "str", "title": "str", "status": "str", "requiredRoles": ["str"], "expectedWorkflowVersion": 1, "expectedVersion": 1, "expiresAt": "ISO-8601?", "createdAt": "ISO-8601", "payload": "object" } ],
  "createdAt": "ISO-8601",
  "updatedAt": "ISO-8601"
}
```

规范化字段（P7 §四.1，OpenAPI schema 为前后端唯一契约源）：

- 事件保留契约字段 `type`（前端读取 `type`，不读旧字段）。
- work item 以 `expectedWorkflowVersion` 为 canonical 字段；兼容期内同时返回 legacy `expectedVersion`；补充 `workflowId`、`createdAt`。
- effect 补充 `remoteReference`（远端引用）、`attempt`（尝试次数）、`errorDetail`（截断后非敏感错误详情）。
- `needs_reconciliation` 不是失败终态：存在 `outcome_unknown`/跨系统差异/需人工补偿时进入，人工处置并重新对账成功后方可完成。

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

### 3.10 GET /v1/me（→ 200）

返回当前已认证 **active** 用户：数据库权威角色（`RoleAssignment`，非 JWT claims）与 JWT 到期时间（供 BFF 会话 Max-Age 计算）。

```json
{
  "id": "uuid",
  "username": "str",
  "displayName": "str",
  "email": "str",
  "roles": ["str"],
  "isActive": true,
  "jwtExpiresAt": "ISO-8601?"
}
```

未认证/inactive/未知用户 → `401 unauthenticated`。

### 3.11 GET /livez、GET /healthz、GET /readyz（→ 200 / 503）

- `GET /livez`：仅进程存活 → `200 {"status": "ok"}`，无依赖检查。
- `GET /healthz`：`/livez` 的兼容别名。
- `GET /readyz`：数据库、Alembic head、adapter 配置（Shopify/Odoo）、worker heartbeat（30 秒窗口）全部正常 → `200 {"status": "ok", "checks": {...}}`；任一失败 → `503 {"status": "not_ready", "checks": {...}}`，分项 `{"status": "ok|fail", "message": "str"}`。适配器缺失或 worker 心跳丢失时 readiness 不通过（fail-closed）。

### 3.12 GET /v1/ops/inbox?status=failed&limit=&offset=（→ 200，仅 system_admin）

inbox 分页列表（运维处置 failed 事件）。默认 `status=failed`；非法 status → `422`。

```json
{
  "items": [ { "eventId": "uuid", "consumer": "str", "status": "str", "attempts": 0, "nextAttemptAt": "ISO-8601?", "leaseUntil": "ISO-8601?", "lastError": "str?", "processedAt": "ISO-8601?", "receivedAt": "ISO-8601" } ],
  "total": 0,
  "limit": 50,
  "offset": 0
}
```

### 3.13 GET /v1/ops/runtime（→ 200，仅 system_admin）

worker / inbox / effect / reconciliation 四维运行时快照（控制台健康卡片数据源）：

```json
{
  "worker": { "status": "ok|warn|down", "processName": "str", "instanceId": "str", "statusDetail": "str", "startedAt": "ISO-8601", "heartbeatAt": "ISO-8601", "ageSeconds": 0 },
  "inbox": { "status": "ok|warn", "pending": 0, "processing": 0, "processed": 0, "failed": 0, "oldestAgeSeconds": 0 },
  "effect": { "status": "ok|warn", "planned": 0, "dispatched": 0, "succeeded": 0, "failed": 0, "outcome_unknown": 0, "reconciled": 0, "manual_reconciliation": 0 },
  "reconciliation": { "status": "ok|warn|none", "runId": "uuid?", "runStatus": "str?", "checked": 0, "diffs": 0, "failedDomains": [], "skippedDomains": [], "finishedAt": "ISO-8601?" }
}
```

`reconciliation` 摘要键固定为 `checked`/`diffs`/`failedDomains`/`skippedDomains`（兼容旧 `by_domain` 读取）。

## 4. POST /v1/webhooks/shopify（→ 200）

Shopify webhook 入口（异步处理）。

- 校验：对原始请求体计算 Base64 HMAC-SHA256，与 `X-Shopify-Hmac-Sha256` 常量时间比较；缺失或失败 → `401 unauthenticated`。
- 去重：按 `X-Shopify-Webhook-Id` 去重（inbox 唯一键 `(consumer=shopify-webhook, eventId)`）。
- 处理：原始体加密留存（默认 30 天，`sensitive_payload` vault）、幂等入库、创建领域实体（`SalesOrder` / `ReturnCase`）、创建 DBOS v2 `WorkflowRun`（`workflow_version=2`）并发出 `workflow.accepted` 供 relay 启动 v2 定义；领域事件（如 `orders/create` → `order.received`、`refunds/create` → `return.case_requested`）只携带稳定引用，不再路由到 legacy v1 slice；成功 → `200 {"received": true, "deduplicated": bool}`。
- 容忍乱序与漏投；以 `updated_at` 增量对账兜底（runbooks/reconciliation-drift.md）。

## 5. 健康检查与运维接口（写）

### 5.1 POST /v1/ops/inbox/{id}/retry（→ 200，仅 system_admin）

把一条 failed inbox 事件重置为 `pending`（attempts 清零）重新投递。

- **必须携带 `Idempotency-Key`**（缺失 → `422 validation_error`）；同 key 重放原结果，异 body → `409 idempotency_key_conflict`。
- 成功 → `200 {"eventId": "uuid", "status": "pending", "retriedAt": "ISO-8601", "actorUserId": "uuid"}`。
- 事件不存在 → `404 not_found`。

## 6. 长命令响应

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

## 7. Idempotency-Key 语义

- **必带** `Idempotency-Key` header（任意不透明字符串，建议 UUID）：五类命令端点（2.1–2.5）与 ops inbox retry（5.1）；缺失 → `422 validation_error`。
- **必带**：work item decision（2.6）与 reconciliation diff resolve（3.6）同样统一必带（计划 §四.1）；缺失 → `422 validation_error`。
- 服务端保存 `(scope, key, requestHash, result)`：
  - `scope` = 端点级命令域（如 `catalog-revision`、`procurement`、`return`、`reconciliation`）；
  - `requestHash` = 规范化请求体的 SHA-256；
  - `result` = 首次执行的完整响应。
- 同 `scope+key` 且 `requestHash` 相同、状态 `completed` → 重放已存 result（幂等重放）。
- 同 `scope+key` 且 `requestHash` 相同、仍在处理（`processing`）→ `409 idempotency_in_progress`，附 `Retry-After: 1`。
- 同 `scope+key` 且 `requestHash` 不同 → `409 idempotency_key_conflict`。
- 5xx 重试必须复用同一 key；不同 `scope` 互不影响。
- 幂等记录、业务写入与工作流启动在同一事务内提交。

## 8. 错误模型

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
| 409 | `idempotency_in_progress` | 同 key 同 body 仍在处理，附 `Retry-After: 1` |
| 409 | `workflow_version_conflict` | `expectedWorkflowVersion` 不匹配 |
| 409 | `state_conflict` | 状态不允许该操作（如对已决/过期 work item 再决策） |
| 422 | `validation_error` | 请求体/字段校验失败（details 含 `{"fields":[...]}`） |
| 500 | `internal_error` | 内部错误 |
| 502 | `external_system_error` | 上游系统（Shopify/Odoo）错误，可重试（复用同 key） |

## 9. 示例

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

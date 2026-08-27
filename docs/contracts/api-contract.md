# API 契约（当前实现）

> 本文档定义当前 HTTP API 的稳定行为语义；**精确 request/response JSON schema 以 FastAPI `/openapi.json` 为机器可读事实源**，控制台通过 `npm run gen:types` 生成 TypeScript 类型。本文档必须与 `backend/app/api/v1/*` 同步。

关联：ADR-0004（幂等）、ADR-0011（DBOS v2）、ADR-0012（typed effect）、ADR-0013（canonical reconciliation）、ADR-0014（BFF/隐私）、`responsibility-alignment.md`。

## 1. 通用约定

- API prefix：`/v1`；health/metrics 端点位于根路径。
- 业务 API 认证：`Authorization: Bearer <JWT>`。
- JWT role claims 不作为最终授权事实；服务端读取数据库 `RoleAssignment`。
- `system_admin` 不自动拥有业务审批权。
- 时间：ISO-8601 UTC；金额使用 decimal/string contract。
- 错误统一使用：

```json
{
  "error": {
    "code": "str",
    "message": "str",
    "correlationId": "uuid",
    "details": null
  }
}
```

主要错误码：`unauthenticated`、`permission_denied`、`not_found`、`validation_error`、`state_conflict`、`workflow_version_conflict`、`idempotency_key_conflict`、`idempotency_in_progress`、`external_system_error`、`internal_error`。

## 2. 异步 command API

以下端点要求 `Idempotency-Key`，成功返回 `202 Accepted`：

| Endpoint | command type | 发起角色 |
| --- | --- | --- |
| `POST /v1/catalog-revisions` | `catalog-revision` | `catalog_owner` |
| `POST /v1/listing-publications` | `listing-publication` | `catalog_owner` |
| `POST /v1/procurements` | `procurement` | `procurement_lead` |
| `POST /v1/returns` | `return` | `customer_service` |
| `POST /v1/reconciliations` | `reconciliation` | `accountant` / `system_admin` |

标准受理响应：

```json
{
  "workflowId": "uuid",
  "status": "accepted",
  "statusUrl": "/v1/workflows/{workflowId}"
}
```

command 受理只创建 durable orchestration facts，不在 API request 中直接执行 Shopify/Odoo 副作用。

## 3. Work item decisions 与 execution authority

### 3.1 普通 decision

```text
POST /v1/work-items/{work_item_id}/decisions
```

要求：

- `Idempotency-Key` 必带；
- body 使用 OpenAPI `WorkItemDecisionSubmit`；
- `expectedWorkflowVersion` 用于乐观并发；
- 角色、four-eyes、过期状态等由服务端校验。

关键约束：如果当前 work item 的 server-owned authorization profile 要求执行授权，则普通 endpoint **拒绝 `approve`**，防止仅通过 `WorkItemDecision` 绕过 execution authority。

### 3.2 Authorized decision

```text
POST /v1/work-items/{work_item_id}/authorized-decisions
```

只接受 `approve`。它在同一事务中：

1. 记录 durable `WorkItemDecision`；
2. 根据当前 work item 解析 server-owned profile；
3. mint 一个独立 `ExecutionAuthorization`；
4. 再让 worker 能观察对应 decision event。

成功响应在普通 decision response 基础上增加：

```json
{
  "authorizationId": "uuid",
  "authorizationProfile": "listing-publication-v1 | return-credit-note-v1 | return-refund-v1"
}
```

当前 profiles：

| Profile | Target | Allowed operations |
| --- | --- | --- |
| `listing-publication-v1` | Shopify | `shopify.product_publish` |
| `return-credit-note-v1` | Odoo | `odoo.credit_note_create`, `odoo.credit_note_validate` |
| `return-refund-v1` | Shopify | `shopify.refund_create` |

return financial scope 由服务器从当前 `ReturnCase` 推导，客户端不能扩大 scope。listing publication 保留 compatibility `scope` input，但 target/allowed operations 仍由服务器决定，dispatch 时重新验证 current subject/version/fingerprint/policy/environment。

### 3.3 Work item inbox

```text
GET /v1/work-items?status=&limit=&offset=
```

每个 item 的 projection 包含 `authorizationProfile`。控制台必须消费该 server projection；不得通过 `allowedOperations`、scope 或其它 client-side 推导自行决定 authority。

## 4. Workflow read / responsibility / completion

```text
GET  /v1/workflows
GET  /v1/workflows/{workflow_id}
GET  /v1/workflows/{workflow_id}/responsibility
POST /v1/workflows/{workflow_id}/completion-recheck
```

### Responsibility Inspector

`GET /v1/workflows/{workflow_id}/responsibility` 返回 schema `commerce-responsibility-inspector-v1`，并且顶层 `authorityBearing=false`。

主要 sections：

- `workflow`
- `currentResponsibility`
- `historical`
- `decisions`
- `authorizations`
- `execution`
- `reality`
- `confirmedOutcomes`
- `responsibilityObligations`
- `openResponsibility`
- `shortcuts`

`currentResponsibility` 当前完整实现于 listing-publication。它重新计算当前 eligibility；`historical` 只保存当时实际 reliance，二者不得互相升级。

Inspector 是只读 explanation，不 mint authority。

### Completion recheck

`POST /v1/workflows/{workflow_id}/completion-recheck` 当前仅允许 `catalog_owner`、`accountant`、`system_admin` 请求。

它只对处于 `running` 且 `result.completionBlocked=true` 的 workflow 有效。成功后发出 durable `workflow.completion_recheck_requested`，topic 为 `completion-recheck`。

该请求：

- 不创建 verification evidence；
- 不直接修改为 completed；
- 只让等待中的 workflow 重新执行 bounded completion assessment。

## 5. Publication qualification

```text
POST /v1/publication-qualifications
```

角色：`catalog_owner`。

这是 append-only current qualification assessment，绑定 exact：

- catalog revision；
- channel；
- purpose；
- policy version；
- adapter version；
- environment reference；
- evidence/source revision references。

`qualified` 不等价于 `ExecutionAuthorization`。

## 6. Semantic / responsibility record API

这些接口用于明确记录责任层事实，不通过推断把旧事实升级成新事实。

| Endpoint | 角色/语义 |
| --- | --- |
| `POST /v1/effect-realizations` | `system_admin`; append reality assessment |
| `POST /v1/reconciliation-resolutions` | `accountant` / `system_admin`; append recovery disposition/verification metadata，可同时 open obligation |
| `POST /v1/responsibility/confirmed-outcomes` | `compliance` / `system_admin`; 仅允许从 VERIFIED realization + evidence 确认 |
| `POST /v1/responsibility/obligations` | `compliance` / `system_admin`; open explicit scoped obligation |
| `POST /v1/responsibility/obligations/{id}/discharge` | `compliance` / `system_admin`; discharge，不删除历史 |
| `POST /v1/responsibility/knowledge-projections` | `compliance` / `system_admin`; 保存 portable projection record |
| `POST /v1/responsibility/experience/evaluate` | 任一有效业务角色；返回 admission + open responsibilities，`authorityBearing=false` |
| `POST /v1/responsibility/experience/bind` | `compliance` / `system_admin`; commit HistoricalExperienceUse + Commerce binding |

### ConfirmedOutcome

`ConfirmedOutcome` 必须引用同一 effect 的 `EffectRealizationAssessment`，且 assessment 必须是 `VERIFIED` 并包含 evidence refs。`EffectLedger.succeeded` 本身不足以建立 ConfirmedOutcome。

### Historical Experience bind

同一个 historical use ref 若已绑定到不同 Commerce subject/workflow/version，服务端拒绝 rebound。历史 reliance 不做 silent backfill。

## 7. Reconciliation API

```text
GET  /v1/reconciliations?limit=&offset=
GET  /v1/reconciliations/{run_id}
POST /v1/reconciliations/{run_id}/diffs/{diff_id}/resolve
```

read roles 由 reconciliation domain RBAC 定义；diff resolve 要求 `accountant` / `system_admin` 与 `Idempotency-Key`。

核心语义：

- canonical domains：`listing`、`order`、`procurement`、`return`、`catalog`、`effect`；
- required reader 缺失 => run failure，不视为 0 diff；
- 0 diff 要求每个 required domain 实际 checked 或明确 `provenEmpty`；
- diff 一律进入人工处置，不自动抹平；
- `resolve` 记录人工 disposition，不自动证明 repair 已完成；
- 后续 external read-back / reconciliation 才能证明一致。

## 8. Domain read APIs

当前包含：

```text
GET /v1/sales-orders?status=&limit=&offset=
GET /v1/return-cases?status=&limit=&offset=
GET /v1/procurements?status=&limit=&offset=
```

精确 read-model 字段以 OpenAPI 为准。

## 9. Identity / ops / health

### Identity

```text
GET /v1/me
```

返回当前 active user 与数据库权威角色；未知/inactive user 返回 `401 unauthenticated`。

### Ops（仅 `system_admin`）

```text
GET  /v1/ops/inbox?status=failed&limit=&offset=
POST /v1/ops/inbox/{event_id}/retry
GET  /v1/ops/runtime
```

inbox retry 要求 `Idempotency-Key`。runtime 返回 worker / inbox / effect / reconciliation 快照。

### Process probes

```text
GET /livez
GET /healthz
GET /readyz
GET /metrics
```

`/readyz` 检查：database、Alembic head、Shopify/Odoo adapter configuration、worker heartbeat。任一失败返回 503 `not_ready`。

## 10. Shopify webhook

```text
POST /v1/webhooks/shopify
```

服务端：

- 对 raw body 做 Shopify HMAC-SHA256 verification；
- 以 webhook id 去重；
- sensitive raw payload 加密进入 vault；
- 创建/更新最小领域事实；
- 创建 DBOS v2 workflow 并发出 `workflow.accepted`；
- API request 本身不直接执行 downstream external effects。

## 11. Idempotency 语义

要求 `Idempotency-Key` 的当前主要写路径包括：

- 五类 async command；
- ordinary decision；
- authorized decision；
- reconciliation diff resolve；
- failed inbox retry。

语义：

- same scope + same key + same body，已完成 => replay stored result；
- same scope + same key + same body，仍 processing => `409 idempotency_in_progress` + `Retry-After: 1`；
- same scope + same key + different body => `409 idempotency_key_conflict`。

5xx/retry 时应复用原 key，不应换 key 规避 uncertain request result。

## 12. Authority / responsibility negative invariants

API consumer 必须遵守：

```text
candidate != Decision
ExperienceUseAdmission.allowed != Decision
Decision != ExecutionAuthorization
PublicationQualification.qualified != Authorization
EffectLedger.succeeded != ConfirmedOutcome
repair disposition != verified repair
WorkflowRun.completed != universal responsibility discharge
client-side inference != execution authority
```

更完整 contract 见 `responsibility-alignment.md`。

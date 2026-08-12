# 事件契约（唯一事实源）

> 本文档是事件信封、事件类型、effect 操作、反馈类型的唯一契约事实源。**命名不可改动**；其他子代理/实现使用完全相同的字符串。关联决策：ADR-0005（信封与 outbox/inbox）。

## 1. 事件信封

所有事件使用统一信封：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `eventId` | uuid（uuid7） | 是 | 事件唯一 id |
| `type` | string | 是 | 事件类型，见第 2 节枚举 |
| `aggregateId` | string | 是 | 业务聚合 id（如目录修订 id、退款 case id） |
| `version` | int | 是 | 聚合版本号（乐观并发） |
| `occurredAt` | string（ISO-8601 UTC） | 是 | 发生时刻 |
| `correlationId` | uuid | 是 | 根追踪 id，贯穿全链路 |
| `causationId` | uuid? | 否 | 产生本事件的上游事件/命令 id；根为 null |
| `producer` | string | 是 | 产生方域名，见第 5 节 |
| `schemaVersion` | int | 是 | 事件 payload schema 版本，起始 1 |
| `payload` | object | 是 | 业务负载（按类型定义） |
| `traceparent` | string? | 否 | W3C trace context（`00-<trace-id>-<span-id>-01`）；API ingress 创建 span 后随 outbox 事件传播，worker 提取并为其建 span |
| `tracestate` | string? | 否 | W3C trace context 附加状态（vendor 字段，随 `traceparent` 一起透传） |

## 2. 事件类型清单（命名不可改动）

### feedback

`feedback.observed` · `feedback.clustered` · `feedback.candidate_created` · `feedback.reviewed` · `feedback.promoted` · `feedback.rejected`

### catalog

`catalog.revision_drafted` · `catalog.normalized` · `catalog.validated` · `catalog.approved` · `catalog.official` · `catalog.superseded`

### listing

`listing.publishing` · `listing.published` · `listing.publish_failed` · `listing.suspended` · `listing.retired`

### order

`order.received` · `order.validated` · `order.accepted` · `order.odo_drafted` · `order.confirmed` · `order.reserved` · `order.picking` · `order.shipped` · `order.invoiced` · `order.in_payment` · `order.reconciled` · `order.closed`

### procurement

`procurement.demand_detected` · `procurement.rfq_drafted` · `procurement.pending_approval` · `procurement.po_confirmed` · `procurement.partially_received` · `procurement.received` · `procurement.bill_posted` · `procurement.in_payment` · `procurement.reconciled` · `procurement.closed`

### return

`return.case_requested` · `return.eligibility_reviewed` · `return.authorized` · `return.goods_received` · `return.inspected` · `return.disposition_approved` · `return.credit_note_posted` · `return.refund_pending` · `return.refund_succeeded` · `return.reconciled` · `return.closed`

### workflow

`workflow.accepted` · `workflow.decision_recorded` · `workflow.completed` · `workflow.failed` · `workflow.cancelled`

#### `workflow.decision_recorded` payload

审批决定落库后由工作流域发出，worker 以 `DBOS.send(destination=workflow_id, topic=work_item_id, idempotency_key=decision_id)` 送达对应 workflow（durable decision messaging，ADR-0011）。字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `workflow_id` | uuid | 目标 workflow run id（= DBOS workflow id） |
| `work_item_id` | uuid | 决定所属 work item id（= DBOS.send topic） |
| `decision_id` | uuid | `WorkItemDecision` id（= DBOS.send idempotency key，防重复送达） |
| `decision` | string | `approve \| reject \| confirm \| cancel` |
| `actor_user_id` | uuid | 审批人 |
| `reason` | string? | 审批备注 |
| `submitted_version` | int | 提交时携带的版本（`expectedWorkflowVersion`） |

投递语义：`DBOS.send` 幂等，重复 relay 不产生第二条业务流程；决策早于 `DBOS.recv` 时 workflow 仍能正确收到。

### effect

`effect.planned` · `effect.dispatched` · `effect.succeeded` · `effect.failed` · `effect.outcome_unknown` · `effect.reconciled` · `effect.manual_reconciliation`

## 3. 初始反馈类型（固定 11 类）

`product_quality` · `content_accuracy` · `pricing_promotion` · `availability` · `payment` · `fulfillment` · `packaging` · `service` · `return_refund` · `fraud_abuse` · `other`

## 4. Effect 操作清单（system.operation）

### shopify

`shopify.product_publish` · `shopify.product_update` · `shopify.fulfillment_create` · `shopify.refund_create`

### odoo

`odoo.product_create` · `odoo.product_update` · `odoo.sale_order_create` · `odoo.sale_order_confirm` · `odoo.stock_move_create` · `odoo.picking_create` · `odoo.picking_validate` · `odoo.invoice_create` · `odoo.invoice_validate` · `odoo.credit_note_create` · `odoo.credit_note_validate` · `odoo.po_create` · `odoo.po_confirm` · `odoo.bill_create` · `odoo.receive_transfer`

## 5. producer / schemaVersion 规则

**producer 取值**（与事实所有权表一致）：

`feedback_intelligence` · `operating_policy` · `catalog` · `listing` · `order` · `procurement` · `return` · `workflow` · `effect` · `shopify_adapter` · `odoo_adapter`

规则：

- 事件 `type` 必须属于对应 producer 的域（如 `catalog.*` 只能由 `catalog` 或经授权的下游产生，见 data-ownership.md）。
- `schemaVersion` 从 1 开始，**仅追加式演进**：允许新增字段，禁止重命名/删除/改类型；破坏性变更必须升 `schemaVersion` 并编写消费端迁移映射。
- 消费者必须忽略未知字段；同一事件重新投递不改变 `eventId`。
- inbox 唯一键 `(consumer, eventId)`；跨数据库边界使用显式 outbox（ADR-0005）。

## 6. 事件流示例（首条纵向切片）

`feedback.observed → feedback.clustered → feedback.candidate_created → feedback.reviewed → feedback.promoted → catalog.revision_drafted → catalog.normalized → catalog.validated → catalog.approved → catalog.official → listing.publishing → listing.published → effect.planned → effect.dispatched → effect.succeeded → effect.reconciled → workflow.completed`

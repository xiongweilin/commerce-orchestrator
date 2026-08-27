# 事件契约（唯一事实源）

> 本文档是事件信封、事件类型、effect 操作、反馈类型的契约事实源。命名不可随意改动；实现新增事件时必须同步此文件。关联决策：ADR-0005、ADR-0011、ADR-0012。

## 1. 事件信封

所有事件使用统一信封：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `eventId` | uuid（uuid7） | 是 | 事件唯一 id |
| `type` | string | 是 | 事件类型，见第 2 节 |
| `aggregateId` | string | 是 | 业务聚合 id |
| `version` | int | 是 | 聚合版本号 |
| `occurredAt` | ISO-8601 UTC | 是 | 发生时刻 |
| `correlationId` | uuid | 是 | 根追踪 id |
| `causationId` | uuid? | 否 | 上游事件/命令 id |
| `producer` | string | 是 | 产生方域名 |
| `schemaVersion` | int | 是 | payload schema 版本，从 1 开始 |
| `payload` | object | 是 | 业务负载 |
| `traceparent` | string? | 否 | W3C trace context |
| `tracestate` | string? | 否 | W3C trace context 附加状态 |

## 2. 事件类型清单

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

`workflow.accepted` · `workflow.decision_recorded` · `workflow.completion_recheck_requested` · `workflow.completed` · `workflow.failed` · `workflow.cancelled`

#### `workflow.decision_recorded`

审批决定落库后发出。worker 通过：

```text
DBOS.send(
  destination=workflow_id,
  topic=work_item_id,
  idempotency_key=decision_id,
)
```

将决定 durable 送达工作流。payload 至少包含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `workflow_id` | uuid | 目标 workflow run id |
| `work_item_id` | uuid | work item id / DBOS topic |
| `decision_id` | uuid | `WorkItemDecision` id / send idempotency key |
| `decision` | string | `approve \| reject \| confirm \| cancel` |
| `actor_user_id` | uuid | 决策人 |
| `reason` | string? | 决策备注 |
| `submitted_version` | int | `expectedWorkflowVersion` |

重复 relay 不产生第二条业务决策；决定早于 `DBOS.recv` 到达也必须可被后续接收。

#### `workflow.completion_recheck_requested`

仅用于已处于 blocked completion assessment 的 workflow。它要求等待中的 DBOS workflow 重新评估当前 bounded-completion evidence，不创建新证据、不直接改变 workflow 状态。

payload：

| 字段 | 类型 | 说明 |
|---|---|---|
| `workflow_id` | uuid | 要求重新评估的 workflow |
| `requested_by_user_id` | uuid | 请求人 |

worker 将该事件转为 `DBOS.send` 到固定 topic `completion-recheck`。

### effect

`effect.planned` · `effect.dispatched` · `effect.succeeded` · `effect.failed` · `effect.outcome_unknown` · `effect.reconciled` · `effect.manual_reconciliation`

`effect.succeeded` 只表示 adapter/execution report 成功，不等价于 `ConfirmedOutcome`。

## 3. 初始反馈类型（固定 11 类）

`product_quality` · `content_accuracy` · `pricing_promotion` · `availability` · `payment` · `fulfillment` · `packaging` · `service` · `return_refund` · `fraud_abuse` · `other`

## 4. Effect 操作清单

### shopify

`shopify.product_publish` · `shopify.product_update` · `shopify.fulfillment_create` · `shopify.refund_create`

### odoo

`odoo.product_create` · `odoo.product_update` · `odoo.sale_order_create` · `odoo.sale_order_confirm` · `odoo.stock_move_create` · `odoo.picking_create` · `odoo.picking_validate` · `odoo.invoice_create` · `odoo.invoice_validate` · `odoo.credit_note_create` · `odoo.credit_note_validate` · `odoo.po_create` · `odoo.po_confirm` · `odoo.bill_create` · `odoo.receive_transfer`

## 5. producer / schemaVersion 规则

producer：

`feedback_intelligence` · `operating_policy` · `catalog` · `listing` · `order` · `procurement` · `return` · `workflow` · `effect` · `shopify_adapter` · `odoo_adapter`

规则：

- 事件 `type` 必须属于对应 producer 的域。
- `schemaVersion` 从 1 开始，默认仅追加式演进；破坏性变更必须升版本并提供消费端迁移映射。
- 消费者必须忽略未知字段；同一事件重新投递不改变 `eventId`。
- inbox 唯一键为 `(consumer, eventId)`。
- 同库内 workflow durability 由 DBOS 负责，不额外构建第二套队列真相。
- 跨数据库边界按 ADR-0005 使用显式 outbox。

## 6. 当前主链示例

```text
workflow.accepted
  -> DBOS workflow
  -> workflow.decision_recorded (需要人工决策时)
  -> effect.planned
  -> effect.dispatched
  -> effect.succeeded | effect.failed | effect.outcome_unknown
  -> reality verification / reconciliation
  -> workflow.completion_recheck_requested (仅 blocked completion 后显式请求)
  -> workflow.completed
```

责任层的 `ExecutionAuthorization`、`EffectRealizationAssessment`、`ConfirmedOutcome`、`ResponsibilityObligation` 等主要是持久化语义记录，不因为存在对应表就强制要求新增同名事件。事件与记录类型不得互相推断等价关系。

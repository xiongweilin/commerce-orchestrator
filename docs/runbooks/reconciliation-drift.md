# Runbook：每日对账与差异处置

## 目标

以权威事实（Odoo、Shopify 渠道状态、effect ledger）为基准，发现投影与本地记录的差异；差异一律进入 `MANUAL_RECONCILIATION` 人工处置，**禁止自动抹平**。

## 1. 每日对账任务

- 定时触发（建议每日 03:00，可配置），由 worker 调度。
- 范围：
  - Shopify：按 `updated_at` 增量拉取产品/订单/退款状态，与 effect ledger 比对；
  - Odoo：发票/账单/库存/PO 状态与本地流程记录比对；
  - 本地：inbox 积压、effect 停留在 `outcome_unknown`/`failed` 的条目。
- 产出：差异清单（差异类型、涉及 effect/workflow、双方状态、时间戳）。

## 2. 发现差异后的处置

1. 自动将相关 effect 置为 `MANUAL_RECONCILIATION`（或新建对账任务），**不做任何自动改写**。
2. 通知运营（console 待办/告警），附差异清单。
3. 人工分类根因：

| 差异类型 | 典型根因 | 处置方式 | 责任角色 |
|---|---|---|---|
| 未投递/未执行 | 派发失败、worker 故障 | 补发 effect（复用原 Idempotency-Key） | system_admin / commerce_lead |
| 结果未知 | 外部超时、结果不明 | 核实外部实际状态后按事实入账或补发 | system_admin / 相关业务角色 |
| 外部被修改 | Shopify/Odoo 侧人工改动 | 以权威事实为准回写投影，或走业务修正流程 | 相应领域 owner |
| 系统缺陷 | bug 导致状态不一致 | 修复后重新对账，必要时走回滚（ADR-0006） | 开发 + system_admin |
| 无需处理 | 已由人工在外部完成等价操作 | 记录原因并标记 reconciled | 相应业务角色 |

4. 处置必须满足审批边界与四眼原则（如涉及退款/PO/库存调整/会计过账）。

## 3. 恢复流程

1. 完成处置动作（补发/修正/标记无需处理），记录处置原因与操作人。
2. 重新触发对账：`POST /v1/reconciliations`（`action: "run"`），或等次日任务。
3. 差异清零 → `effect.reconciled` → 关联工作流 `workflow.completed` → 状态从 `MANUAL_RECONCILIATION` 退出。
4. 若差异反复出现，升级为缺陷工单并登记复盘。

## 4. 不变量

- **禁止自动抹平**：任何代码路径不得静默改写对账差异。
- **禁止直接改权威事实**：发票只能贷项通知单修正、库存只能 stock move/adjustment 改变。
- **处置可审计**：处置记录、操作人、原因、时间全部落库并可追溯（correlationId）。

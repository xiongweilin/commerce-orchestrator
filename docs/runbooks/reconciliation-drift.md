# Runbook：对账与差异处置（canonical reconciliation）

## 目标

以领域 **canonical facts**（Odoo、Shopify 渠道状态、effect ledger）为基准，发现投影与本地记录的差异；差异一律进入 `MANUAL_RECONCILIATION` 人工处置，**禁止自动抹平**（ADR-0013）。

## 1. 对账机制

- 触发：`POST /v1/reconciliations`（受理后由 worker/DBOS 执行；scheduled run 定时触发）。
- 读回：`ReconciliationReader.read_actual(domain, scope) -> list[CanonicalExternalState]`；readers 覆盖 Shopify（listing/order/return/effect）与 Odoo（catalog/order/procurement/inventory/return-credit-note/effect），`CompositeReconciliationReader`/`EffectReconciliationReader` 合并双端。
- 六域比较字段：listing（SKU/product GID/published/content_hash）、order（currency/total/双外部 id）、procurement（po_id/sku/qty/currency）、return（refund id/amount/currency/credit note id）、catalog（sku/odoo_product_id/content_hash）、effect（operation/intent_id/remote_reference/remote_present）。`status` 类字段不做跨词汇表硬比较（保留在 facts 供人工参考）。
- 摘要固定键：`checked`、`diffs`、`failedDomains`、`skippedDomains`、`byDomain`（另附 runType/scheduled/startedAt/finishedAt/verified/deprecationWarnings）。
- 兼容：旧 `"shopify"` 域输入展开为 `listing + order + return` 并返回 `deprecationWarnings`；新客户端使用领域名。

## 2. 「skipped 不算成功」与「0 差异」判定

- **缺 reader 即失败**：请求的必需域缺少 reader 时，整个 reconciliation run 为 `failed`，错误码 `reconciliation_incomplete`，`failedDomains` 列出缺失域；先补 reader 再重跑，禁止把缺失域当“无差异”。
- **scheduled run 不允许 skipped**：`skippedDomains` 必须为空；不允许 optional/skipped 域。
- **“0 差异”证据**：只有每个必需域 `checked > 0`，或显式证明该域当前无实体（`provenEmpty=True`）时才成立。任何报告“0 差异”的 run 必须能附上 `checked` 与 `skippedDomains=[]`。

## 3. 发现差异后的处置

1. 差异写入 diff 清单（`MANUAL_RECONCILIATION`），**不做任何自动改写**。
2. 通知运营（console 待办/`ReconciliationDrift` 告警），附差异清单。
3. 人工分类根因：

| 差异类型 | 典型根因 | 处置方式 | 责任角色 |
|---|---|---|---|
| 未投递/未执行 | 派发失败、worker 故障 | 补发 effect（复用原 Idempotency-Key）；failed inbox 见 [worker-failure.md](worker-failure.md) | system_admin / 相关业务角色 |
| 结果未知 | 外部超时、结果不明（outcome_unknown） | 核实外部实际状态后按事实入账或补发；**不得盲目重发** | system_admin / 相关业务角色 |
| 外部被修改 | Shopify/Odoo 侧人工改动 | 以权威事实为准回写投影，或走业务修正流程 | 相应领域 owner |
| 系统缺陷 | bug 导致状态不一致 | 修复后重新对账，必要时走回滚（ADR-0006） | 开发 + system_admin |
| 无需处理 | 已由人工在外部完成等价操作 | 记录原因并标记 reconciled | 相应业务角色 |

4. 处置必须满足审批边界与四眼原则（如涉及退款/PO/库存调整/会计过账）。

## 4. 恢复流程

1. 完成处置动作（补发/修正/标记无需处理），`resolve_diff` 记录处置与操作人——**resolve 只记录人工处置，不立即把 effect/workflow 标记完成**。
2. 重新触发对账（`POST /v1/reconciliations`，或等 scheduled 任务）。
3. 只有后续重新对账证明一致（每个必需域 `checked > 0`、无 skipped、无 diff），才可将 diff/effect/workflow 置为 resolved/reconciled/completed。
4. 若差异反复出现，升级为缺陷工单并登记复盘。

## 5. 不变量

- **禁止自动抹平**：任何代码路径不得静默改写对账差异。
- **禁止直接改权威事实**：发票只能贷项通知单修正、库存只能 stock move/adjustment 改变。
- **skipped 不算成功**：缺失 reader / 未检查的域不得计入“0 差异”证据。
- **处置可审计**：处置记录、操作人、原因、时间全部落库并可追溯（correlationId）。
- 告警：`ReconciliationIncomplete`（failed/skipped 域 > 0）与 `ReconciliationDrift`（未解决 diff > 0）见 [alerting.md](alerting.md)。

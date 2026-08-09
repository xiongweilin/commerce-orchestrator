# 数据所有权契约（唯一事实源）

> 本文档是领域事实所有权、字段级 owner 规则与投影字段的唯一契约事实源。关联决策：ADR-0003。

## 1. 领域 → 权威数据

| 领域 | 权威数据/表 | 存储 | 更新方式 | 备注 |
|---|---|---|---|---|
| Feedback Intelligence | 结构化反馈、聚类结果、AI 候选建议 | orchestrator PostgreSQL | 反馈域写入；AI 只写建议不写业务字段 | 原始 payload 加密保留 30 天 |
| Operating Policy | 审批边界、SOP、敏感品类规则 | orchestrator PostgreSQL | 政策域维护；compliance 参与 | 审批路由依据 |
| Catalog-PIM | 商品内容/上架修订与不可变版本 | orchestrator PostgreSQL | 目录域写入；catalog_owner 审批 | 冻结后不可修改原候选 |
| Offer&Pricing | 价格/促销规则 | orchestrator PostgreSQL（规则）/ Odoo（执行） | commerce_lead 审批 | 低于毛利约束加 finance_approver |
| Shopify | 渠道侧产品/订单/退款状态 | 外部（Shopify） | 仅渠道 adapter 执行 effect | Admin GraphQL 2026-07；投影带来源 |
| Odoo Product | 商品主数据 | Odoo 19 | 仅 Odoo 内部/窄 method | 以 Odoo 为准 |
| Inventory | 库存量 | Odoo 19（权威）+ 投影 | 仅 stock move / adjustment | 禁止直接改库存字段 |
| Sales-Purchase | 订单、PO、收货发货 | Odoo 19（权威）+ 流程表 | 渠道/采购流程受控写入 | 四眼原则 |
| Finance | 发票/账单/贷项通知单 | Odoo 19（权威账本） | accountant 过账 | 已过账发票只能贷项通知单修正 |
| Workflow Control | workflows / work items / events / outbox / inbox / idempotency / effect ledger | orchestrator PostgreSQL | 系统写入 | DBOS OSS + PostgreSQL |
| Metabase | 只读运营投影 | 投影库（可重建） | 只读消费事件 | 非权威，禁止回写 |

## 2. 字段级 owner 规则

- **每字段单 owner**：任何字段有且仅有一个权威所有者；其他系统只能读投影。
- **禁止 last-writer-wins**：多系统并发写同一字段时，不得静默覆盖；冲突必须显式对账或人工处置。
- **跨系统投影必须携带 `sourceRevision` / `observedAt` / `owner`**：
  - `sourceRevision`：源系统修订标识/版本/时间戳，保证可追溯；
  - `observedAt`：捕获源事实的时刻（ISO-8601 UTC）；
  - `owner`：该字段事实所有者的领域名（见第 1 节）。
- **已过账发票只能通过贷项通知单（credit note）修正**，禁止直接修改发票/账单记录。
- **库存只能通过 stock move / inventory adjustment 改变**，禁止直接改写库存字段。
- **AI 只生成建议**：不批准、不执行、不写业务权威字段；建议写入候选表并携带全部证据元数据（ADR-0009）。

## 3. 审批边界（服务端强制）

固定角色（11 类）：`catalog_owner` · `commerce_lead` · `finance_approver` · `procurement_lead` · `budget_owner` · `warehouse_staff` · `inventory_supervisor` · `accountant` · `customer_service` · `compliance` · `system_admin`（`system_admin` 负责系统运维，不承担业务审批）。

| 变更 | 审批人 | 附加约束 |
|---|---|---|
| 商品内容/上架 | `catalog_owner` | `compliance` 可否决上架/SOP/敏感品类 |
| 调价 | `commerce_lead` | 低于毛利约束再加 `finance_approver` |
| PO | `procurement_lead` 提出 | `budget_owner` 批准 |
| 收货/发货 | `warehouse_staff` | — |
| 库存调整 | `inventory_supervisor` | 影响估值加 `finance_approver` |
| 发票/账单/贷项通知单 | `accountant` | 已过账发票只能贷项通知单修正 |
| 退款 | `customer_service` 提出 → `warehouse_staff` 确认实物 → `finance_approver` 批准金额 | 渠道 adapter 执行 |
| 上架/SOP/敏感品类 | `compliance` | 可否决 |

**四眼原则**：退款 / PO / 库存调整 / 会计过账，禁止同人提出 + 批准；服务端强制校验（提出者 ≠ 批准者）。

## 4. 投影与消费规则

- 投影仅用于读取与展示（如 Metabase、console）；消费方不得回写投影。
- 投影更新来自事件（event-contract.md），按 `eventId` 去重，乱序以 `observedAt`/`sourceRevision` 判定新旧。
- 对账差异一律进入 `MANUAL_RECONCILIATION`，禁止自动抹平（runbooks/reconciliation-drift.md）。

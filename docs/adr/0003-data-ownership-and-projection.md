# ADR-0003：事实所有权与投影规则

- **Status**: Accepted
- **Date**: 2026-08-10

## Context

多系统（Feedback Intelligence、Odoo、Shopify、Metabase 投影）共享同一批商品/订单/库存数据。若无唯一 owner，会出现 last-writer-wins 覆盖、账实不符、审计断链，直接破坏对账与合规。

## Decision

- **每个字段有且仅有一个 owner（权威事实源）**；领域与权威数据映射见 [data-ownership.md](../contracts/data-ownership.md) 与根 README 事实所有权表。
- **跨系统投影必须携带 `sourceRevision` / `observedAt` / `owner`**；任何消费方不得回写投影。
- **禁止 last-writer-wins**：冲突必须显式对账或人工处置，不允许静默覆盖。
- **已过账发票只能通过贷项通知单（credit note）修正**，禁止直接修改发票/账单记录。
- **库存只能通过 stock move / inventory adjustment 改变**，禁止直接改写库存字段。
- **AI 只生成建议**，不批准、不执行，不写业务权威字段（见 ADR-0009）。

## Consequences

**正面**：对账范围清晰；审计链完整；冲突处理路径唯一（对账 + 人工处置）。

**负面/约束**：新增跨域字段必须先定义 owner 并更新契约；映射与投影层实现成本增加；跨系统写路径变窄（受控 adapter 执行）。

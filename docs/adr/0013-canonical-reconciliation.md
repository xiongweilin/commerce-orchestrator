# ADR-0013：Canonical Reconciliation 与「skipped 不算成功」

- **Status**: Accepted
- **Date**: 2026-08-11

## Context

v1 对账按字符串状态比较（本地 `closed` vs 渠道 `PAID` 之类），且缺少 reader 时
直接跳过该域；`skipped` 域被当作“无差异”计入成功。这会导致：

- 跨系统词汇表不同的状态被误判为差异或误判为一致；
- 必需域未读回时仍报告“0 差异”，掩盖数据缺失；
- 对账结果无法区分“真正一致”与“没检查”。

## Decision

- **以领域 canonical facts 比较**：新增 `ReconciliationReader` interface

  ```python
  read_actual(domain, scope) -> list[CanonicalExternalState]
  ```

  生产 reader：Shopify（listing/order/return/effect）、Odoo（catalog/order/
  procurement/inventory/return-credit-note/effect）、`CompositeReconciliationReader`
  （order/return 合并双端）、`EffectReconciliationReader`（按 target system 分发）、
  `InMemoryReconciliationReader`（测试）。
- **比较字段**（`COMPARE_FIELDS`，六域）：listing（SKU/product GID/published/
  content_hash）、order（currency/total/双外部 id）、procurement（po_id/sku/qty/
  currency）、return（refund id/amount/currency/credit note id）、catalog（sku/
  odoo_product_id/content_hash）、effect（operation/intent_id/remote_reference/
  remote_present）。`status` 类字段不做跨词汇表硬比较（保留在 facts 供人工参考）。
- **缺 reader 即失败**：请求的必需域缺少 reader 时，整个 reconciliation run 为
  `failed`，错误码 `reconciliation_incomplete`，`failedDomains` 列出缺失域。
- **scheduled 不允许 skipped**：scheduled run 的 `skippedDomains` 必须为空；
  不允许 optional/skipped 域。
- **“0 差异”的定义收紧**：只有每个必需域 `checked > 0`，或显式证明该域当前无实体
  （`provenEmpty=True`）时才成立；摘要固定包含 `checked`、`diffs`、
  `failedDomains`、`skippedDomains`、`byDomain`。
- **diff 解决不立即完成**：`resolve_diff` 只记录人工处置，不把 effect/workflow
  置为完成；只有后续重新对账证明一致，才可将 diff/effect/workflow 置为
  resolved/reconciled/completed。
- **兼容旧输入**：旧 `"shopify"` 域输入在一个发布周期内展开为
  `listing + order + return` 并返回 `deprecationWarnings`；新客户端使用领域名。

## Consequences

**正面**：对账结果可解释、可证明（checked > 0 / provenEmpty）；缺失 reader 显式失败，
不再以“跳过”冒充成功；状态字段不再跨词汇表硬比较，人工聚焦真实事实差异。

**负面/约束**：每个必需域都需要生产 reader 且字段映射要经沙盒实测（ADR-0008 门禁）；
“0 差异”证据必须附 `checked` 与 `skippedDomains=[]`；reader 字段不符时差异以人工
diff 呈现（fail-closed）。

## Supersedes

- **ADR-0010** 中“对账差异一律进 `MANUAL_RECONCILIATION`、禁止自动抹平”的规则保留；
  本 ADR 补充“skipped 不算成功”与 `reconciliation_incomplete` 语义。
- 旧 `services/reconciliation.py` 的字符串状态比较与“缺 connector 跳过”路径
  （仅作为无 readers 时的向后兼容 legacy 路径保留，不用于 scheduled run）。

## References

- [runbooks/reconciliation-drift.md](../runbooks/reconciliation-drift.md)
- [event-contract.md](../contracts/event-contract.md)

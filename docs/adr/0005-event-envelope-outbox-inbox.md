# ADR-0005：事件信封与 outbox/inbox

- **Status**: Accepted
- **Date**: 2026-08-10

## Context

跨域解耦需要事件；但若每个步骤都引入独立队列，会产生两套真相、重复投递、顺序问题与运维复杂度，与 ADR-0002（无消息中间件）冲突。

## Decision

- **统一事件信封**：`eventId` / `type` / `aggregateId` / `version` / `occurredAt` / `correlationId` / `causationId` / `producer` / `schemaVersion` / `payload`（字段说明见 [event-contract.md](../contracts/event-contract.md)）。
- **inbox 唯一键 `(consumer, eventId)`**：每个消费者对同一事件只处理一次，重复投递被去重。
- **仅跨数据库边界使用显式 outbox**：如投递 Shopify webhook、Odoo integration outbox；同一数据库内通过 DBOS 事务步骤与事件表完成。
- **DBOS 事务步骤内不叠加第二套队列真相**：工作流状态由 DBOS 持久化，事件表是业务事实，二者同库一致；避免业务 outbox 与 DBOS 恢复日志不一致。

## Consequences

**正面**：事件可审计、可重放；重复投递天然免疫；无独立队列，故障面小。

**负面/约束**：跨数据库投递依赖 outbox relay（worker 内实现）；消费方必须幂等并容忍乱序（对账兜底）；schemaVersion 演进需严格向后兼容。

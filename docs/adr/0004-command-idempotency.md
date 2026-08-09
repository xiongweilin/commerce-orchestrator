# ADR-0004：命令幂等（Idempotency-Key）

- **Status**: Accepted
- **Date**: 2026-08-10

## Context

外部调用、webhook、重试与网络抖动会造成同一命令被重复提交；长流程启动与幂等记录必须原子提交，否则会出现“记了幂等键但没启动工作流”或重复启动。

## Decision

- **所有内部写命令必须携带 `Idempotency-Key`**（header），语义详见 [api-contract.md](../contracts/api-contract.md)。
- 服务端保存 `(scope, key, requestHash, result)`：
  - `scope` = 认证主体 + 端点 + 聚合 id；
  - `requestHash` = 规范化请求体的 SHA-256；
  - `result` = 首次执行的响应（含长命令响应与最终状态入口）。
- **同 `scope+key` 且 requestHash 相同** → 重放存储的 result（幂等重放）。
- **同 `scope+key` 但 requestHash 不同** → `409 Conflict`（`idempotency_key_conflict`）。
- 不同 `scope` 互不影响；5xx 重试必须复用同一 key。
- 幂等记录写入、业务写入与工作流启动在**同一事务/恢复单元**内提交。

## Consequences

**正面**：重试安全；重复 webhook/双击不会产生双效果；对账时可依据幂等记录判断“是否已派发”。

**负面/约束**：所有写命令必须设计规范化的 requestHash 算法；错误处理多一个 409 分支；客户端必须管理 key 生命周期。

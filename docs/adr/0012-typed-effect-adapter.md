# ADR-0012：Typed effect adapter、fail-closed、幂等与 outcome_unknown 策略

- **Status**: Accepted
- **Date**: 2026-08-11

## Context

v1 的外部效果执行存在三个缺陷：

1. **无强类型契约**：connector 返回 `EffectResult` + 抛 `OutcomeUnknownError`，
   部分路径靠“错误字符串是否包含 timeout”推断状态，无法保证分类正确；
2. **stub 伪装成功**：未注册的 connector 曾返回成功，破坏“缺失即显式失败”的承诺；
3. **幂等策略分散**：每个 operation 的“先查后建/读回/状态预检”逻辑没有统一入口与
   统一的重试/未知结果规则。

## Decision

- **内部强类型 interface**（`app/schemas/effects.py`）：

  ```python
  EffectExecutionRequest: intent_id, operation, parameters, idempotency_key,
                          request_hash, correlation_id, approval_ref
  EffectExecutionOutcome =
      Succeeded(remote_reference, response_hash, replayed)
      | Failed(error_code, detail, retryable, response_hash)
      | OutcomeUnknown(error_code="outcome_unknown", detail)
  ```

- **Pydantic discriminated union**：`EFFECT_OPS` 每个 operation 一个参数模型
  （`EffectParametersUnion`，判别字段 `operation`）；`validate_effect_parameter_coverage`
  在启动时校验集合一致，集合漂移直接失败。
- **禁止字符串推断**：adapter 按异常类型（`isinstance(httpx.TimeoutException)` 等）
  与显式异常（`OutcomeUnknownError`、`RetryableEffectError`）分类；
  `OutcomeUnknownError` 一对一映射为 `OutcomeUnknown`。
- **Fail-closed**：adapter/operation 未配置是启动配置错误，worker 不 ready，
  **绝不标记 succeeded**；删除“未注册 connector 返回成功”的 stub；测试用显式
  `InMemoryEffectAdapter`。
- **执行顺序固定**（WP4 编排 + WP5 seam）：
  1. DBOS transaction：`planned → dispatched`，递增 attempt；
  2. DBOS step：执行 adapter（`execute_effect`）；
  3. DBOS transaction：写入 `succeeded | failed | outcome_unknown`
     （`apply_outcome`）。
- **重试与未知结果**：仅 `Failed(retryable=True)` 可重试，上限 3 次；
  `OutcomeUnknown` **永不自动重发**，立即令 workflow 进入 `needs_reconciliation`；
  每次调用携带同一 intent_id + idempotency_key。
- **操作级幂等策略**（按计划表格落地）：Shopify `refund_create` 用原生
  idempotency directive；product update/publish 按 GID/publication 读回，目标状态
  已存在视为成功（`replayed=True`）；fulfillment 调用前查询 fulfillment order 与
  已有 fulfillment；Odoo create 类以 `CO:<intent_id>` 写入
  `client_order_ref`/`partner_ref`/`origin`/`ref`/`invoice_origin` 并先查后建；
  confirm/validate/post/receive 调用前读状态，已达目标状态视为幂等成功。
- **补偿固定人工**：禁止自动反向写（不自动下架、不自动撤销发票/贷项通知单、
  不重复退款）；`outcome_unknown` 时 ledger 写 `compensation="reconciliation"`，
  差异只能人工 resolve，重新对账一致后才完成。

## Consequences

**正面**：效果执行有单一强类型入口；分类、重试、幂等、补偿规则集中且可测；
缺 adapter 显式失败，不再伪装成功；资金/库存效果未知时系统停止自动动作并进对账。

**负面/约束**：每个 `EFFECT_OPS` 都必须维护参数模型与适配（集合校验强制）；
读回幂等依赖远端可读性与字段映射；人工补偿流程增加处理时长。

## Supersedes

- **ADR-0007 / ADR-0008** 中基于裸 `EffectResult` 直连 connector 的 effect 执行路径：
  连接器能力（Shopify GraphQL 2026-07、Odoo JSON-2）与幂等读回策略保留，
  但执行统一收敛到 typed seam；两份 ADR 的集成/踩坑记录不变。
- 旧的“未注册 connector 返回成功”stub 与可变全局注册表
  （`register_connector`/`get_connector`）被删除；依赖改为构造时注入。

## References

- [architecture.md](../architecture.md)（5.3/5.4）
- [event-contract.md](../contracts/event-contract.md)（effect 事件与操作清单）

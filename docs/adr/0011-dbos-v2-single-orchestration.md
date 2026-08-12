# ADR-0011：DBOS v2 单一编排主线、inbox relay 与 durable decision messaging

- **Status**: Accepted
- **Date**: 2026-08-11

## Context

P7 之前的编排存在多条“真相”：

- `commands.py` 内含 v1 inline 状态机 continuation，API/脚本可直接推进领域状态；
- `workflows/vertical_slice.py` 是 DBOS v1 切片，与 v1 inline 逻辑重复；
- inbox 只是简单的 `pending → processed | failed` 表，无 lease、无退避、无重试上限；
- 审批决定在 API 进程内同步跑 continuation，worker 恢复与审批无关联。

结果：同一流程的状态推进分散在 API、模拟脚本、worker 多处，无法保证“API/webhook →
worker → DBOS → 真实 adapter → 对账”的单一可恢复主线；worker 崩溃时可能重复或丢失
外部副作用。

## Decision

对外只保留三个主要 interface（`services/commands.py`、`services/approvals.py`）：

```python
accept_command(command, actor, idempotency_key, correlation_id) -> AcceptedCommand
submit_decision(work_item_id, actor, decision, expected_version, idempotency_key) -> DecisionResult
start_workflow_run(workflow_run_id) -> DBOSWorkflowHandle
```

- **`accept_command` 只做受理**：同一事务内创建 `WorkflowRun`
  （`orchestration_engine='dbos'`、`workflow_version=2`）、最小化输入，并写入
  `workflow.accepted` inbox 事件（consumer=`worker`）。**不做领域状态迁移、不做外部效果**。
- **worker relay 是唯一的启动路径**：worker 消费 `workflow.accepted`，以
  `SetWorkflowID(str(run.id))` + `DBOS.start_workflow` 启动 v2 definition；
  相同 `workflow.accepted` 重放多次只启动一个 workflow（确定性 workflow id 去重）。
- **definition 注册表**：`(workflow_type, workflow_version)` → definition；
  所有新流程使用 `workflow_version=2`；现有纯状态机与 guard 保留为内部实现，
  DBOS workflow 调用它们，**不复制第二套转换规则**。
- **Durable 审批**：workflow 创建 work item 后执行
  `DBOS.recv(topic=str(work_item_id), timeout_seconds=30*24*3600)`；
  审批 API 在同一事务写 `WorkflowRun`/`WorkItem`/`WorkItemDecision`
  （`work_item_id` 唯一约束）并写入 `workflow.decision_recorded` inbox 事件；
  worker 以 `DBOS.send(destination=workflow_id, topic=work_item_id,
  idempotency_key=decision_id)` 将决定送达 workflow。决策早于 `DBOS.recv` 时仍能正确收到。
- **inbox relay 状态机**：`pending → processing → processed | failed`；`InboxEvent`
  增加 `attempts`/`next_attempt_at`/`lease_until`/`last_error`/`processed_at`
  与 `(consumer, status, next_attempt_at)` 索引。
- **relay 算法固定**（`services/outbox_inbox.py`）：
  1. `FOR UPDATE SKIP LOCKED` 按批认领（SQLite 下降级为 no-op）；
  2. 置 `processing` + 30 秒 lease 后提交；
  3. 事务外执行 DBOS `start_workflow` / `send`；
  4. 成功事务性标记 `processed`；
  5. 第 3 步成功、第 4 步失败：lease 到期后重处理，确定性 workflow id /
     send idempotency key 防重复；
  6. 失败指数退避，最大 10 次，超出进 `failed` 并告警；
  7. worker 启动/周期回收 lease 已过期的 `processing` 项（回收不涨 attempts）；
  8. 默认轮询 500ms、批次 50。
- **新状态语义**：`completed`（全部必需 effect 成功且对账通过）、
  `needs_reconciliation`（存在 `outcome_unknown`/跨系统差异/需人工补偿，**非终态**）、
  `failed`（确定性 guard 失败/配置错误/重试耗尽）、`cancelled`（拒绝/取消/审批超期）。
- **在途兼容**：既有 v1/in-flight 流程不强行迁移；`legacy_inline` 非终态通过
  v1 切片兼容 adapter 完成；所有新命令只创建 DBOS v2 workflow。

## Consequences

**正面**：编排只有一条可恢复主线（worker 经 inbox relay 启动/驱动 DBOS）；
恢复、幂等、审批送达全部由 DBOS + PostgreSQL 保证；决策消息带
`idempotency_key=decision_id`，重复 send 不产生第二条业务流程。

**负面/约束**：新命令必须经 API 受理 + worker relay，不再允许脚本直接推进状态；
`legacy_inline` 非终态清零并连续一个观察周期无兼容调用后，需单独变更删除 legacy
continuation；v2 审批依赖 worker 运行（worker 缺失时 work item 不会自动推进）。

## Supersedes

- **ADR-0005** 中关于 inbox 的“仅去重、无认领语义”的实现部分：本 ADR 保留统一事件信封
  与 inbox 唯一键 `(consumer, eventId)`，但把 inbox 从简单队列升级为带
  lease/退避/dead-letter 的 relay，并新增 `workflow.decision_recorded` 的
  DBOS.send 投递路径。
- **ADR-0006** 的版本语义沿用（`expectedWorkflowVersion` 比较、旧版本不原位修改），
  但“审批同步 continuation”改为 DBOS.recv/send 的 durable messaging；
  ADR-0006 其余内容不变。
- 旧架构中“API/模拟脚本直接推进领域状态”的做法被废除；`vertical_slice.py` 仅保留为
  在途 legacy 的兼容 adapter（v1 切片，供 webhook 在途流与 legacy_inline 完成）。

## References

- [architecture.md](../architecture.md)
- [api-contract.md](../contracts/api-contract.md)
- [event-contract.md](../contracts/event-contract.md)

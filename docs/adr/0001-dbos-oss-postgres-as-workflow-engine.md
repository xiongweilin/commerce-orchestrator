# ADR-0001：以 DBOS OSS + 独立 PostgreSQL 作为长流程引擎（v1 不依赖 DBOS Conductor）

- **Status**: Accepted
- **Date**: 2026-08-10

## Context

系统核心是跨系统长流程（反馈 → 候选 → 审批 → 目录/PIM → 渠道发布 → 效果记账 → 对账），流程可能挂起数小时到数天等待人工审批或外部渠道结果。因此需要：

- 崩溃/重启后从最后完成的步骤恢复，不重复也不丢失外部副作用；
- 步骤至少一次（at-least-once）、事务步骤恰好一次（exactly-once）语义；
- 与业务数据同库的事务性，避免“队列与业务不一致”；
- 可观测、可审计。

候选方案：自研状态机 + 消息队列、Temporal、Hatchet、DBOS。

## Decision

- 采用 **DBOS OSS（Python 包 `dbos`，验证版本 2.29，要求 Python >=3.10；本仓库使用 Python 3.12）** + **独立 PostgreSQL** 作为长流程引擎与系统状态存储。
- 使用 `@DBOS.workflow()` / `@DBOS.step()` / `@DBOS.transaction()`（同步事务）；SQLAlchemy datasource 受支持；系统库通过环境变量 `DBOS_SYSTEM_DATABASE_URL` 配置；表可自动创建或由 `dbos migrate` 管理。
- 依赖 DBOS 运行时保证：工作流从最后完成的步骤恢复；step 至少一次执行；事务步骤恰好一次。
- **v1 不依赖 DBOS Conductor**：Conductor 自托管为专有许可，免费 key 仅限开发/试验场景，且限单应用单 executor；OSS 单节点可自动恢复，多节点恢复需要协调或引入 Conductor。
- 未来若需要多节点调度/高可用，再评估 **Temporal / Hatchet**；**明确不自研控制面**。

## Consequences

**正面**：恢复语义开箱即用；无需引入独立消息队列；事务步骤与业务数据同库，一致性简单。

**负面/约束**：单节点模型限制吞吐与 HA（v1 可接受）；DBOS 生态较新，需固定版本并持续跟踪上游；多节点时需要重新走 ADR 评估 Temporal/Hatchet。

## References

- <https://docs.dbos.dev/production/hosting-conductor>
- <https://docs.dbos.dev/architecture>
- <https://docs.dbos.dev/python/tutorials/workflow-tutorial>

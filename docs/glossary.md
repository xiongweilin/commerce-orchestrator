# 领域术语表

本表为全仓库统一词汇；中文术语 + English identifier + 一句话定义。事件类型、effect 操作、角色与审批边界以 `docs/contracts/` 为准。

| 中文术语 | Identifier | 定义 |
|---|---|---|
| 运营控制塔 | operations control tower | 跨系统编排工作流、审批、执行效果、责任解释与对账的内部运营中枢。 |
| 事实所有权 | data ownership | 每个字段有且仅有一个权威所有者，其他系统只能消费投影。 |
| 投影 | projection | 从权威事实派生的只读视图，必须携带来源信息且禁止回写。 |
| 权威事实源 | source of truth | 某字段唯一可写的事实所有者（如 Odoo 商品/财务事实、Shopify 渠道事实、Commerce effect ledger）。 |
| 候选冻结 | candidate freeze | 候选进入 `frozen` 后不可修改原候选，只能生成新版本或走审批。 |
| 决策 | Decision / WorkItemDecision | 人对一个 work item 作出的 durable 决定；Decision 不等价于执行授权。 |
| 执行授权 | ExecutionAuthorization | 服务端从明确 Decision 派生的 exact-scope authority，绑定 subject/version/fingerprint、target、operation、policy/environment 与可选 expiry。 |
| 授权配置 | authorization profile | 服务端拥有的授权模板；当前包括 listing publish、return credit note、return refund 三类。 |
| 当前责任 | current responsibility | 按当前 authorization、qualification、Experience-use 与 open obligations 重新计算的 eligibility explanation。 |
| 历史依赖 | HistoricalExperienceUse | 某次历史 judgment 当时实际依赖的 Experience/knowledge snapshot；存在历史依赖不代表当前仍 eligible。 |
| 责任绑定 | ResponsibilityBinding | 将 HistoricalExperienceUse 与 Commerce workflow/subject/version 明确绑定的 sidecar 记录。 |
| 责任义务 | ResponsibilityObligation | 明确、可审计、带 scope 的责任记录；状态可从 open 变为 discharged，但历史记录不删除。 |
| 当前开放责任 | openResponsibility | Responsibility Inspector 中仍处于 open 的 obligation 子集。 |
| 发布资格 | PublicationQualificationAssessment | 对 exact catalog revision/channel/purpose/policy/adapter/environment 的当前发布资格评估；qualified 不等价于 Authorization。 |
| 效果账本 | effect ledger | 记录每个外部 effect 的执行生命周期：planned → dispatched → succeeded/failed/outcome_unknown → reconciled/manual_reconciliation。 |
| 现实验证 | EffectRealizationAssessment | 对 effect 是否在真实外部系统中实现的显式 read-back/verification 记录。 |
| 已确认业务结果 | ConfirmedOutcome | 仅基于 `VERIFIED` realization assessment + evidence 建立的 bounded outcome；不等价于 universal goal completion。 |
| 结果未知 | outcome_unknown | 外部调用结果不确定的 effect 状态；**不自动重发**，转入 reconciliation。 |
| 对账 | reconciliation | 以 canonical facts/read-back 比较外部真实状态、业务期望与本地执行记录。 |
| 人工对账状态 | MANUAL_RECONCILIATION | 差异待人工处置的状态；禁止自动抹平。 |
| 对账处置 | ReconciliationResolution | 对差异的人工 disposition/verification metadata；处置记录本身不等价于 repair 已真实发生。 |
| 有界完成 | bounded completion | 只证明某 workflow 声明的有限 requirements 已满足，coverage claim 固定为 `declared-scope-only`。 |
| 完成重新评估 | completion recheck | 对 blocked completion 发出 durable `workflow.completion_recheck_requested`，要求 workflow 重新评估当前证据；不制造证据、不直接完成。 |
| 发件箱 | outbox | 跨数据库边界投递事件的持久化发件箱；同库内不叠加第二套 workflow 队列真相。 |
| 收件箱 | inbox | 消费者侧去重/relay 表，唯一键 `(consumer, eventId)`。 |
| 幂等键 | Idempotency-Key | 写命令/决策等要求携带的幂等键；同 scope+key 同 body 重放，同 key 异 body 返回 409。 |
| 审批边界 | approval boundary | 每类变更必须由指定角色决策的规则。 |
| 四眼原则 | four-eyes principle | 提出人与批准人不得为同一人；高风险业务动作由服务端强制。 |
| 事件信封 | event envelope | 事件统一字段：eventId/type/aggregateId/version/occurredAt/correlationId/causationId/producer/schemaVersion/payload。 |
| 关联链 | correlationId | 跨系统、跨事件的根追踪 id，用于审计与排查。 |
| 因果链 | causationId | 产生本事件的上游事件/命令 id。 |
| 渠道适配器 | channel adapter | 与 Shopify/Odoo 通信的 adapter，通过 typed effect seam 执行 effect 并校验响应。 |
| typed effect seam | EffectExecutionRequest/Outcome | 统一的外部副作用请求/结果边界，避免 workflow 直接散落调用 adapter。 |
| 长流程引擎 | workflow engine | 持久化、可恢复的长流程运行时（本仓库为 DBOS OSS + PostgreSQL）。 |
| 来源修订号 | sourceRevision | 投影携带的源系统版本/修订标识。 |
| 观测时间 | observedAt | 捕获源事实的时刻（ISO-8601 UTC）。 |
| 所有者 | owner | 投影字段中标识字段事实所有者的领域名。 |
| 清洗器版本 | sanitizerVersion | 生成候选时使用的数据清洗/脱敏规则版本。 |
| 建议哈希 | proposalHash | 候选建议内容哈希，用于不变性与审计。 |
| 预期工作流版本 | expectedWorkflowVersion | 决策携带的乐观并发版本；不匹配返回 409。 |
| 工作流版本不可变 | workflow version immutability | 已发布 workflow definition 不原位改写，只通过新版本演进。 |
| 只读投影库 | read-only projection | Metabase 消费事实/事件生成的运营视图，可重建、非权威。 |
| 最小字段原则 | minimal field principle | 只采集业务必需字段，从源头减少敏感数据暴露。 |
| 基线备份 | baseline backup | 沙盒/基础设施变更前建立并验证可恢复的备份。 |
| 故障注入 | kill injection | 随机/定点杀死 worker 以验证 DBOS 恢复语义。 |
| 差异抹平 | auto-smoothing | 对账时静默改写差异的行为，本系统明确禁止。 |

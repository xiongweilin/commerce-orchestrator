# 领域术语表

本表为全仓库统一词汇；中文术语 + 英文 identifier + 一句话定义。契约中的事件类型、effect 操作、反馈类型、角色与审批边界以 [docs/contracts/](contracts/) 为准，命名不可改动。

| 中文术语 | Identifier | 定义 |
|---|---|---|
| 运营控制塔 | operations control tower | 跨系统编排工作流、审批、幂等与对账的内部运营中枢。 |
| 事实所有权 | data ownership | 每个字段有且仅有一个权威所有者，其他系统只能消费投影。 |
| 投影 | projection | 从权威事实派生的只读视图，必须携带来源信息且禁止回写。 |
| 权威事实源 | source of truth | 某字段唯一可写的事实所有者（如 Odoo 商品主数据、effect ledger）。 |
| 候选冻结 | candidate freeze | 候选进入 frozen 后不可修改原候选，只能生成新版本或走审批。 |
| 效果账本 | effect ledger | 记录每个对外 effect 全生命周期的账本：planned → dispatched → succeeded/failed/outcome_unknown → reconciled/manual_reconciliation。 |
| 发件箱 | outbox | 跨数据库边界投递事件的持久化发件箱；同库内不叠加第二套队列真相。 |
| 收件箱 | inbox | 消费者侧去重表，唯一键 (consumer, eventId)。 |
| 幂等键 | Idempotency-Key | 写命令必带；按 scope+key+requestHash+result 保存，同 key 不同 body 返回 409。 |
| 审批边界 | approval boundary | 每类变更必须由指定角色审批的规则（见 data-ownership.md）。 |
| 四眼原则 | four-eyes principle | 提出人与批准人不得为同一人；退款/PO/库存调整/会计过账强制。 |
| 纵向切片 | vertical slice | 覆盖全链路的最小端到端能力，首个切片为“反馈→发布→对账”21 步。 |
| 聚合 | aggregate | 一致性边界内的一组业务对象（如一个目录修订、一个退款 case）。 |
| 事件信封 | event envelope | 事件统一包裹字段：eventId/type/aggregateId/version/occurredAt/correlationId/causationId/producer/schemaVersion/payload。 |
| 关联链 | correlationId | 跨系统、跨事件的根追踪 id，用于审计与排查。 |
| 因果链 | causationId | 产生本事件的上游事件/命令 id，构成因果链。 |
| 渠道适配器 | channel adapter | 负责与外部渠道（Shopify/Odoo）通信的适配器，执行 effect 并校验结果。 |
| 对账 | reconciliation | 以权威事实为基准比对投影/本地记录，发现并处置差异的流程。 |
| 人工对账状态 | MANUAL_RECONCILIATION | effect 差异待人工处置的状态；禁止自动抹平。 |
| 结果未知 | outcome_unknown | 外部调用超时/结果不明后的 effect 状态，走有限重试与人工对账。 |
| 长流程引擎 | workflow engine | 持久化、可恢复的长流程运行时（本仓库为 DBOS OSS + PostgreSQL）。 |
| 来源修订号 | sourceRevision | 投影携带的源系统版本/修订标识，保证可追溯。 |
| 观测时间 | observedAt | 投影捕获源事实的时刻（ISO-8601 UTC）。 |
| 所有者 | owner | 投影字段中标识字段事实所有者的领域名。 |
| 清洗器版本 | sanitizerVersion | 生成候选时使用的原始数据清洗/脱敏规则版本。 |
| 建议哈希 | proposalHash | 候选建议内容的哈希，用于不变性与审计。 |
| 预期工作流版本 | expectedWorkflowVersion | 审批决策携带的版本号；不匹配返回 409，防止过期决策。 |
| 工作流版本不可变 | workflow version immutability | 已发布工作流的步骤顺序不可原位修改，只能发新版本。 |
| 只读投影库 | read-only projection | Metabase 消费事件生成的运营视图库，可重建、非权威。 |
| 最小字段原则 | minimal field principle | 只采集业务必需字段，从源头减少敏感数据暴露。 |
| 基线备份 | baseline backup | 每批 Odoo 模块安装前建立的 DB+filestore 备份，先恢复验证再进下一批。 |
| 故障注入 | kill injection | 随机/定点杀死 worker 进程以验证恢复语义的测试手段。 |
| 差异抹平 | auto-smoothing | 对账时静默改写差异的行为，本系统明确禁止。 |

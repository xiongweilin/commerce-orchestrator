# 当前实现快照

[English](current-implementation.md) | [简体中文](current-implementation.zh-CN.md)

本文记录 commerce-orchestrator 当前实现形态，使 repository prose 与实际代码保持同步。历史架构决策位于 docs/adr，规范性 API、event 和 data-ownership contract 位于 docs/contracts。

## 项目边界

Commerce Orchestrator 是个人 full-stack sandbox，用于 durable、governed 的跨系统 commerce workflow。它使用模拟数据、Shopify development store 和 Odoo 19 sandbox，没有 production-promotion path、真实用户或真实订单。

AI 仅用于 candidate generation，不负责 approval、生成 execution authority、dispatch external effect 或重写 authoritative business fact。

## Runtime topology

API 负责命令和 webhook 的 validation/recording；PostgreSQL inbox relay 把 durable signal 交给 DBOS worker；worker 负责 workflow、typed effect、Shopify/Odoo integration、reconciliation、privacy cleanup、heartbeat 和 metrics。

## Durable workflow

新 command/webhook 使用单一主线：acceptance → inbox relay → DBOS workflow → durable approval wait → effect planning/dispatch → external read-back/realization verification → reconciliation → bounded completion。

needs_reconciliation 是非终态；outcome_unknown 不能 blind resend。

## Decision 与 execution authority

WorkItemDecision 不等于 execution authority。需要 effect authority 的 approval 使用 authorized-decisions，由 server 选择 profile 并生成 exact-scope ExecutionAuthorization。

Authorization 绑定 workflow、subject/version/fingerprint、target、operation、policy、environment 和可选 expiry；dispatch 时必须重新验证 current facts。

## Responsibility plane

核心链：evidence → Experience-use evaluation → HistoricalExperienceUse → Decision → ExecutionAuthorization → effect execution → EffectRealizationAssessment → ConfirmedOutcome → ResponsibilityObligation lifecycle。

必须保持：candidate != Decision；Decision != Authorization；qualification != Authorization；EffectLedger.succeeded != ConfirmedOutcome；reconciliation disposition != verified repair；WorkflowRun.completed != universal responsibility discharge。

currentResponsibility 根据当前状态重新评估；historical 保留当时实际依赖。历史存在不自动成为 current eligibility。

## Reality、reconciliation 与 completion

所有外部 effect 经过 typed effect seam 与 effect ledger。succeeded 只是 adapter execution report；只有独立 read-back 形成 VERIFIED realization assessment 后，才能生成 ConfirmedOutcome。

Canonical reconciliation 覆盖 listing、order、procurement、return、catalog、effect。缺 required reader 是 failure；zero-diff 需要 checked row 或 proven empty。Manual resolution 不证明真实修复已发生。

Completion 是 declared-scope claim，永久 coverage 为 declared-scope-only。completion-recheck 只发 durable reevaluation signal。

Console 是 inspection/request consumer，不能从 client-side data 推断 authority。

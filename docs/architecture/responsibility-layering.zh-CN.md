# Responsibility Layering

[English](responsibility-layering.md) | [简体中文](responsibility-layering.zh-CN.md)

该架构在不替换 DBOS、既有 Commerce domain state machine 或外部 fact ownership 的前提下，增加 semantic/responsibility plane。

```text
Commerce business/domain facts
→ portable Experience contracts
→ Commerce responsibility specialization
→ DBOS durable workflow
→ Effect Ledger + typed adapters
→ external read-back / realization / reconciliation
→ ConfirmedOutcome + bounded completion
```

## 所有权

- `agent-kernel/contracts/`：generic portable responsibility semantics。
- Commerce：API/event vocabulary 和 domain specialization。
- Commerce PostgreSQL：orchestration/responsibility persistence。
- DBOS：唯一 durable workflow engine。
- Odoo/Shopify：继续持有 `data-ownership.md` 声明的 facts。
- Console：非权威，不能生成或推断 execution authority。

Commerce production code 只能消费 portable public contract/reference seam，不能依赖 portable governance dispatch、`InvocationPermit`、provider routing 等 execution internal。

## Decision 与 authority 分离

`WorkItemDecision` 是 approval 必要条件，但不是 `ExecutionAuthorization`。

需要 effect authority 的 work item 必须调用 `POST /v1/work-items/{id}/authorized-decisions`。同一 transaction 中记录 Decision 和 exact-scope authorization；dispatch 再按 current subject version/fingerprint、environment、policy 和 operation 重新验证。

Role membership、历史 approval、client-side object 都不能替代 current matching authorization。

## CURRENT 与 HISTORICAL

Responsibility Inspector 分开：

- `historical`：之前判断实际依赖的 Experience/knowledge state；
- `currentResponsibility`：当前 authorization、qualification、Experience-use state 和 open obligation 下的 fresh eligibility。

历史记录是 immutable evidence，不会自动成为 current eligibility。

## Execution、reality、outcome

```text
ExecutionAuthorization
→ EffectLedger execution fact
→ EffectRealizationAssessment
→ ConfirmedOutcome
```

`EffectLedger.succeeded` 只说明 adapter/execution success，不等于 business outcome。ConfirmedOutcome 必须有显式 `VERIFIED` reality assessment。

Reconciliation disposition 也不是 repair 已发生的证明；仍需后续 read-back/verification。

## Obligation 与 bounded completion

Responsibility obligation 显式且有 scope，discharge 后继续保留历史。单个相关 reconciliation diff 不会全局 invalidate projection。

Workflow completion 是 declared-scope claim。Completion service 检查 domain terminal state、required effect、current qualification、realization、required outcome、settled work item 和 blocking reconciliation。

永久 coverage claim：`declared-scope-only`。

`completion-recheck` 只发 durable signal，不创造 evidence、不直接完成 workflow。

历史 migration 不 retroactively 把旧 `approval_ref` 升级成 Authorization、把旧 succeeded effect 升级成 ConfirmedOutcome、把旧 resolution 升级成 verified repair。

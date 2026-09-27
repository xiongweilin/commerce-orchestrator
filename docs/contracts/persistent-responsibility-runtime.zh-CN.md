# Commerce persistent-responsibility runtime boundary

[English](persistent-responsibility-runtime.md) | [简体中文](persistent-responsibility-runtime.zh-CN.md)

Commerce 按 `docs/contracts/responsibility-compatibility.toml` 声明的精确 revision 消费 `agent-kernel`。

## 所有权

Commerce PostgreSQL 继续是 catalog/listing fact、publication qualification、Decision、ExecutionAuthorization、EffectLedger、reconciliation 和 verified outcome 的权威 owner。DBOS 继续是 durable workflow execution substrate。

Portable SQLite store 只由 worker 持有 portable responsibility coordination state：StandingResponsibility identity/history、assessment、proposal、priority/portfolio decision、reservation、commitment、reasoning/session continuity 和 materialized portable Work。

两个 store **不是一个 atomic transaction domain**。Portable assessment/commitment 不能生成 Commerce effect authority。在任何 external mutation 前，Commerce 必须重新读取 current domain facts，并重新验证现有 version/scope-bound Decision/ExecutionAuthorization。两边不一致时在 effect boundary fail closed，不能把 portable journal 当 Commerce truth。

## Durable worker wiring

`app.responsibility.runtime.get_responsibility_kernel()` 使用 `COMMERCE_RESPONSIBILITY_STATE_PATH` 构建 `ResponsibilityKernel(SQLiteStateStore(...))`。Worker 启动恢复该 store，并幂等 admit listing-integrity StandingResponsibility。Compose 使用 named volume 持久化同一 responsibility identity/history。

## Closure evidence

`tests/test_c20_listing_integrity_steward.py` 包含 SQLite restart-boundary test：多个 kernel 在同一 persisted file 上关闭/重建后，responsibility/version/status/assessment 保持；proposal/reservation/commitment/Work 能继续且不产生重复 Work 或 AuthorizationGrant；terminal Work completion 仍经过 portable CompletionAuthority proof gate；bounded Work 完成后 standing responsibility 仍保持 ACTIVE。

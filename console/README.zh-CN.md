# Operations Console

[English](README.md) | [简体中文](README.zh-CN.md)

Next.js 16 + React 19 + TypeScript 的 Commerce Orchestrator 运维控制台。它只是 FastAPI 后端之上的内部 inspection/request surface，刻意保持**非权威**。

它可以请求 Decision、展示 responsibility/authority fact，但不能生成或推断 execution authority。

## Runtime 与安全模型

- App Router / TypeScript strict mode。
- Same-origin BFF secure session。
- JWT 经 backend `/v1/me` 验证后只保存于 HttpOnly `commerce_session` cookie。
- 非 GET BFF request 需要 CSRF token + Origin validation。
- Client component 只调用同源 BFF，不接收 JWT。
- Server component 可以用 server-side session 调用 `COMMERCE_API_BASE`。
- 禁止把 JWT 放进 `localStorage`。

## 快速开始

```bash
npm install
npm run dev
npm run build
npm run gen:types
```

Generated API type 来自 FastAPI OpenAPI，写入 `lib/generated/openapi.ts`，不得手工修改。

## 当前页面

控制台覆盖 overview/runtime health、workflow/detail、approval inbox、reconciliation、command entry、system-admin failed inbox，以及 Responsibility Inspector。

Backend 始终是 status、eligibility、authority decision 的事实源。

## Approval 行为

`DecisionForm` 根据 server-projected `authorizationProfile` 区分普通 Decision 与需要 execution authority 的 approval。

需要 profile 时调用：

```text
POST /v1/work-items/{work_item_id}/authorized-decisions
```

否则调用：

```text
POST /v1/work-items/{work_item_id}/decisions
```

Reject 始终是普通 Decision。请求带 fresh `Idempotency-Key` 和 `expectedWorkflowVersion`。

如果页面没有显式 profile，console 从 pending work-item projection 解析；解析失败时暂停 approval，不能猜测“不需要 authorization”。

Console 不得从 `allowedOperations`、scope field、projection ref、portable status、historical Experience 或成功 effect 推断 authority。真正的 target、operation、subject binding、dispatch eligibility 都由 backend 重新验证。

## Responsibility Inspector

Inspector 消费：

```text
GET /v1/workflows/{workflow_id}/responsibility
```

UI 分开展示 CURRENT responsibility、HISTORICAL reliance、Decision、ExecutionAuthorization、Execution effect、Reality assessment、ConfirmedOutcome、Responsibility obligation 和 Open responsibility。

必须保持：

```text
Decision != Authorization
EffectLedger.succeeded != ConfirmedOutcome
workflow completed != universal responsibility discharge
```

Discharged obligation 继续保留在历史记录中。

## Health 与目录

Backend probe：`/livez`、`/healthz`、`/readyz`。`/readyz` 包含 database、Alembic head、adapter config、worker heartbeat。

Console 目录按 App Router、component、lib/session/auth/generated type 和 script 分层。Build-time data page 是 dynamic，不应要求 backend 在线；runtime backend failure 应显示为 operational error，不能让 console 成为 authority source 或静默假设成功。

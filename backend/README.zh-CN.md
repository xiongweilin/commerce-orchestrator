# commerce-orchestrator backend

[English](README.md) | [简体中文](README.zh-CN.md)

FastAPI + DBOS 后端，用于 Commerce Orchestrator 沙箱。后端负责命令受理、workflow/read API、durable decision messaging、typed external effects、canonical reconciliation，以及 Commerce 的 responsibility/authority specialization。

Odoo 19 继续作为其所属业务/会计事实的权威账本；Shopify 是第一个外部渠道。Commerce PostgreSQL 持有编排、effect、reconciliation、responsibility 和 audit 事实。

## 技术栈

- Python 3.12
- FastAPI / Pydantic v2 / pydantic-settings
- SQLAlchemy 2 + Alembic
- PostgreSQL / psycopg3
- DBOS 2.x durable workflow
- structlog / prometheus-client / OpenTelemetry
- PyJWT / cryptography / HMAC-SHA256

依赖版本以 `uv.lock` 为准，不要从 prose 复制版本号。

## Runtime 拆分

API process 负责 command/webhook ingress、JWT + DB-backed RBAC、read API、semantic/responsibility record request、health/readiness/metrics。

Worker process 负责 inbox relay、DBOS v2 workflow、durable decision/recheck delivery、typed effect dispatch、Shopify/Odoo adapter、canonical reconciliation、privacy cleanup、heartbeat 和 metrics。

API 不直接执行下游 Shopify/Odoo 副作用。

## 应用结构

```text
backend/app/
  api/v1/
  config.py
  connectors/
  core/
  models/
  responsibility/
  schemas/
  services/
  workflows/
  worker.py
```

Responsibility implementation 分布在 `models/responsibility.py`、`models/effect_realization.py`、`models/publication_qualification.py`、`models/reconciliation_resolution.py`，以及对应 service/API 文件。

## Decision 与 execution authority

`WorkItemDecision` 本身不授权外部执行。

普通 Decision：

```text
POST /v1/work-items/{id}/decisions
```

需要 execution profile 的 approval：

```text
POST /v1/work-items/{id}/authorized-decisions
```

当前 server-owned profile：

| Profile | Target | Operations |
| --- | --- | --- |
| `listing-publication-v1` | Shopify | `shopify.product_publish` |
| `return-credit-note-v1` | Odoo | `odoo.credit_note_create`, `odoo.credit_note_validate` |
| `return-refund-v1` | Shopify | `shopify.refund_create` |

需要 profile 的 work item 不能通过普通 Decision endpoint 绕过 authorization；authorized endpoint 在一个 transaction 中记录 Decision，并签发独立、精确 scope 的 `ExecutionAuthorization`。

## Responsibility Inspector

```text
GET /v1/workflows/{workflow_id}/responsibility
```

该 response 不携带 authority，展示 current responsibility、historical Experience reliance、Decision、ExecutionAuthorization、effect execution、reality assessment、ConfirmedOutcome，以及全部/open responsibility obligation。

Listing publication 的 explanation path 与 dispatch eligibility 共享，避免 UI 解释与实际执行 policy 分叉。

## Reality verification 与 bounded completion

`EffectLedger.succeeded` 只表示 adapter 报告执行成功，不等于 `ConfirmedOutcome`。ConfirmedOutcome 需要同一 effect 的 `VERIFIED` `EffectRealizationAssessment` 与证据。

Workflow completion 永久使用：

```text
coverage_claim = declared-scope-only
```

Blocked completion 通过 `POST /v1/workflows/{workflow_id}/completion-recheck` 发出 durable recheck signal；该 endpoint 不创造证据，也不直接把 workflow 标记 completed。

## Migration、环境和验证

Schema 使用 Alembic 全链路，始终运行 `uv run alembic upgrade head`，不要根据 prose 中的固定 table/migration 数量判断当前 schema。

配置全部使用 `COMMERCE_` 前缀；变量名以根 `.env.example` 和 `app/config.py` 为准。真实 secret 不得提交。

本地：

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
uv run python -m app.worker
```

检查：

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

关键 semantic test 覆盖 durable orchestration、idempotent decision delivery、authorization boundary、current-fact/exact-scope check、`outcome_unknown` 不自动 retry、execution/realization/outcome 分离、canonical reconciliation、CURRENT/HISTORICAL responsibility，以及 declared-scope completion/recheck。

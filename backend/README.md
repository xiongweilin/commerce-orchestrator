# commerce-orchestrator backend

FastAPI + DBOS backend for the Commerce Orchestrator sandbox. The backend owns command acceptance, workflow/read APIs, durable decision messaging, typed external effects, canonical reconciliation and the Commerce responsibility/authority specialization.

Odoo 19 remains the authoritative business/accounting ledger for Odoo-owned facts; Shopify is the first external channel. Commerce PostgreSQL owns orchestration, effect, reconciliation, responsibility and audit facts.

## Tech stack

- Python 3.12
- FastAPI / Pydantic v2 / pydantic-settings
- SQLAlchemy 2 + Alembic
- PostgreSQL / psycopg3
- DBOS 2.x for durable workflow execution
- structlog / prometheus-client / OpenTelemetry
- PyJWT / cryptography / HMAC-SHA256

Dependency versions are pinned by `uv.lock`; do not copy version numbers from prose when the lockfile can answer the question directly.

## Runtime split

The backend image has two process roles:

```text
API process
  uvicorn app.main:app
  - command/webhook ingress
  - JWT + database-backed RBAC
  - read APIs
  - semantic/responsibility record requests
  - health/readiness/metrics

worker process
  python -m app.worker
  - inbox relay
  - DBOS v2 workflows
  - durable decision/recheck delivery
  - typed effect dispatch
  - Shopify/Odoo adapters
  - canonical reconciliation
  - privacy cleanup / heartbeat / metrics
```

The API does not perform downstream Shopify/Odoo side effects directly.

## Current application structure

```text
backend/app/
  api/v1/                  HTTP routes
  config.py                COMMERCE_* settings
  connectors/              Shopify / Odoo / Dify / projection integrations
  core/                    db / errors / security / logging / telemetry / time
  models/                  SQLAlchemy models
  responsibility/          portable-runtime persistence seam
  schemas/                 Pydantic + shared vocabularies
  services/                domain/application services
  workflows/               DBOS workflow definitions and execution wrappers
  worker.py                 worker process
```

Responsibility-related implementation currently spans:

```text
models/responsibility.py
models/effect_realization.py
models/publication_qualification.py
models/reconciliation_resolution.py
services/responsibility.py
services/responsibility_execution.py
services/responsibility_profiles.py
services/current_use_eligibility.py
services/publication_qualification.py
services/realization_resolution.py
services/workflow_completion.py
api/v1/semantic_records.py
api/v1/publication_qualifications.py
api/v1/decisions.py
```

## Decision and execution authority

A `WorkItemDecision` does not by itself authorize external execution.

Ordinary decisions:

```text
POST /v1/work-items/{id}/decisions
```

Approval that requires an execution profile:

```text
POST /v1/work-items/{id}/authorized-decisions
```

Current server-owned profiles:

| Profile | Target | Operations |
| --- | --- | --- |
| `listing-publication-v1` | Shopify | `shopify.product_publish` |
| `return-credit-note-v1` | Odoo | `odoo.credit_note_create`, `odoo.credit_note_validate` |
| `return-refund-v1` | Shopify | `shopify.refund_create` |

For a work item that resolves to one of these profiles, the ordinary decision endpoint rejects `approve`. The authorized endpoint records the durable Decision and mints the distinct exact-scope `ExecutionAuthorization` in one transaction.

## Responsibility Inspector

```text
GET /v1/workflows/{workflow_id}/responsibility
```

The response is non-authority-bearing and exposes:

- current responsibility explanation;
- historical Experience reliance;
- decisions;
- execution authorizations;
- effect execution facts;
- reality assessments;
- confirmed outcomes;
- all responsibility obligations and the currently open subset.

For listing publication, the current explanation function is shared with the dispatch eligibility composition. This is intentional: presentation and actual dispatch should not maintain separate policy implementations.

## Reality verification and bounded completion

`EffectLedger.succeeded` means the adapter reported execution success. It is not a `ConfirmedOutcome`.

A ConfirmedOutcome requires a `VERIFIED` `EffectRealizationAssessment` with evidence for the same effect.

`services/workflow_completion.py` evaluates per-workflow finite completion requirements and permanently uses:

```text
coverage_claim = declared-scope-only
```

A blocked completion can be re-evaluated through:

```text
POST /v1/workflows/{workflow_id}/completion-recheck
```

The endpoint emits a durable recheck signal; it does not create evidence or directly set the workflow to completed.

## Database migrations

The current schema is not represented by `0001_initial.py` alone. Alembic currently contains migrations through:

```text
0010_responsibility_workflow_refs.py
```

Recent migrations add publication qualification, realization/reconciliation-resolution records and the responsibility plane. Always use:

```bash
uv run alembic upgrade head
```

and never rely on a hard-coded prose table count.

## Environment variables

All application settings use the `COMMERCE_` prefix. The complete example is the repository root `.env.example`; `app/config.py` is the code source.

Important groups:

- databases: `COMMERCE_DATABASE_URL`, `COMMERCE_DBOS_SYSTEM_DATABASE_URL`, optional API/worker role URLs;
- auth/privacy: `COMMERCE_JWT_SECRET`, `COMMERCE_ENCRYPTION_KEY`, `COMMERCE_PII_HASH_KEY`;
- relay/retry: inbox poll/batch/lease/max attempts, effect max retries;
- Shopify: shop/API version/access token or client credentials/webhook secret;
- Odoo: base URL/API key/database/username;
- Dify: base URL/workflow id/API key;
- observability: OTLP endpoint, worker metrics/heartbeat settings;
- sandbox safety: `COMMERCE_ALLOW_DEV_REFUND` is fail-closed by default.

Do not commit real secrets.

## Local development

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Worker in another terminal:

```bash
cd backend
uv run python -m app.worker
```

## Tests / checks

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Important current semantic tests include:

- command/inbox/DBOS durable orchestration;
- idempotent decision delivery;
- authorization-required approval cannot bypass `authorized-decisions`;
- exact-scope/current-fact authorization checks;
- `outcome_unknown` does not automatically retry;
- effect execution vs realization vs ConfirmedOutcome separation;
- canonical reconciliation fail-closed behavior;
- responsibility CURRENT/HISTORICAL separation;
- discharged obligations remain historical;
- declared-scope completion and durable recheck.

## Simulation scripts

The existing scripts remain sandbox/demo drivers for order-to-cash, return/refund, feedback-to-catalog and procurement flows. `--real-llm` in the feedback simulation can use Dify for candidate generation; AI still only generates proposals and never approves or executes effects.

## Documentation

- `../docs/current-implementation.md`
- `../docs/architecture.md`
- `../docs/architecture/responsibility-layering.md`
- `../docs/contracts/api-contract.md`
- `../docs/contracts/responsibility-alignment.md`

# commerce-orchestrator backend

The "operations control tower" backend for e-commerce: cross-system workflows, approvals, RBAC, idempotency, the effect ledger, and reconciliation, with **Odoo 19** as the authoritative ledger, **Shopify development store** as the first channel, and **Metabase** for read-only analytics.

## Tech stack

- Python 3.12.13, virtual environment managed by uv 0.12.3 (`backend/.venv`)
- FastAPI + SQLAlchemy 2.0 (sync style) + Alembic migrations
- DBOS 2.29 (only in `app/workflows`, see below)
- pydantic v2 + pydantic-settings (config, env-var prefix `COMMERCE_`)
- PostgreSQL (psycopg3); model types also compatible with SQLite (unit tests)
- structlog (structured logging), prometheus-client, OpenTelemetry (optional OTLP export)
- pyjwt (JWT) + cryptography (Fernet-encrypted raw payloads) + HMAC-SHA256 (Shopify webhook verification)

## Directory structure

```
backend/
  app/
    config.py        # pydantic-settings config (COMMERCE_ prefix, get_settings() cached)
    core/            # infrastructure: db / uuid7 / time / security / errors / logging / telemetry
    models/          # SQLAlchemy declarative models (23 tables, one module per domain)
    schemas/         # pydantic v2: base types, command models, event/role/effect operation vocabulary
    api/             # HTTP route layer
    services/        # business logic
    workflows/       # DBOS workflows (cross-system orchestration, approvals)
    connectors/      # external system connectors (Shopify / Odoo / Metabase)
  alembic/           # migration scripts (0001_initial creates all tables)
  tests/             # unit/integration tests
```

## Environment variables

All use the `COMMERCE_` prefix (see `app/config.py`), configurable in the environment or `backend/.env`.

| Variable | Default | Notes |
| --- | --- | --- |
| `COMMERCE_DATABASE_URL` | `postgresql+psycopg://commerce:commerce@localhost:5432/commerce` | application main database |
| `COMMERCE_DBOS_SYSTEM_DATABASE_URL` | `postgresql+psycopg://commerce:commerce@localhost:5432/dbos` | DBOS system database |
| `COMMERCE_JWT_SECRET` | none (required) | JWT signing key |
| `COMMERCE_JWT_EXPIRES_MINUTES` | `480` | JWT validity (minutes) |
| `COMMERCE_ENCRYPTION_KEY` | none (required) | Fernet key for encrypting raw payloads |
| `COMMERCE_ENVIRONMENT` | `dev` | runtime environment (`dev` logs to console, others emit JSON) |
| `COMMERCE_LOG_LEVEL` | `INFO` | log level |
| `COMMERCE_SHOPIFY_API_VERSION` | `2026-07` | Shopify API version |
| `COMMERCE_SHOPIFY_SHOP_NAME` | empty | Shopify shop name |
| `COMMERCE_SHOPIFY_ACCESS_TOKEN` | empty | Shopify access token |
| `COMMERCE_SHOPIFY_WEBHOOK_SECRET` | empty | Shopify webhook HMAC key |
| `COMMERCE_ODOO_BASE_URL` | empty | Odoo 19 base URL |
| `COMMERCE_ODOO_API_KEY` | empty | Odoo API key |
| `COMMERCE_ODOO_DB` | empty | Odoo database name |
| `COMMERCE_ODOO_USERNAME` | empty | Odoo username |
| `COMMERCE_DIFY_BASE_URL` | `http://127.0.0.1:18080` | Dify service base URL (local deployment) |
| `COMMERCE_DIFY_WORKFLOW_ID` | empty | published Dify workflow id (P6 candidate generation) |
| `COMMERCE_DIFY_API_KEY` | empty | Dify app API key (sensitive) |
| `COMMERCE_OTLP_ENDPOINT` | empty | OTLP tracing endpoint; empty makes telemetry a no-op |
| `COMMERCE_RAW_PAYLOAD_RETENTION_DAYS` | `30` | encrypted raw-payload retention days |

## Local run

```bash
cd backend
uv sync                     # install dependencies (do not hand-edit pyproject.toml / uv.lock)
uv run alembic upgrade head # apply migrations (reads COMMERCE_DATABASE_URL)
uv run uvicorn app.main:app --reload
```

## Tests

```bash
cd backend
uv run pytest
```

## Simulation scripts (no real customers, real systems)

Located in `scripts/`, each loads the repository `.env` first and then imports the app; run directly with `uv run python scripts/<script>`:

- `run_test_order_flow.py [shopify_order_id]`: feeds a Shopify order into the O2C workflow and runs it to completion — received → … → closed (13 steps + 4 human gates + effect accounting + reconciliation); passing an order id makes it idempotent.
- `simulate_return_refund.py [shopify_order_id]`: full-order return/refund loop — builds a test order (with payment transaction), 5 human approval gates (four-eyes), Odoo credit-note posting, Shopify refundCreate (manual when a parent transaction exists, otherwise cash), effect ledger + three-way reconciliation; with no arguments it creates a new test order each run.
- `simulate_feedback_to_catalog.py`: Feedback → clustering → AI candidate (draft→candidate→frozen→scored→official, AI suggests only, never approves) → catalog revision approval → listing effect planned; by default uses the local simulated candidate with `model_id="simulated-v1"`, and skips when a candidate from the same model is detected (`--force` reruns). With `--real-llm` and `COMMERCE_DIFY_WORKFLOW_ID` / `COMMERCE_DIFY_API_KEY` configured, the Dify workflow (`DifyConnector` in `backend/app/connectors/dify.py`) generates the candidate's `proposal_json` from redacted feedback instead (`model_id` recorded as `dify:<workflow_id>`; AI suggests only, never approves).
- `simulate_procurement.py`: procurement loop — requirement → RFQ → approval (budget_owner four-eyes) → receiving (warehouse_staff) → billing (accountant ×2 four-eyes) → closed; the worker segment really executes Odoo PO creation/confirmation, receiving (`button_validate`), and bill creation + posting; effect ledger across the whole chain, reconciliation with zero differences.

Notes: Shopify orders created through the API/backend do not push webhooks (Shopify limitation, only the checkout flow triggers them), so the scripts drive the local workflow directly; reconciliation differences always enter `MANUAL_RECONCILIATION`, never auto-flattened.

## Code standards

```bash
cd backend
uv run ruff check app
uv run ruff format --check app
```

Rules are in the `[tool.ruff]` section of `backend/pyproject.toml`.

## Relationship with DBOS

- `app/workflows` uses DBOS to orchestrate cross-system workflows (state persistence, retries, recovery), and persists intermediate state through `app/core`'s session, effect-ledger, and message tables.
- `app/core`, `app/models`, `app/schemas` **do not depend on DBOS** and can run and be tested independently; `app/main.py` assembles FastAPI and the DBOS runtime only within this package.

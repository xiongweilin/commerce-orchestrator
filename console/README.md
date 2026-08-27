# Operations Console

Next.js 16 + React 19 + TypeScript operations console for Commerce Orchestrator. The console is an internal inspection/request surface over the FastAPI backend; it is deliberately **non-authoritative**.

It can request decisions and display responsibility/authority facts, but it does not mint or infer execution authority.

## Runtime and security model

- App Router / TypeScript strict mode.
- Same-origin BFF secure session.
- JWT is stored only in an HttpOnly `commerce_session` cookie after backend `/v1/me` verification.
- Non-GET BFF requests require CSRF token + Origin validation.
- Client components call the same-origin BFF; they do not receive the JWT.
- Server components may call `COMMERCE_API_BASE` with the server-side session.
- No JWT in `localStorage`.

## Quick start

```bash
npm install
npm run dev
npm run build
npm run gen:types
```

Requirements: Node.js >= 20.9; Node 24 / npm 11 are recommended for this sandbox.

Generated API types come from backend FastAPI OpenAPI and are written to `lib/generated/openapi.ts`. Do not hand-edit that file.

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `COMMERCE_API_BASE` | `http://localhost:8000` | server-private FastAPI base URL |
| `COMMERCE_CONSOLE_ORIGIN` | request origin | non-GET Origin validation |
| `COMMERCE_SESSION_MOCK` | unset | non-production development-only session verification bypass |
| `OPENAPI_URL` | `<COMMERCE_API_BASE>/openapi.json` | type generation source |

## Current pages

The console currently includes operations views for:

- overview/runtime health;
- workflows and workflow detail;
- approval inbox;
- reconciliation runs and differences;
- command entry;
- failed inbox operations for `system_admin`;
- Responsibility Inspector for workflow responsibility chains.

The backend remains the source of status/eligibility/authority decisions.

## Approval behavior

`DecisionForm` distinguishes ordinary decisions from approvals that require execution authority.

For a pending work item the console consumes the server-projected:

```text
authorizationProfile
```

Current values are:

```text
listing-publication-v1
return-credit-note-v1
return-refund-v1
```

If approve requires a profile, the console calls:

```text
POST /v1/work-items/{work_item_id}/authorized-decisions
```

Otherwise it calls:

```text
POST /v1/work-items/{work_item_id}/decisions
```

Reject remains an ordinary Decision.

The console sends a fresh `Idempotency-Key` for decision requests and includes `expectedWorkflowVersion`.

### Fail-closed profile resolution

If the page did not already receive an explicit profile, `DecisionForm` resolves the profile from the pending work-item projection. If that lookup fails, approval is paused rather than guessing that no authorization is required.

### What the console does not do

The console must not infer authority from:

- `allowedOperations`;
- `scope.channel` or other scope values;
- projection refs;
- portable status;
- historical Experience use;
- a successful effect status.

It only selects the appropriate request path from server-projected profile metadata. Actual target system, allowed operations, subject bindings and dispatch eligibility are revalidated by the backend.

## Responsibility Inspector

The workflow Responsibility Inspector consumes:

```text
GET /v1/workflows/{workflow_id}/responsibility
```

The server response is explicitly non-authority-bearing and exposes separate sections for:

```text
CURRENT responsibility
HISTORICAL reliance
Decisions
ExecutionAuthorizations
Execution effects
Reality assessments
ConfirmedOutcomes
Responsibility obligations
Open responsibility
```

The UI preserves the distinction between CURRENT and HISTORICAL:

- historical records describe what was relied on at the time;
- current responsibility is a fresh server evaluation;
- historical existence never becomes current eligibility through client logic.

The UI also keeps:

```text
Decision != Authorization
EffectLedger.succeeded != ConfirmedOutcome
workflow completed != universal responsibility discharge
```

as presentation constraints.

Discharged responsibility obligations remain visible in historical responsibility data, while `openResponsibility` contains only current open obligations.

## Health / ops

Overview health data comes from backend probes and `GET /v1/ops/runtime` where the current session has the required role.

Backend probes:

```text
/livez
/healthz
/readyz
```

`/readyz` includes database, Alembic head, adapter configuration and worker heartbeat checks.

## Directory structure

```text
console/
├── app/                     App Router pages and BFF routes
│   ├── api/                 session / me / backend proxy
│   ├── workflows/           workflow views + Responsibility Inspector
│   ├── reconciliations/     reconciliation views
│   └── ops/                 system-admin operations
├── components/              forms/status/error/health/responsibility UI
├── lib/
│   ├── api.ts               API wrapper + Idempotency-Key helpers
│   ├── generated/openapi.ts generated FastAPI schema types
│   ├── session*.ts          BFF session helpers
│   ├── server-auth.ts       server-side auth access
│   └── types.ts             console-specific projections
├── scripts/gen-types.mjs
├── package.json
└── next.config.ts
```

## Build-time behavior

Data pages are dynamic and should not require a live backend during `npm run build`. Runtime backend failures are rendered as operational errors rather than converting the console into an authority source or silently assuming success.

## Documentation

- `../docs/current-implementation.md`
- `../docs/contracts/api-contract.md`
- `../docs/contracts/responsibility-alignment.md`
- `../docs/architecture/responsibility-layering.md`

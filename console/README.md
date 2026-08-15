# Operations Console

The **internal operations console** of the e-commerce control tower (Next.js 16 + TypeScript, App Router). Used to view workflows, the approval inbox, reconciliation differences, failed-inbox operations, and to issue operations commands. The backend is FastAPI (default `http://localhost:8000`, API v1), accessed through a same-origin **BFF secure session** — no direct backend connection, no JWT in localStorage.

## Tech stack

- Next.js 16.x (`output: "standalone"`, App Router, server components + client forms)
- React 19.x, TypeScript (strict)
- Zero extra runtime dependencies: no Tailwind, no UI library; hand-written CSS (`app/globals.css`, light theme + dark header, Chinese UI)

## Quick start

Requirements: Node.js >= 20.9 (24 recommended), npm 11.

```bash
npm install
npm run dev        # dev mode http://localhost:3000
npm run build      # production build (outputs .next/standalone, used by the Dockerfile)
npm start          # production start http://localhost:3000
npm run gen:types  # generate TypeScript types from the backend OpenAPI (lib/generated/openapi.ts)
```

> Windows note: ports 3001–3100 are in the system reserved range on this machine (`netsh interface ipv4 show excludedportrange protocol=tcp`), and binding fails with `EACCES`. Use a port outside the reserved range for development, e.g. `npm run dev -- -p 3200` or `npm start -- -H 127.0.0.1 -p 3200`.

## Environment variables

| Variable | Default | Notes |
| --- | --- | --- |
| `COMMERCE_API_BASE` | `http://localhost:8000` | backend FastAPI address (**server-private**, used only by BFF/server components; the client only reaches the same-origin BFF) |
| `COMMERCE_CONSOLE_ORIGIN` | request's own origin | allowed console origin (Origin check for BFF non-GET requests; read first, falls back to `CONSOLE_ORIGIN`; when unset, the request's own origin) |
| `COMMERCE_SESSION_MOCK` | unset | dev mode only (non-production): `1` makes POST /api/session skip the backend `/v1/me` verification |
| `OPENAPI_URL` | `<COMMERCE_API_BASE>/openapi.json` | full URL `npm run gen:types` pulls the OpenAPI from |

## Authentication

Uses the **Next.js BFF secure session** (rework plan §4.3):

- Enter a JWT once in the top-right; `POST /api/session` has the BFF verify it against the backend `/v1/me`, then writes the JWT to an **HttpOnly cookie** (`commerce_session`, `SameSite=Strict`, `Path=/`, `Secure` forced outside dev, Max-Age no longer than the JWT's remaining TTL and ≤ 8 hours), and generates a non-sensitive `commerce_csrf` cookie.
- Client components always reach the same-origin BFF `/api/backend[...]`, never touching the JWT; non-GET requests carry `X-CSRF-Token` (matching the `commerce_csrf` cookie) + an Origin check.
- Server components read the HttpOnly session via `lib/server-auth.ts` and connect to `COMMERCE_API_BASE` directly.
- `DELETE /api/session` logs out and clears both cookies.

> The backend `/v1/me` is implemented; in dev mode `COMMERCE_SESSION_MOCK=1` can temporarily skip backend verification (effective only when `NODE_ENV !== "production"`).

## Pages

| Route | Page | Main API |
| --- | --- | --- |
| `/` | Overview: workflow-count cards, quick entries, system status | `GET /v1/workflows?limit=1` (reads total) |
| `/workflows` | Workflow list: status filter, refresh, pagination; rows link to details | `GET /v1/workflows?status=&limit=&offset=` |
| `/workflows/[id]` | Workflow details: status header, current step, version, event timeline, effect ledger, work-item decision form | `GET /v1/workflows/{id}`、`POST /v1/work-items/{id}/decisions` |
| `/approvals` | Approval inbox: pending work-item cards + inline approve/reject forms | `GET /v1/work-items?status=pending` |
| `/reconciliations` | Reconciliation: start a run (with Idempotency-Key) + run list | `POST/GET /v1/reconciliations` |
| `/reconciliations/[runId]` | Reconciliation details: differences table (MANUAL_RECONCILIATION highlighted) + resolution-note form | `GET /v1/reconciliations/{runId}`、`POST /v1/reconciliations/{runId}/diffs/{diffId}/resolve` |
| `/ops/inbox` | Ops inbox: failed-inbox view + retry (navigation visible only to system_admin) | `GET /v1/ops/inbox?status=failed`、`POST /v1/ops/inbox/{id}/retry` |
| `/commands` | Command entry: type selection + JSON payload + a fresh Idempotency-Key each time, shows the 202 result | `POST /v1/catalog-revisions`、`/v1/listing-publications`、`/v1/procurements`、`/v1/returns`、`/v1/reconciliations` |

The overview page has four health cards (worker / inbox / effect / reconciliation), sourced from `GET /readyz` and `GET /v1/ops/runtime`.

## Directory structure

```text
console/
├── app/                     # App Router pages (layout / overview / workflows / approvals / reconciliations / commands / ops)
│   ├── api/                 # BFF routes: session / me / backend proxy (CSRF + Origin check)
│   ├── globals.css          # global styles (hand-written CSS)
│   ├── workflows/[id]/     # workflow details
│   ├── reconciliations/[runId]/  # reconciliation details
│   └── ops/inbox/           # failed-inbox view + retry (system_admin)
├── components/              # StatusBadge / ErrorBox / Loading / forms / refresh button / session management / health cards
├── lib/
│   ├── api.ts               # fetch wrapper: server connects to COMMERCE_API_BASE / client goes through the same-origin BFF + CSRF
│   ├── types.ts             # API v1 contract types (read models per api-contract.md; command types from generated)
│   ├── generated/openapi.ts # generated from OpenAPI by scripts/gen-types.mjs (never hand-edit)
│   ├── session.ts           # session cookie constants (client/server shared)
│   ├── session-server.ts    # session cookie read/write / CSRF / Max-Age computation (server)
│   ├── format.ts            # time / short-id / JSON display helpers
│   └── server-auth.ts       # server reads JWT / current user from the HttpOnly session (fail-closed)
├── scripts/gen-types.mjs    # OpenAPI -> TypeScript type generation (deterministic output)
├── public/favicon.svg
├── package.json
├── package-lock.json        # generated by npm install
├── tsconfig.json
├── next.config.ts           # output: "standalone"
└── next-env.d.ts
```

## Notes (assumptions made while implementing)

- Workflow/work-item/reconciliation status values are authoritative on the backend; unknown statuses show the raw string (neutral-gray badge). `MANUAL_RECONCILIATION` shows as a purple highlighted badge "人工对账".
- The reconciliation `summary` difference-count field name is not fixed; compatible with `diffCount / diff_count / unmatched / mismatchCount / unresolved` naming, showing "—" when unavailable.
- The workflow-list status filter set is the seven states of plan §2.1: `accepted / running / awaiting_approval / completed / needs_reconciliation / failed / cancelled`.
- The reconciliation-difference resolve endpoint may not be wired yet: when the backend returns 404/405/501 or the corresponding error code, the page shows an "interface not ready" message instead of crashing.
- The workflow event contract field is `type` (no longer `eventType`); approval decisions submit `expectedWorkflowVersion` (compatibly reads legacy `expectedVersion`).
- The backend `/v1/me`, `/readyz`, `/livez`, `/v1/ops/*` are all implemented; when the backend is unreachable, the corresponding cards show "unknown".
- All data pages render dynamically (`force-dynamic`), never requesting the backend at build time; when the backend is unreachable, pages show an error message instead of failing the build.

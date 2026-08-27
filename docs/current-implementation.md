# Current implementation snapshot

This document is the implementation-oriented snapshot of `commerce-orchestrator` at `main` commit `3187a1c67677cf538e19d8db5a842f559cc675ac` (2026-08-27). It is intended to keep repository documentation aligned with the code that actually exists. Architecture decisions remain in `docs/adr/`; normative API/event/data-ownership contracts remain in `docs/contracts/`.

## Project boundary

Commerce Orchestrator is a personal full-stack sandbox for durable, governed cross-system commerce workflows. It runs with simulated data, a Shopify development store and an Odoo 19 sandbox. It has no production-promotion path, no real users and no real orders.

The system is not an autonomous commerce agent. AI is currently limited to candidate generation. AI does not approve, mint execution authority, dispatch external effects, or rewrite authoritative business facts.

The implementation is best understood as a durable execution and responsibility control plane around commerce workflows.

## Runtime topology

```text
Next.js console
    |
    | same-origin BFF session / CSRF / Origin checks
    v
FastAPI API process
    |
    | workflow.accepted / workflow.decision_recorded /
    | workflow.completion_recheck_requested via PostgreSQL inbox relay
    v
DBOS worker process
    |
    +--> typed effect seam --> Shopify Admin GraphQL 2026-07
    |
    +--> typed effect seam --> Odoo 19 JSON-2
    |
    +--> canonical reconciliation
    |
    +--> privacy cleanup / runtime heartbeat / metrics
    v
PostgreSQL
    +-- Commerce application state
    +-- responsibility / realization / qualification records
    +-- inbox / outbox / idempotency / effect ledger
    +-- DBOS system database
```

The API and worker share the same codebase but have separate process responsibilities. The API validates and records commands; the worker owns DBOS orchestration and external side effects.

## Durable workflow mainline

All new command and webhook flows use DBOS v2 and the single orchestration path:

```text
API/webhook acceptance
    -> workflow.accepted
    -> inbox relay
    -> DBOS workflow
    -> durable approval wait where required
    -> typed effect planning and dispatch
    -> external read-back / realization verification
    -> canonical reconciliation
    -> bounded completion assessment
```

Workflow states are:

```text
accepted
  -> running
  -> awaiting_approval
  -> running
  -> completed | needs_reconciliation | failed | cancelled
```

`needs_reconciliation` is intentionally non-terminal. `outcome_unknown` is never blindly re-sent.

## Decision and execution authority

A `WorkItemDecision` is not execution authority.

For ordinary work items, decisions are submitted through:

```text
POST /v1/work-items/{work_item_id}/decisions
```

For approval steps that require effect authority, approval must use:

```text
POST /v1/work-items/{work_item_id}/authorized-decisions
```

The server selects the authorization profile and mints a separate, exact-scope `ExecutionAuthorization` in the same transaction as the durable decision.

Current server-owned authorization profiles are:

| Profile | Target | Allowed operations |
| --- | --- | --- |
| `listing-publication-v1` | Shopify | `shopify.product_publish` |
| `return-credit-note-v1` | Odoo | `odoo.credit_note_create`, `odoo.credit_note_validate` |
| `return-refund-v1` | Shopify | `shopify.refund_create` |

An authorization is bound to the exact workflow, subject type/ref/version/fingerprint, target system, allowed operation set, policy version, environment and optional expiry. Dispatch revalidates current facts; role membership or a historical approval is not inferred as authority.

## Responsibility plane

The responsibility plane is additive to DBOS and the existing domain state machines. It does not replace workflow durability or re-own Shopify/Odoo facts.

The current chain is:

```text
commercial / operational evidence
    -> Experience-use evaluation
    -> HistoricalExperienceUse binding
    -> Decision
    -> ExecutionAuthorization
    -> effect execution
    -> EffectRealizationAssessment
    -> ConfirmedOutcome where required
    -> ResponsibilityObligation lifecycle
```

Important non-equivalences enforced by the implementation:

- candidate != Decision
- ExperienceUseAdmission.allowed != Decision
- Decision != ExecutionAuthorization
- publication qualification != Authorization
- EffectLedger.succeeded != ConfirmedOutcome
- reconciliation disposition != verified repair
- WorkflowRun.completed != universal responsibility discharge

### CURRENT vs HISTORICAL

`GET /v1/workflows/{workflow_id}/responsibility` returns the non-authoritative Responsibility Inspector projection.

For listing publication, `currentResponsibility` is re-evaluated from current authorization, current publication qualification, required experience state, portable status and applicable open obligations. The same server-side explanation path is used by publication dispatch eligibility, reducing explanation/enforcement drift.

`historical` records the knowledge/experience state that was actually relied on at the time of the earlier judgment. Historical reliance is immutable and does not become current eligibility merely because it existed in the past.

Responsibility obligations remain visible after discharge; discharged records leave `openResponsibility` but remain in `responsibilityObligations` for audit history.

## Effect execution and reality verification

Every external effect goes through the typed effect seam and effect ledger.

```text
planned -> dispatched -> succeeded | failed | outcome_unknown
                               |
                               v
                      realization assessment
                               |
                         VERIFIED evidence
                               |
                               v
                        ConfirmedOutcome
```

`EffectLedger.succeeded` means the adapter reported execution success. It does not by itself prove the business outcome. A `ConfirmedOutcome` requires an explicit `VERIFIED` realization assessment with evidence.

For unknown outcomes, the workflow transitions to reconciliation instead of automatically replaying the side effect.

## Canonical reconciliation

Canonical reconciliation covers six domains:

- `listing`
- `order`
- `procurement`
- `return`
- `catalog`
- `effect`

A missing required reader is failure, not success. A reported zero-diff result requires each required domain to have checked rows or an explicit proof of emptiness. Reconciliation differences are never auto-smoothed.

Manual resolution records a disposition; it does not itself prove that the real-world repair happened. Subsequent read-back and reconciliation are required.

## Bounded workflow completion

Completion is a finite declared-scope claim, not a statement of universal goal completion.

`backend/app/services/workflow_completion.py` defines per-workflow completion contracts. Depending on workflow type, completion may require:

- no pending work items;
- the expected domain terminal state;
- current publication qualification;
- required effect classes to exist;
- realization verification for run-owned effects;
- a `ConfirmedOutcome` where the profile requires it;
- no blocking reconciliation differences.

The permanent coverage claim is `declared-scope-only`.

A blocked completion can be explicitly re-evaluated through:

```text
POST /v1/workflows/{workflow_id}/completion-recheck
```

This endpoint does not invent evidence or directly change completion state. It emits a durable `workflow.completion_recheck_requested` signal that causes the waiting workflow to reassess current evidence.

## Append-only semantic and responsibility APIs

The backend currently exposes append-only or explicitly versioned endpoints for:

- publication qualification assessments;
- effect realization assessments;
- reconciliation resolution records;
- confirmed outcomes;
- responsibility obligations and discharge;
- knowledge projections;
- current Experience-use evaluation;
- HistoricalExperienceUse binding;
- workflow responsibility inspection.

These endpoints are implemented in `backend/app/api/v1/publication_qualifications.py` and `backend/app/api/v1/semantic_records.py`.

The console is an inspection/request consumer only. It must not infer authority from `allowedOperations`, projection references, scope fields or portable status. Authority is server-owned.

## Persistence and migrations

The current Alembic chain is no longer a single `0001` schema. It includes migrations through:

```text
0010_responsibility_workflow_refs.py
```

Recent migrations add publication qualification, effect realization/reconciliation-resolution records, the responsibility plane and workflow references for responsibility records.

## Current console behavior

The Next.js console uses a same-origin BFF secure session. JWTs are not stored in `localStorage`.

The console currently supports workflow/approval/reconciliation/ops views and the Responsibility Inspector. Approval forms resolve the server-projected `authorizationProfile`; approvals that require authority use `authorized-decisions`, while rejects and ordinary decisions use the ordinary decision endpoint.

The UI is deliberately non-authoritative: it presents server verdicts and reasons rather than minting or deriving execution authority.

## Source-of-truth map

| Concern | Primary source |
| --- | --- |
| Runtime code | `backend/app`, `console`, `services` |
| Current implementation snapshot | this document |
| API behavior | `docs/contracts/api-contract.md` + FastAPI OpenAPI |
| Event names/effect operations | `docs/contracts/event-contract.md` |
| Fact ownership | `docs/contracts/data-ownership.md` |
| Responsibility semantic mapping | `docs/contracts/responsibility-alignment.md` |
| Responsibility compatibility boundary | `docs/contracts/responsibility-compatibility.toml` |
| Layering | `docs/architecture.md`, `docs/architecture/responsibility-layering.md` |
| Historical design decisions | `docs/adr/` |
| Operator procedures | `docs/runbooks/` |

When prose and code disagree during an implementation synchronization pass, update the prose to reflect the implementation unless the mismatch is a deliberate contract violation that should instead be fixed in code.
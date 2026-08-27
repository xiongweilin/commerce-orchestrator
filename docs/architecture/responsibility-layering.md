# Responsibility Layering

This architecture adds a semantic/responsibility plane to Commerce Orchestrator without replacing DBOS, the existing Commerce domain state machines, or external fact ownership.

The current implementation is additive:

```text
Commerce business/domain facts
feedback / catalog / pricing / orders
               |
               v
portable Experience contracts
KnowledgeProjection / current admission / historical reliance
               |
               v
Commerce responsibility specialization
Decision / ExecutionAuthorization / obligation / qualification
               |
               v
DBOS durable workflow
               |
               v
Effect Ledger + typed adapters
               |
               v
external read-back / realization assessment / reconciliation
               |
               v
ConfirmedOutcome where required + bounded completion assessment
```

## Ownership

- `portable-runtime/contracts/` owns generic portable responsibility semantics.
- Commerce owns its API/event vocabulary and domain specializations.
- Commerce PostgreSQL owns Commerce orchestration and responsibility persistence.
- DBOS remains the only durable workflow engine.
- Odoo and Shopify retain the fact ownership defined in `docs/contracts/data-ownership.md`.
- The console is non-authoritative and cannot mint or infer execution authority.

## Dependency rule

Commerce production code may consume the portable public-contract/reference seam. It must not depend on portable governance dispatch, `InvocationPermit`, provider routing or other execution internals. The portable reference implementation may evaluate Experience Use and prepare HistoricalExperienceUse compare-and-bind; Commerce supplies persistence and transaction boundaries.

## Decision and authority separation

A `WorkItemDecision` is necessary for approval but is not an `ExecutionAuthorization`.

Work items whose server-owned profile requires effect authority must use `POST /v1/work-items/{id}/authorized-decisions`. In the same transaction, Commerce records the durable Decision and issues a separate exact-scope authorization. Dispatch revalidates the authorization against the current subject version/fingerprint, environment, policy and allowed operation.

Current profiles are:

| Profile | Target | Operations |
| --- | --- | --- |
| `listing-publication-v1` | Shopify | `shopify.product_publish` |
| `return-credit-note-v1` | Odoo | `odoo.credit_note_create`, `odoo.credit_note_validate` |
| `return-refund-v1` | Shopify | `shopify.refund_create` |

Role membership, a prior approval or a client-side object never substitutes for a current matching authorization.

## CURRENT vs HISTORICAL

The responsibility inspector separates current eligibility from historical reliance.

`historical` answers: what Experience/knowledge state was actually relied on when the earlier judgment was made?

`currentResponsibility` answers: with the current authorization, qualification, Experience-use state and open obligations, is this listing-publication responsibility currently eligible?

Historical records are immutable evidence. They do not automatically qualify the current state.

For listing publication, `explain_listing_current_responsibility()` is also consumed by publication dispatch eligibility. This intentionally shares the explanation and enforcement path instead of maintaining a second UI-only policy implementation.

## Execution, reality and outcome are different facts

```text
ExecutionAuthorization
        |
        v
EffectLedger execution fact
        |
        v
EffectRealizationAssessment
        |
        v
ConfirmedOutcome
```

`EffectLedger.succeeded` reports adapter/execution success. It is not a confirmed business outcome. A `ConfirmedOutcome` requires an explicit `VERIFIED` realization assessment with evidence.

Likewise, a reconciliation disposition is not proof that a repair actually happened; subsequent read-back/verification is required.

## Responsibility obligations

Responsibility obligations are explicit and scoped. They remain in history after discharge. `openResponsibility` is only the current open subset; `responsibilityObligations` preserves the full lifecycle.

An obligation does not globally invalidate every projection merely because a related reconciliation difference exists.

## Bounded completion

Workflow completion is a declared-scope claim only. `backend/app/services/workflow_completion.py` evaluates finite requirements for each supported workflow type, including domain terminal state, required effects, current qualification, effect realization, confirmed outcome where required, settled work items and blocking reconciliation differences.

The permanent coverage claim is `declared-scope-only`.

If a workflow is blocked on completion evidence, `POST /v1/workflows/{workflow_id}/completion-recheck` emits a durable `workflow.completion_recheck_requested` signal. The request does not invent evidence or directly mark the workflow complete; the waiting DBOS workflow reassesses current facts.

## Historical migration rule

Responsibility data introduced after this architecture is additive. Existing workflow/effect/reconciliation rows are not reinterpreted as if explicit responsibility objects had existed historically.

In particular:

- legacy `approval_ref` is not upgraded into `ExecutionAuthorization`;
- historical `succeeded` effects are not upgraded into `ConfirmedOutcome`;
- historical reconciliation resolutions are not upgraded into verified repair;
- HistoricalExperienceUse is not retroactively synthesized when the portable contract forbids backfill.

## Current implementation references

- `backend/app/services/responsibility.py`
- `backend/app/services/responsibility_execution.py`
- `backend/app/services/workflow_completion.py`
- `backend/app/api/v1/decisions.py`
- `backend/app/api/v1/semantic_records.py`
- `docs/contracts/responsibility-alignment.md`
- `docs/current-implementation.md`

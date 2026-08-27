# Commerce Responsibility Alignment Contract

Status: canonical Commerce specialization contract.

`portable-runtime/contracts/` owns the generic responsibility semantics consumed here. This document owns only Commerce-specific specialization and mapping. If a Commerce mapping conflicts with the required portable contract catalog, the portable contract wins for generic semantic meaning; Commerce domain facts and fact ownership remain governed by `data-ownership.md`.

## Frozen architecture boundary

```text
Commerce Domain / Intelligence
        |
        v
Experience Governance
        |
        v
Responsibility Runtime
        |
        v
DBOS durable execution + Effect Ledger
        |
        v
Reality verification + canonical reconciliation
        |
        v
Bounded completion
```

DBOS remains the durable workflow substrate. The responsibility layer does not replace DBOS, re-own Odoo/Shopify facts, or replace existing Commerce domain state machines.

## Mapping

| Commerce concept | Responsibility meaning | Non-equivalence |
| --- | --- | --- |
| AI candidate suggestion | Assertion / Derivation provenance | candidate != Decision |
| evaluation artifact | EvidenceArtifact | artifact existence != supported Assertion |
| feedback/external readback | Observation | Observation != proposition truth |
| reusable evaluated knowledge | KnowledgeProjection | projection != execution authority |
| portable Experience admission | ExperienceUseAdmission | allowed != approval/authorization |
| HistoricalExperienceUse + `ResponsibilityBinding` | immutable historical reliance | historical reliance != current eligibility |
| commercial judgment | Assertion with `task-domain-judgment` role | judgment != Decision |
| `WorkItemDecision` | Commerce specialization of Decision | Decision != Authorization |
| RBAC/four-eyes/compliance checks | policy/authority basis | passing policy != AuthorizationGrant |
| `ExecutionAuthorization` | exact-scope current execution authority | Authorization != Decision or role membership |
| `PublicationQualificationAssessment` | exact-context current publication qualification | qualified != Experience admission or Authorization |
| `EffectExecutionRequest` | effect/action intent | intent != execution fact |
| DBOS effect step | execution occurrence | DBOS success != confirmed business Outcome |
| EffectLedger `succeeded` | adapter/execution report | succeeded != ConfirmedOutcome |
| `EffectRealizationAssessment.verified` | reality verification evidence | verified != universal objective completion |
| `ConfirmedOutcome` | explicitly confirmed bounded business outcome | outcome != universal workflow responsibility discharge |
| `ReconciliationResolution` | recovery disposition + verification metadata | disposition != realized repair |
| `ResponsibilityObligation` | explicit scoped open/discharged responsibility | one obligation != global projection invalidation |
| `WorkflowRun.completed` | declared-scope bounded completion | completed != universal responsibility discharge |

## Required negative invariants

- CO-R01: AI candidate exists -/-> approved.
- CO-R02: ExperienceUseAdmission.allowed -/-> Decision.
- CO-R03: HistoricalExperienceUse exists -/-> current qualification.
- CO-R04: WorkItemDecision.approve -/-> arbitrary execution authority.
- CO-R05: authorization for subject/version v1 -/-> v2.
- CO-R06: Shopify publish authorization -/-> refund/Odoo accounting operation.
- CO-R07: PublicationQualification.qualified -/-> Authorization.
- CO-R08: EffectLedger.succeeded -/-> ConfirmedOutcome.
- CO-R09: outcome_unknown -/-> automatic retry.
- CO-R10: repair disposition -/-> verified repair.
- CO-R11: reconciliation mismatch -/-> global KnowledgeProjection invalidation.
- CO-R12: workflow completed -/-> universal responsibility discharge.
- CO-R13: console/UI inference -/-> execution authority.
- CO-R14: discharged obligation -/-> deleted historical responsibility fact.

## Positive eligibility shapes

CO-P01: exact subject/version/fingerprint + allowed current Experience use when required + bound HistoricalExperienceUse + explicit Decision + matching unrevoked/unexpired Authorization + current Commerce qualification + no blocking applicable obligation may make a responsibility-aware effect eligible for dispatch. No single input substitutes for the others.

CO-P02: effect execution + authoritative external readback + explicit reality verification + no blocking reconciliation + declared completion requirements may establish bounded workflow completion. This does not claim universal goal completion or universal responsibility discharge.

## Server-owned authorization profiles

Commerce currently owns three profiles:

| Profile | Target | Operations |
| --- | --- | --- |
| `listing-publication-v1` | `shopify` | `shopify.product_publish` |
| `return-credit-note-v1` | `odoo` | `odoo.credit_note_create`, `odoo.credit_note_validate` |
| `return-refund-v1` | `shopify` | `shopify.refund_create` |

Approval for a work item with one of these profiles must use `POST /v1/work-items/{id}/authorized-decisions`. The server records the Decision and mints the distinct authorization in one transaction. Ordinary `POST /v1/work-items/{id}/decisions` refuses an `approve` that would bypass a required profile.

The server derives return financial scope from the current `ReturnCase`; a client cannot widen it. Listing publication retains a compatibility `scope` input, but target system and allowed operation are server-selected and dispatch revalidates current subject facts.

## CURRENT/HISTORICAL explanation rule

`GET /v1/workflows/{workflow_id}/responsibility` is a non-authority-bearing inspector projection.

- `historical` preserves the exact prior Experience-use reliance.
- `currentResponsibility` re-evaluates current listing-publication eligibility.
- `decisions`, `authorizations`, `execution`, `reality`, `confirmedOutcomes`, `responsibilityObligations` and `openResponsibility` expose the durable chain without upgrading one fact type into another.

For listing publication, the current explanation function is shared with the dispatch eligibility composition. The UI must present the server explanation rather than reimplement authority logic.

## Bounded completion rule

Completion uses `backend/app/services/workflow_completion.py` and permanently reports `coverage_claim = "declared-scope-only"`.

A blocked completion is re-evaluated only through the explicit durable `workflow.completion_recheck_requested` signal. A recheck request neither creates evidence nor directly marks the run complete.

## Persistence rule

New responsibility facts are append-only or explicitly versioned. Historical legacy facts are never upgraded by inference. In particular:

- old `approval_ref` is not backfilled as Authorization;
- old `succeeded` effects are not backfilled as ConfirmedOutcome;
- old reconciliation resolutions are not backfilled as verified recovery;
- HistoricalExperienceUse cannot be retroactively added to a pre-existing judgment when the portable contract forbids backfill;
- discharged obligations remain visible as historical records.

## Consumer ceilings

The Commerce console is an inspection/request consumer. It cannot create an Authorization, InvocationPermit, dispatch authority, ConfirmedOutcome or Experience-use authority by presenting or deriving a client-side object.

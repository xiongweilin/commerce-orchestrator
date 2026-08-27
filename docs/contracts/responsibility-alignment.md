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
Shopify / Odoo + canonical reconciliation
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
| commercial judgment | Assertion with `task-domain-judgment` role | judgment != Decision |
| WorkItemDecision | Commerce specialization of Decision | Decision != Authorization |
| RBAC/four-eyes/compliance checks | policy/authority basis | passing policy != AuthorizationGrant |
| PublicationQualificationAssessment | Commerce exact-context publication qualification | qualified != Experience admission or Authorization |
| EffectExecutionRequest | effect/action intent | intent != execution fact |
| DBOS effect step | execution occurrence | DBOS success != confirmed business Outcome |
| EffectLedger `succeeded` | adapter/execution report | succeeded != ConfirmedOutcome |
| EffectRealizationAssessment `verified` | reality verification evidence | verified != universal objective completion |
| ReconciliationResolution | recovery disposition + verification | disposition != realized repair |
| WorkflowRun `completed` | declared-scope bounded completion | completed != universal responsibility discharge |

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

## Positive eligibility shapes

CO-P01: exact subject/version + allowed current Experience use + bound HistoricalExperienceUse + explicit Decision + matching unrevoked Authorization + current Commerce qualification may make a responsibility-aware effect eligible for dispatch. No single input substitutes for the others.

CO-P02: effect execution + authoritative external readback + verification + no blocking reconciliation + declared completion requirements may establish bounded workflow completion. This does not claim universal goal completion or universal responsibility discharge.

## Persistence rule

New responsibility facts are append-only or explicitly versioned. Historical legacy facts are never upgraded by inference. In particular:

- old `approval_ref` is not backfilled as Authorization;
- old `succeeded` effects are not backfilled as ConfirmedOutcome;
- old reconciliation resolutions are not backfilled as verified recovery;
- HistoricalExperienceUse cannot be retroactively added to a pre-existing judgment when the portable contract forbids backfill.

## Consumer ceilings

The Commerce console is an inspection/request consumer. It cannot create an Authorization, InvocationPermit, dispatch authority, ConfirmedOutcome or Experience-use authority by presenting a client-side object.

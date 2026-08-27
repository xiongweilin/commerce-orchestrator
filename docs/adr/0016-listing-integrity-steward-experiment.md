# ADR 0016: Listing Integrity Steward as a persistent-responsibility experiment

- Status: Experimental
- Milestone: C20
- Scope: No schema migration, no new effect authority, no change to existing listing publication authorization profiles

## Context

C16-C19 established responsibility separation inside bounded workflows: current-use qualification, distinct Decision and ExecutionAuthorization, effect realization, ConfirmedOutcome, open/discharged obligations, and a non-authority-bearing responsibility inspector.

The next question is different: what owns responsibility *after* one workflow completes? A listing-integrity concern is persistent. A successful diagnosis or repair does not mean the organization can stop being responsible for future drift.

## Decision

Introduce a non-canonical `Listing Integrity Steward` experiment under `backend/app/experiments/`.

The standing mission is:

> Maintain Shopify listing integrity against the currently qualified catalog state.

The experiment uses this candidate flow:

```text
Standing mission
  -> Commerce catalog facts + current publication qualification
  -> Shopify readback evidence supplied by the Shopify fact owner
  -> Situation assessment
  -> Non-authority-bearing work proposal
  -> Bounded resource commitment
  -> Read-only diagnosis / evidence preparation
  -> if external mutation is required: existing human Decision -> ExecutionAuthorization path
  -> existing Effect -> realization -> ConfirmedOutcome semantics
  -> reassess mission
  -> mission remains active
```

## Responsibility cuts under test

The experiment is intended to falsify, not canonize, these candidate cuts:

- observation is not situation assessment;
- situation assessment is not work proposal;
- work proposal is not resource commitment;
- commitment is not ExecutionAuthorization;
- resource allocation is not external-effect authority;
- no observed failure is not verified health;
- completed diagnostic work is not discharge of the standing mission;
- standing responsibility is not permanent authority.

The portable-runtime Stage-4 experiment contains the broader candidate set, including priority judgment, delegation, and subdelegation. Commerce only specializes cuts that this domain can currently falsify.

## Fact ownership

The steward does not introduce a global world-state owner.

- Catalog revision and publication qualification remain Commerce-owned facts.
- Shopify readback remains Shopify-owned reality evidence supplied through the integration boundary.
- The steward owns only its projection, assessment, proposal, and bounded commitment.
- Existing workflow/effect/reconciliation records remain owned by their existing modules.

A missing readback can become a situation only relative to an explicit expected-by time. Absence of an error is never promoted to `health-verified` without current Shopify readback evidence and current publication qualification.

## Authority

Every new experimental record is `authority_bearing = false`.

The steward may autonomously diagnose and prepare evidence within a resource envelope. It may not:

- approve a listing publication change;
- mint `ExecutionAuthorization`;
- reinterpret a resource allocation as external-effect permission;
- bypass current publication qualification;
- treat a proposed repair as a realized repair.

An external listing mutation therefore routes to `HUMAN_DECISION_REQUIRED` and reuses the existing Commerce responsibility chain.

## Resource governance

`StewardResourceRequest` and `StewardResourceEnvelope` make resource commitment explicit. This is deliberately separate from external-effect authorization. The first experiment uses API-call, compute, and human-attention units rather than pretending that all priorities collapse into one objective scalar.

## Promotion rule

No type in this experiment becomes canonical merely because it works here. Promotion requires repeated counterexamples across independent domains showing that collapsing the responsibility position causes an unsafe shortcut, historical ambiguity, or unverifiable state.

## Consequences

Positive:

- the first standing responsibility outlives bounded Work;
- positive events and missing expected evidence can both create situations;
- autonomous diagnosis can increase without silently increasing effect authority;
- Commerce becomes a reference domain for the portable-runtime Stage-4 hypothesis.

Costs:

- the experiment intentionally duplicates some concepts rather than prematurely moving them into a shared canonical package;
- there is no persistence schema for missions/proposals/commitments yet;
- there is no production scheduler or autonomous trigger wiring yet.

Those costs are intentional. The experiment should earn canonical surface through falsification before operational persistence is added.

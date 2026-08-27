# Commerce persistent-responsibility runtime boundary

Commerce consumes `portable-runtime` at the exact revision declared in
`docs/contracts/responsibility-compatibility.toml`.

## Ownership

Commerce PostgreSQL remains authoritative for catalog/listing facts,
publication qualification, Decisions, ExecutionAuthorization, EffectLedger,
reconciliation and verified outcomes. DBOS remains the durable workflow
execution substrate.

The portable SQLite store is owned by the worker and persists only portable
responsibility coordination state: StandingResponsibility identity/history,
assessments, proposals, priority/portfolio decisions, reservations,
commitments, reasoning/session continuity and materialized portable Work.

The two stores are deliberately **not** presented as one atomic transaction
domain. A portable assessment or commitment cannot mint Commerce effect
authority. Before any external mutation, Commerce must independently re-read
current domain facts and revalidate the existing version/scope-bound
Decision/ExecutionAuthorization path. A disagreement between stores therefore
fails closed at the effect boundary rather than being resolved by treating the
portable journal as Commerce truth.

## Durable worker wiring

`app.responsibility.runtime.get_responsibility_kernel()` constructs a
`ResponsibilityKernel(SQLiteStateStore(...))` from
`COMMERCE_RESPONSIBILITY_STATE_PATH`. The worker boot path restores this store
and idempotently admits the listing-integrity StandingResponsibility. Compose
mounts the configured path on the `responsibility-state` named volume so worker
process/container restarts retain the same responsibility identity/history.

## Closure evidence

`tests/test_c20_listing_integrity_steward.py` includes a SQLite restart-boundary
test that closes and reconstructs multiple kernels over one persisted file. It
proves the same responsibility/version/status and assessment survive restart;
proposal/reservation/commitment/Work continue without duplicated Work or any
AuthorizationGrant; terminal Work completion still passes through the existing
portable CompletionAuthority proof gate; and the standing responsibility
remains ACTIVE after bounded Work completion.

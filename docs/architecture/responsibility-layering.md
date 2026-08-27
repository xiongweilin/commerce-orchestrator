# Responsibility Layering

This architecture adds a semantic/responsibility plane to Commerce Orchestrator without replacing DBOS or changing domain fact ownership.

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
Decision / Authorization / obligation / qualification
               |
               v
DBOS durable workflow
               |
               v
Effect Ledger + adapters
               |
               v
external observation / realization / reconciliation / confirmed outcome
```

## Ownership

- `portable-runtime/contracts/` owns generic portable responsibility semantics.
- Commerce owns its API/event vocabulary and domain specializations.
- Commerce PostgreSQL remains the persistence owner for Commerce orchestration and responsibility projections.
- DBOS remains the only durable workflow engine.
- Odoo and Shopify retain the fact ownership defined in `docs/contracts/data-ownership.md`.
- The console is non-authoritative.

## Dependency rule

Commerce production code may consume the portable public-contract/reference seam. It must not depend on portable governance dispatch, InvocationPermit, provider routing or other execution internals. The portable reference implementation may evaluate Experience Use and prepare HistoricalExperienceUse compare-and-bind; Commerce supplies persistence and transaction boundaries.

## Historical migration rule

Responsibility data introduced after this architecture is additive. Existing workflow/effect/reconciliation rows are not reinterpreted as if explicit responsibility objects had existed historically.

## Pilot

The first real domain slice is feedback/catalog knowledge -> commercial judgment -> historical experience reliance -> human Decision -> scoped Authorization -> Shopify publication execution -> readback/realization -> bounded completion.

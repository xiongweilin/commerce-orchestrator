# Commerce Orchestrator — frozen predecessor snapshot

[![Legacy CI](https://github.com/xiongweilin/commerce-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/xiongweilin/commerce-orchestrator/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Status: frozen historical predecessor.**

This repository preserves the pre-`world-runtime` Commerce implementation and its historical design evidence. It is not part of the current Personal AI OS runtime, is not an active deployment target, and must not be used as evidence that `agent-kernel` remains an active dependency.

## Boundary

- Current durable agency owner: [xiongweilin/world-runtime](https://github.com/xiongweilin/world-runtime).
- Current cross-domain semantic owner: [xiongweilin/semantic-language](https://github.com/xiongweilin/semantic-language).
- This repository owns only historical Commerce implementation evidence from the retired predecessor line.
- The pinned `agent-kernel` compatibility record is historical evidence only. It must not be advanced, revived, or extended.
- Re-entry into the active architecture requires an explicit Commerce migration to Runtime Protocol 4.0 or its successor. That migration must create new current contracts rather than treating the predecessor snapshot as runnable.

## What is preserved

The snapshot contains the former Commerce specialization, including:

- candidate / Decision / execution-authority separation;
- DBOS-based durable workflows;
- effect ledger and reconciliation logic;
- Shopify/Odoo adapters and reality read-back;
- bounded completion and responsibility experiments;
- historical ADRs, contracts, runbooks, console, backend and infrastructure.

Those files are preserved for lineage and reference. Their package versions, external APIs, Compose configuration, dependency locks and runbooks are **not current operational instructions**.

## Do not

Do not:

- run the old Quick Start as a supported system;
- treat backend/Compose buildability as maintained;
- update the retired `agent-kernel` pin;
- add new features or compatibility work to the predecessor line;
- infer current Commerce, Runtime, authorization or deployment semantics from this snapshot.

If a historical document says “current”, interpret it relative to the commit in which it was written, not relative to the current Personal AI OS.

## Preservation CI

The GitHub workflow checks repository hygiene and the explicit frozen-boundary marker. Historical dependency findings may remain visible, but they are not deployability claims. No backend, console or Compose runtime acceptance is performed.

## Historical documentation

The existing `docs/`, `backend/`, `console/`, `services/`, `infra/` and `compose.yaml` trees remain intentionally preserved. They are historical artifacts, not current runbooks.

For active architecture and doctrine, use:

- https://github.com/xiongweilin/guide
- https://github.com/xiongweilin/world-runtime
- https://github.com/xiongweilin/semantic-language

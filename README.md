# Commerce Orchestrator — frozen reference snapshot

[![Legacy CI](https://github.com/xiongweilin/commerce-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/xiongweilin/commerce-orchestrator/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Status: frozen. Not a current deployment target.**

This repository is retained as a reference snapshot for Commerce workflow design, effect reconciliation,
Shopify/Odoo integration, DBOS orchestration, and domain-completion experiments.

Do not use its Compose files, runbooks, dependency pins, API shapes, or service topology as current AIOS
operational guidance.

Current shared semantic/runtime owners:

- [AIOS monorepo](https://github.com/xiongweilin/aios)
- [World Runtime](https://github.com/xiongweilin/aios/tree/main/world-runtime)
- [Semantic Language](https://github.com/xiongweilin/aios/tree/main/semantic-language)
- [guide](https://github.com/xiongweilin/guide)

Feature development does not continue on this snapshot. A future Commerce implementation must establish
new current contracts against the active AIOS runtime rather than extending this tree in place.

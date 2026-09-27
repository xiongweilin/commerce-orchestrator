# Commerce Orchestrator — frozen reference snapshot

[![CI](https://github.com/xiongweilin/commerce-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/xiongweilin/commerce-orchestrator/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![Docs: EN / 中文](https://img.shields.io/badge/docs-EN%20%7C%20%E4%B8%AD%E6%96%87-blue.svg)](README.zh-CN.md)

[English](README.md) | [简体中文](README.zh-CN.md)

**Status: frozen historical predecessor.**

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

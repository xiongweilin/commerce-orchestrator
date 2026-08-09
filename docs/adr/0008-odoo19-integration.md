# ADR-0008：Odoo 19 集成（External JSON-2 API 优先）

- **Status**: Accepted
- **Date**: 2026-08-10

## Context

Odoo 19 是权威账本，需集成商品、库存、订单、财务等写入路径。Odoo 提供多种外部接口（XML-RPC、`/jsonrpc`、新的 External JSON-2 API），且 Community 版本对某些接口的实际可用性未知。

## Decision

- **优先使用官方 External JSON-2 API**：`POST /json/2/<model>/<method>` + bearer API key；`/jsonrpc` 已弃用，不新增依赖。
- **必须在真实 Odoo 19 Community 容器运行时实测可用性**；未证实前不进入写入阶段（只读/对账路径也需说明）。
- 若 JSON-2 在 Community 不可用：**在 Odoo 内开发最小 integration module**（鉴权 HTTPS command endpoint、`x_integration_key`、integration outbox），**不扩大 XML-RPC 依赖**。
- **需要原子性的动作由单个窄 Odoo method 完成**，避免跨方法组合产生中间态。
- 写入必须满足幂等与 effect ledger 语义（ADR-0004 与 architecture.md 5.3）。

## Consequences

**正面**：使用官方现代接口，减少弃用风险；写入前有实测门禁；integration module 为最小集，可控可审计。

**负面/约束**：需要真实容器做集成验证环境；Community 可用性未证实前写路径不可用；若需扩展 Odoo method，需要 Odoo 内开发与发布流程。

## References

- <https://www.odoo.com/documentation/19.0/developer/reference/external_api.html>

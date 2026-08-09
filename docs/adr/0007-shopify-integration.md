# ADR-0007：Shopify 集成（冻结 API 版本 + webhook 安全 + 增量对账）

- **Status**: Accepted
- **Date**: 2026-08-10

## Context

Shopify 是首个外部渠道，涉及产品发布、更新、履约与退款效果；webhook 可能被伪造、乱序、漏投；GraphQL 响应错误形态多样（HTTP 层、顶层 errors、mutation userErrors），单一检查会漏判失败。

## Decision

- **Admin GraphQL 使用冻结稳定版本 `2026-07`**（2026-07-01 发布），**禁止 `latest`**；升级 API 版本需显式计划与回归测试。
- **webhook 校验**：对原始请求体计算 **Base64 HMAC-SHA256**，与 `X-Shopify-Hmac-Sha256` 做**常量时间比较**；按 `X-Shopify-Webhook-Id` 去重（inbox 唯一键 `(consumer=shopify-webhook, eventId)`）。
- **容忍乱序与漏投**：不假设投递顺序；建立基于 `updated_at` 的**增量对账**兜底。
- **GraphQL 响应必须同时检查**：HTTP 状态、顶层 `errors`、mutation `userErrors`；任一失败都不得视为成功，进入 effect 失败或 `outcome_unknown` 路径。

## Consequences

**正面**：API 行为稳定可回归；webhook 安全与去重明确；漏投由对账兜底，不依赖投递可靠性。

**负面/约束**：版本升级节奏受 Shopify 版本周期约束；增量对账需要持续运行；userErrors 需映射为业务错误码。

## References

- <https://shopify.dev/docs/api/usage/versioning>
- <https://shopify.dev/docs/apps/build/webhooks/verify-deliveries>

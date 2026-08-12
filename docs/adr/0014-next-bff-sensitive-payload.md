# ADR-0014：Next.js BFF 安全会话、敏感 payload 保留与最小权限

- **Status**: Accepted
- **Date**: 2026-08-11

## Context

v1 控制台把 JWT 放在 `localStorage`、浏览器直接向 `NEXT_PUBLIC_API_BASE` 发
`Authorization`，token 可被 XSS 读取；敏感 payload（原始 webhook、shipping/customer
数据）缺少统一加密 vault 与保留策略；JWT role claims 与数据库 `RoleAssignment`
并存，权限来源不清晰。

## Decision

- **Next.js BFF 同源会话**：
  - `POST /api/session`：接收一次 JWT，调用后端 `/v1/me` 验证后设置
    `commerce_session` cookie；`DELETE /api/session` 清除会话；
  - `/api/backend/[...path]`：allowlist 代理允许的后端路径与方法；
    禁止代理 `/v1/webhooks/shopify`；非 GET 强制 `X-CSRF-Token` 常量时间比较
    与 `Origin` 校验（等于配置的 console origin）；
  - Cookie：`HttpOnly`、非 dev 强制 `Secure`、`SameSite=Strict`、`Path=/`、
    Max-Age = min(JWT 剩余 TTL, 8h)；
  - 创建会话时生成随机 `commerce_csrf` 非敏感 cookie；移除 JWT `localStorage`
    与 JS 可读的 token cookie；`COMMERCE_API_BASE` 为服务器私有环境变量，
    客户端只访问同源 BFF。
- **敏感 payload vault（`sensitive_payload` 表）**：`purpose`、`classification`、
  `owner`、`source_type`、`source_id`、`ciphertext`（Fernet）、`key_version`、
  `expires_at`、`deleted_at`、`created_at`；默认保留 30 天。
- **伪匿名 customer ref**：对需要匹配但无需还原的 customer ref 用
  `COMMERCE_PII_HASH_KEY` 做 HMAC-SHA256，带 `pii:` 前缀；workflow input /
  outbox payload 只保存最小字段与 `sensitivePayloadId`，不保存完整 webhook。
- **后台 backfill 与清理**：对旧 `SalesOrder.customer_ref`、shipping JSON、
  `ReturnCase.customer_ref` 做加密 backfill（幂等，`pii:` 标记跳过）；
  清理 job 每日执行：到期后**先清 ciphertext，再写 tombstone（deleted_at）**，
  记录删除数量/失败数量/最老过期年龄；日志、trace、指标不输出内容。
- **最小权限**：`get_current_user` 查询 `User` 并校验 `is_active`；JWT role claims
  仅作展示，权限以数据库 `RoleAssignment` 为准；`system_admin` 不自动获得业务
  审批权；compliance 只能在其范围 reject/veto 不能 approve；权限拒绝写审计
  （不记录 token/请求体/PII）。数据库角色分离：
  `commerce_migrator`/`commerce_api`/`commerce_worker`/`commerce_readonly`/
  `dbos_app`/`metabase_app`/`odoo_app`（既有 owner `commerce` 兼容保留）。

## Consequences

**正面**：token 不再暴露给浏览器 JS；敏感数据统一加密 + 30 天保留 + 到期清理可审计；
权限来源唯一（DB RoleAssignment）；数据库按角色最小授权。

**负面/约束**：BFF 引入会话/CSRF 生命周期（Max-Age 与 JWT TTL 联动）；
`COMMERCE_PII_HASH_KEY` 丢失将无法匹配历史 customer ref（需先备份再轮换）；
明文 PII 的不可逆清空（计划三.1 第 7 步）需单独明确批准后执行。

## Supersedes

- 旧架构 6.1 中“原始 payload 加密存储（如 AES-GCM，`ENCRYPTION_KEY`）”的落地方案：
  统一收敛为 `sensitive_payload` vault + Fernet + 30 天保留 + HMAC 伪匿名；
  加密密钥语义（`COMMERCE_ENCRYPTION_KEY`）不变。
- v1 控制台“JWT 入 localStorage + 浏览器直连 API”方案被 BFF 会话取代；
  `NEXT_PUBLIC_API_BASE` 不再作为客户端运行时配置。

## References

- [architecture.md](../architecture.md)（3/6）
- [contracts/data-ownership.md](../contracts/data-ownership.md)
- [runbooks/privacy-cleanup.md](../runbooks/privacy-cleanup.md)

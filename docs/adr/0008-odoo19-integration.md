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

## P0 实测结论（2026-08-10，Odoo 19.0 Community 容器 `odoo:19`）

在 `docker compose --profile odoo up -d` 的 Odoo 19.0 Community 容器上完成运行时验证：

1. **JSON-2 路由可用**：`POST /json/2/<model>/<method>` 存在；无/错误 bearer → `401 Unauthorized`；未知子路径 → `404`（提示 "Did you mean POST /json/2/<model>/<method>?"）。需通过 `X-Odoo-Database: <db>` 头显式选库，否则返回 "No database is selected"。
2. **Bearer API key 可用**：key 经 `res.users.apikeys._generate(scope=None, name, expiration)` 生成（scope 必须为 NULL 全局 key，bearer 校验实际使用 `scope='rpc'` 且要求 `scope IS NULL`）；key 必须挂在 **active** 用户下（系统 `__system__` 用户 inactive，其 key 校验恒失败——踩坑记录）；容器内 `odoo shell` 生成的 key 需显式 `env.cr.commit()` 否则不落库。
3. **请求体形状（实测）**：`create(model, vals_list)` → body `{"vals_list": [ {...} ]}`；`write` → body `{"ids": [id], "vals": {...}}`；`search_read` → `{"domain": [], "fields": [...], "limit": n}`；action 类（`action_confirm`/`action_post`/`action_done`）→ `{"ids": [id]}`。返回：create 为 `[new_id]`，write 为 `true`，读为 `[{...}]`。
4. **实测通过项**：`res.users/search_read`、`product.template/create`、`product.template/write`（含回读验证）；`backend/app/connectors/odoo.py` 已按上述形状实现并端到端验证（probe/create/update 全绿，对应 MockTransport 单测同步更新）。
5. **模块依赖**：仅装 `base` 时 `product.product` 不存在（404）；P0 容器内已 `-i product` 安装以验证商品写入。生产 Odoo 按 P3 分批安装（`sale_management`/`purchase`/`account`/`delivery` 等）并遵守 backup-restore.md 基线流程。
6. **结论**：**Odoo 19 Community 的 JSON-2 可用**，P0 写入门禁满足，无需 fallback integration module；正式环境仍需生产级 key 轮换与权限收敛（最小权限用户，非 admin）。

## References

- <https://www.odoo.com/documentation/19.0/developer/reference/external_api.html>

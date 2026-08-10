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

## P3 实测补充（2026-08-10，模块安装与 O2C 全链路）

在 `commerce-odoo19`（odoo:19）上完成 P3 模块安装与订单到现金全链路验证，基线备份
`work/odoo_baseline_before_p3.sql`（pg_dump 16.14，可读性已验证）：

1. **模块安装**：一次执行 `odoo -d odoo -i sale_management,purchase,purchase_stock,account,stock_account,delivery --stop-after-init --db_host=postgres --db_port=5432 --db_user=commerce --db_password=commerce --log-level=warn` 成功（约 38s），sale/stock/payment/portal 等依赖自动带入；`docker restart` 后验证。**坑**：Odoo 19 XML-RPC `authenticate(db, login, password, user_agent_env)` 需要第 4 个参数（`{}`），只传 3 个参数报 `TypeError: exp_authenticate() missing 1 required positional argument: 'user_agent_env'`。
2. **商品创建**：`product.template.type` 在 Odoo 19 仅 `consu`(Goods)/`service`/`combo`，传 `storable` 报 `Wrong value for product.template.type`；可存储商品应传 `type='consu'` + `is_storable=True`。
3. **库存（原子性实测）**：`stock.quant` **无 `reason` 字段**（传入报 `Invalid field 'reason'`）；`inventory_diff_quantity` 为只读存储计算字段，create 时值被静默丢弃（on-hand 不变）。唯一实测可用的**单调用原子路径**：`stock.quant/write {"ids": [...], "vals": {"inventory_quantity_auto_apply": N}, "context": {"inventory_mode": True}}` —— inverse 在同一服务端事务内设置计数并 `action_apply_inventory`（生成并过账库存移动）；要求调用用户具备 `stock.group_stock_user`（admin 隐含）。`backend/app/connectors/odoo.py::update_quantity` 已按此修正：语义改为绝对 on-hand 数量、移除 `reason`、幂等（重复写入当前值服务端跳过）。
4. **订单→发票**：`create_invoice` 若行 vals 不带 `sale_line_ids` 关联，发票可过账但订单 `invoice_status` 保持 `to invoice`、`invoice_ids` 为空；需在 `invoice_line_ids` 行里给 `sale_line_ids: [(6, 0, [sale_line_id])]`（已过账发票可 `button_draft` → write 补链 → `action_post` 修复）。**坑**：`account.move/action_post` 的 JSON-2 返回值不稳定（同方法两次调用分别返回 `True`/`False`），必须读回 `state` 确认，不能依赖返回值。
5. **货币/税**：Odoo 默认 USD，Shopify CNY 映射待正式处理（演示按数值 99 记价）；公司默认 15% 销售税使订单/发票合计 113.85（行单价 99，`amount_total` 含税）。
6. **端到端记录**：partner `Shopify-Test-Customer` id=6；商品 `SKU-YIFU-01`/衣服 template+product id=5（参考价 99，Goods+storable）；`stock.quant` id=1（WH/Stock on-hand 11，id=2 为 Inventory adjustment 残差 -11）；sale.order id=1 `S00001`（state=sale，`invoice_status=invoiced`，ref #1001，合计 113.85 USD）；account.move id=1 `INV/2026/00001`（state=posted，payment_state=not_paid，invoice_origin=S00001）。P0 测试商品（id 1/2/3）已清理。

### P3 补充 2（2026-08-10，采购闭环实测）

- **收货方法名**：`stock.picking` 的过账方法是 `button_validate`（Odoo 19；旧 `action_done` 返回 404
  "The method 'stock.picking.action_done' does not exist"），连接器 `validate_picking`/`receive_transfer` 已改用。
- **账单过账需日期**：`account.move/action_post` 对 `in_invoice`/`in_refund` 要求 `invoice_date`，
  缺失报 422 "The Bill/Refund date is required to validate this document"。
- **采购闭环实测**：`purchase.order/create` + `button_confirm` → 自动生成收据 picking →
  `stock.picking/button_validate` 收货 → `account.move`（`in_invoice` + `invoice_line_ids` + `invoice_date`）
  创建并过账——全链路通过，对账 0 差异。

## References

- <https://www.odoo.com/documentation/19.0/developer/reference/external_api.html>

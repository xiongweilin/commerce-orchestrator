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

## P0 实测结论（2026-08-10，`metratio.myshopify.com` 开发店）

1. **认证走 client-credentials**：Dev Dashboard 应用（Client ID + `shpss_` Client Secret）经
   `POST /admin/oauth/access_token`（form：`grant_type=client_credentials`）换取约 24h Admin API token（`expires_in=86399`）。
   前提：应用必须先**安装**到店铺（否则 `app_not_installed`），且安装版本需声明 scope 并经授权页重新同意
   （安装链接 `https://admin.shopify.com/store/{shop}/oauth/install?client_id={client_id}`）。
   已授 scope：`write_products/write_orders/write_inventory/write_assigned_fulfillment_orders/write_publications`
   （write 隐含 read）。`COMMERCE_SHOPIFY_SHOP_NAME` 只填店铺前缀（如 `metratio`），连接器自行拼接 `.myshopify.com`；
   填全域名会拼出双后缀导致 DNS 通配劫持（伪证书）——踩坑记录。
2. **webhook 公网入口**：`https://metratio.com/webhooks/shopify`（云端 nginx 精确匹配 → tailnet 8086 →
   Windows `127.0.0.1:18086`），路由已写入 metratio.com 基础设施文档。订阅了 `ORDERS_CREATE/REFUNDS_CREATE/PRODUCTS_UPDATE`
   （GraphQL `webhookSubscriptionCreate`，API 2026-07）。
3. **端到端验证通过**：HMAC（client secret 为密钥）+ `X-Shopify-Webhook-Id`（UUID）去重；同 id 重放返回
   `{received:true, deduplicated:true}`；`orders/create`→`order.received`、`refunds/create`→`return.case_requested`
   事件入库（producer=`shopify_adapter`），并创建 `order-to-cash`/`return-to-refund` 工作流；原始 payload 加密留存
   （`projection` 表，owner=`shopify_webhook`）。
4. **2026-07 查询差异**：`orders` 连接不接受 `updatedAfter`，且不存在 `ordersIncremental` 根查询；
   增量改用 `orders(query: "updated_at:>…")` 过滤（连接器已实现）。
   变体写入差异：`ProductUpdateInput` 不含 `variants`；变体字段（如 `sku`）经
   `productVariantsBulkUpdate` + `inventoryItem.sku` 更新（2026-08-10 实测：改 SKU 后 Shopify 即时推送
   `products/update` webhook，形成"写→渠道→webhook→`catalog.revision_drafted`"双向闭环）。
   新建商品推送的是 `products/create`（与 `products/update` 分开订阅）。
5. **订单创建与 webhook**：`POST /admin/api/{v}/orders.json` 可直接创建正式订单（需 `write_orders`，
   无需草稿权限），但 **Shopify 对 API/后台创建的订单不推送 `orders/create` webhook**（webhook 仅由结账流程
   触发的订单产生）——实测创建 #1001（PAID）后无投递。因此 API 建单场景需直接驱动本地 O2C 工作流
   （backend/scripts/run_test_order_flow.py 已封装：摄入→13 步状态机→对账，幂等）。
6. **货币**：开发店默认币种为 JPY（REST 建单返回 JPY）；Odoo 演示为 USD，映射与税率待正式处理。
   对账（domains=['shopify']）会把"本地 closed vs 渠道 PAID"之类差异记为 MANUAL_RECONCILIATION，不自动抹平。
7. **productVariantsBulkUpdate 形状**：2026-07 输入类型 `ProductVariantsBulkInput` 无 `sku` 字段，
   SKU 位于 `inventoryItem.sku`（已实测）。发布用 `publishablePublish` + publication id
   （Online Store `gid://shopify/Publication/{id}`）。
5. **本机网络注意**（环境运维手册佐证）：Windows 侧 v2rayN 为 7890 代理模式（无 TUN），直连 Shopify 时 DNS 优先返回
   IPv6 而 IPv6 出口 EOF——连接器进程内**优先 IPv4** 解析；证书校验用 certifi + Windows 系统库**合并信任**；
   v2rayN 白名单已加 `domain:myshopify.com` 直连规则（备份索引已更新）。

## References

- <https://shopify.dev/docs/api/usage/versioning>
- <https://shopify.dev/docs/apps/build/webhooks/verify-deliveries>

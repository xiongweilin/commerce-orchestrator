# 影刀导入与运行

本流程由影刀负责调度，只读取 API 门禁生成的 `approved.csv`，再通过 Odoo 官方 XML-RPC 接口写入本地 Community 沙箱。`validation_failed` 商品不会进入下载清单，因此影刀无法绕过确定性门禁。

> 这里展示的是“RPA 平台编排 + ERP 官方接口写入”，不是网页元素点击型自动化。最初的页面定位方案受 Odoo 动态视图影响，已改为可重试、可审计且更稳定的 XML-RPC 写入。

## 一次性创建应用

1. 在影刀中新建可视化应用，命名为“商品上架写入 Odoo（沙箱）”。
2. 新建 Python 模块 `catalog_odoo_rpa`，粘贴 `catalog_odoo_rpa.py` 全文并保存。
3. 主流程添加“调用模块”，选择模块的 `run` 函数。
4. 映射下表参数。令牌和密码使用密码输入参数或私有全局变量，不写入源码。
5. 影刀 6.0.30 会把带 Python 默认值的函数参数错误地生成空参数名，因此交付模块的 `run` 故意不设置默认值；`max_items` 和 `dry_run` 必须显式填写。

| 参数 | 值 |
|---|---|
| `api_base_url` | `http://127.0.0.1:18200` |
| `run_id` | API 回放返回的 UUID |
| `rpa_token` | 项目 `.env` 中的 `RPA_TOKEN` |
| `odoo_login_url` | `http://127.0.0.1:18069/web/login?db=catalog_erp` |
| `odoo_product_list_url` | `http://127.0.0.1:18069/odoo/action-282`（兼容参数，RPC 模式不读取页面） |
| `odoo_username` | 本地 Odoo 用户名 |
| `odoo_password` | 本地 Odoo 密码 |
| `max_items` | 首次验证 `1`，全量 `20` |
| `dry_run` | 影刀表达式填写字符串 `"true"` 或 `"false"`，不要填写未定义变量 `true` / `false` |

## 工作机制

1. 下载经过门禁的 `approved.csv`。
2. 从登录 URL 提取 Odoo 地址与数据库名并完成 XML-RPC 鉴权。
3. 动态读取 `product.template` / `product.product` 字段定义，避免硬编码不同 Odoo 版本的产品类型字段。
4. 先按 SKU 查询模板和变体；已存在则复用，防止重复创建。
5. 创建时依次尝试完整、无类型、最小字段集；最小创建成功后再补写可选字段。
6. 每条结果使用稳定 `operation_key` 回调 API。成功和失败均留下审计记录。

## 验证顺序

1. `dry_run="true"`、`max_items=20`：应返回 `approved=20, written=0, failed=0`，不写 Odoo、不回调状态。
2. `dry_run="false"`、`max_items=1`：确认 Odoo 新增或复用一个 SKU，API 当前状态变为 `erp_written`。
3. `dry_run="false"`、`max_items=20`：完成全量写入。
4. 再运行一次：按 SKU 复用商品，稳定 operation key 使 API 回调保持幂等，不产生重复 SKU。
5. 打开 Metabase“商品上架自动化运营看板”，分别检查“当前商品状态”和“操作尝试历史”。

## 已验证基线

运行 `fa2b4f0a-6460-4658-a8e1-af73970f963c` 已完成：

- 输入 30 条；
- 确定性校验失败 10 条；
- Dify 生成候选文案 20 条；
- Odoo 当前成功写入 20 条；
- 当前 ERP 写入错误 0 条；
- Odoo 中匹配演示 SKU 的商品 20 个。

操作历史中的 23 条 `failed` 来自调试阶段的旧尝试，保留用于审计；它们不代表最终仍有 23 个失败商品。最终商品状态和操作尝试历史必须分开解读。

## 失败与恢复

- Odoo 写入失败：回调 `erp_failed` 和错误摘要，不伪装成功；修复后可安全重跑。
- Odoo 已写入但回调失败：重跑时先按 SKU 找到已有商品，再用 `written-v1` operation key 补回调。
- HTTP 回调失败：每次重新构造请求并最多重试三次，HTTP 错误正文会进入诊断摘要。
- 不同 Odoo 版本字段不兼容：先读取字段元数据，再使用降级创建策略；必要字段仍失败时明确记录失败。

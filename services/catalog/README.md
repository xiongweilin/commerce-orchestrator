# 商品上架运营自动化

[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=metratio_catalog-ops-automation&metric=alert_status)](https://sonarcloud.io/dashboard?id=metratio_catalog-ops-automation)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=metratio_catalog-ops-automation&metric=coverage)](https://sonarcloud.io/dashboard?id=metratio_catalog-ops-automation)
[![CI](https://github.com/ratiolin/catalog-ops-automation/actions/workflows/ci.yml/badge.svg)](https://github.com/ratiolin/catalog-ops-automation/actions)


面向数字化运营/实施岗位的合成沙箱：商品 CSV 先经过确定性异常校验，Dify 为通过项生成候选文案，影刀编排 Odoo Community 的 XML-RPC 写入，Metabase 展示当前状态与操作审计。

```text
模拟商品 CSV → 确定性校验 → Dify 候选文案 → approved.csv
→ 影刀 6.0.30 编排 → Odoo XML-RPC 幂等写入 → API 回调 → Metabase
```

## 边界

- Dify 只生成标题、三个卖点和关键词，不决定是否写入 ERP。
- 只有确定性校验通过的记录才会出现在影刀下载清单。
- 影刀写入前按 SKU 查询 Odoo；重复执行不得创建重复商品。
- Metabase 是只读分析界面，不通过 BI 看板修改业务状态。
- Odoo、数据和账号均为本地合成沙箱，不代表生产 ERP 实施或真实业务收益。

## 已完成的真实回放

thinking 回放（2026-08-01，运行 `051b63d5-e736-41ef-abf8-a6ea9f341cc5`，`deepseek-v4-flash` + `thinking: true` + `reasoning_format: separated`）：

| 环节 | 数量 |
|---|---:|
| CSV 输入 | 30 |
| 确定性校验失败 | 10 |
| Dify 生成候选文案（`draft_source=dify`） | 20 |
| 当前已写入 Odoo | 20 |
| 当前 ERP 写入错误 | 0 |

dry-run 验证 `approved=20, written=0, failed=0`；全量写入 20/20 成功且全部按 SKU 复用已有商品（`odoo-existing-N`），再次运行 `approved=0`（已写入商品不再进入清单），幂等成立。详细证据见 `artifacts/evidence/run-051b63d5-thinking.md`。

历史基线：thinking 关闭的 flash 回放（2026-08-01，`7ed79404-…`，见 `artifacts/evidence/run-7ed79404-flash.json`）；pro 回放（2026-07-02，`fa2b4f0a-…`）：

| 环节 | 数量 |
|---|---:|
| CSV 输入 | 30 |
| 确定性校验失败 | 10 |
| Dify 生成候选文案 | 20 |
| 当前已写入 Odoo | 20 |
| 当前 ERP 写入错误 | 0 |
| Odoo 演示 SKU | 20 |

操作表还保留 23 次调试阶段失败尝试（来自 pro 基线回放）。它们是审计历史，不是当前失败商品；Metabase 将“当前商品状态”和“操作尝试历史”分开展示。历史基线证据见 `artifacts/evidence/run-fa2b4f0a.md`。

## 本地端口

- API/OpenAPI：<http://127.0.0.1:18200/docs>
- Odoo：<http://127.0.0.1:18069>
- Metabase：<http://127.0.0.1:18300>

## 启动

```bash
cp .env.example .env
# 设置 CATALOG_DB_PASSWORD、RPA_TOKEN、Dify Key 与本地平台账号
docker compose up -d catalog-postgres
docker compose --profile init run --rm catalog-odoo-init
docker compose up -d --build catalog-api catalog-worker catalog-odoo catalog-metabase
```

未配置 `DIFY_CATALOG_WORKFLOW_API_KEY` 时，只有在 `ALLOW_DEMO_COPYWRITER=true` 的显式演示模式下才允许使用 `demo_rules`。真实平台证据必须确认 `draft_source=dify`。

## 只读 BI 数据源

Metabase 自身数据库和业务数据源分离。业务数据源使用 `metabase_reader`，只有 `CONNECT/USAGE/SELECT` 权限：

```bash
set -a && source .env && set +a
docker compose exec -T catalog-postgres \
  psql -v ON_ERROR_STOP=1 -v reader_password="$METABASE_READER_PASSWORD" \
  -U catalog -d catalog_ops -f /dev/stdin < tools/configure_metabase_reader.sql
```

看板“商品上架自动化运营看板”包含：

- 最新批次当前状态；
- 确定性异常原因；
- 品类与状态交叉分布；
- ERP 操作尝试历史（明确包含调试失败）。

## 运行合成回放

```bash
uv run python tools/generate_sample_catalog.py
uv run python tools/run_catalog_demo.py
```

固定样本 30 条：20 条有效、10 条确定性异常（含重复 SKU）。演示规则与 Dify 结果通过 `draft_source` 明确区分。

## 影刀写入 Odoo

影刀模块位于 `shadowbot/catalog_odoo_rpa.py`（编排器），Odoo 适配层见 `shadowbot/odoo_adapter.py`，产品构建逻辑见 `shadowbot/product_builder.py`，导入与回放见 `shadowbot/IMPORT-RUN-GUIDE.md`。当前实现：

- 使用 Odoo 官方 XML-RPC，避免动态页面定位的不稳定；
- 动态读取字段元数据，兼容产品类型及可写字段差异；
- 按 SKU 查询模板和变体，保障重跑不重复创建；
- 完整、无类型、最小字段三档降级创建；
- 每条结果立即回调，成功与失败使用稳定 operation key；
- HTTP 回调重新构造请求并最多重试三次。

这是“影刀平台编排 + ERP 官方接口写入”的实操证据，不宣称为网页点击型 RPA。


## 代码质量

| 工具 | 用途 | 状态 |
|---|---|---|
| [ruff](https://docs.astral.sh/ruff/) | Lint + 格式化 | 零警告 |
| [pytest](https://docs.pytest.org/) | 单元 & 集成测试 | 92 通过，总覆盖率 99% |
| [SonarQube Cloud](https://sonarcloud.io/dashboard?id=metratio_catalog-ops-automation) | 持续代码质量 | 质量门 OK，新代码覆盖率 99.6%，未解决问题 0 |
| GitHub Actions CI | ruff + pytest + SonarQube | 已配置 |

CI 约束：`portfolio/index.html` 是静态作品页契约测试输入，必须随仓库提交；不要让测试依赖只存在于本地 ignored 文件中。

近期优化：拆分 `catalog_odoo_rpa.py`（683 行）为 `odoo_adapter.py` + `product_builder.py` + 编排器；提取 `MODEL_PRODUCT_*` 常量消除字符串重复；`build_product_values` 认知复杂度降低；FastAPI 端点补 `responses` 文档参数；补齐 API、Worker、service、RPA、Odoo 适配器、产品构建器边界测试。


## 验证

```bash
uv run pytest -q
uv run ruff check .
docker compose exec -T catalog-api alembic check
curl -fsS http://127.0.0.1:18200/health
curl -fsS http://127.0.0.1:18300/api/health
```

本项目只证明合成场景下的机制与平台实操，不声称生产上线、真实提效或销售收益。

# commerce-orchestrator backend

电商内部「运营控制塔」后端：跨系统工作流、审批、RBAC、幂等、效果台账（effect ledger）与对账，以 **Odoo 19** 为权威台账、**Shopify 开发店** 为首个渠道、**Metabase** 只读分析。

## 技术栈

- Python 3.12.13，由 uv 0.12.3 管理虚拟环境（`backend/.venv`）
- FastAPI + SQLAlchemy 2.0（同步风格）+ Alembic 迁移
- DBOS 2.29（仅 `app/workflows` 使用，见下文）
- pydantic v2 + pydantic-settings（配置，环境变量前缀 `COMMERCE_`）
- PostgreSQL（psycopg3）；模型类型同时兼容 SQLite（单元测试用）
- structlog（结构化日志）、prometheus-client、OpenTelemetry（可选 OTLP 导出）
- pyjwt（JWT）+ cryptography（Fernet 加密原始载荷）+ HMAC-SHA256（Shopify webhook 校验）

## 目录结构

```
backend/
  app/
    config.py        # pydantic-settings 配置（COMMERCE_ 前缀，get_settings() 缓存）
    core/            # 基础设施：db / uuid7 / time / security / errors / logging / telemetry
    models/          # SQLAlchemy 声明式模型（23 张表，一个领域一个模块）
    schemas/         # pydantic v2：基础类型、命令模型、事件/角色/效果操作词汇
    api/             # HTTP 路由层
    services/        # 业务逻辑
    workflows/       # DBOS 工作流（跨系统编排、审批）
    connectors/      # 外部系统连接器（Shopify / Odoo / Metabase）
  alembic/           # 迁移脚本（0001_initial 创建全部表）
  tests/             # 单元/集成测试
```

## 环境变量

全部以 `COMMERCE_` 为前缀（见 `app/config.py`），可在环境或 `backend/.env` 中配置。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `COMMERCE_DATABASE_URL` | `postgresql+psycopg://commerce:commerce@localhost:5432/commerce` | 应用主库 |
| `COMMERCE_DBOS_SYSTEM_DATABASE_URL` | `postgresql+psycopg://commerce:commerce@localhost:5432/dbos` | DBOS 系统库 |
| `COMMERCE_JWT_SECRET` | 无（必填） | JWT 签名密钥 |
| `COMMERCE_JWT_EXPIRES_MINUTES` | `480` | JWT 有效期（分钟） |
| `COMMERCE_ENCRYPTION_KEY` | 无（必填） | Fernet 密钥，用于加密原始载荷 |
| `COMMERCE_ENVIRONMENT` | `dev` | 运行环境（`dev` 输出控制台日志，其余输出 JSON） |
| `COMMERCE_LOG_LEVEL` | `INFO` | 日志级别 |
| `COMMERCE_SHOPIFY_API_VERSION` | `2026-07` | Shopify API 版本 |
| `COMMERCE_SHOPIFY_SHOP_NAME` | 空 | Shopify 店铺名 |
| `COMMERCE_SHOPIFY_ACCESS_TOKEN` | 空 | Shopify 访问令牌 |
| `COMMERCE_SHOPIFY_WEBHOOK_SECRET` | 空 | Shopify webhook HMAC 密钥 |
| `COMMERCE_ODOO_BASE_URL` | 空 | Odoo 19 基础地址 |
| `COMMERCE_ODOO_API_KEY` | 空 | Odoo API Key |
| `COMMERCE_ODOO_DB` | 空 | Odoo 数据库名 |
| `COMMERCE_ODOO_USERNAME` | 空 | Odoo 用户名 |
| `COMMERCE_OTLP_ENDPOINT` | 空 | OTLP 追踪端点；为空则遥测为 no-op |
| `COMMERCE_RAW_PAYLOAD_RETENTION_DAYS` | `30` | 原始载荷加密保留天数 |

## 本地运行

```bash
cd backend
uv sync                     # 安装依赖（勿手动修改 pyproject.toml / uv.lock）
uv run alembic upgrade head # 应用迁移（读取 COMMERCE_DATABASE_URL）
uv run uvicorn app.main:app --reload
```

## 测试

```bash
cd backend
uv run pytest
```

## 模拟脚本（无真实客户，真实系统）

位于 `scripts/`，均先自载仓库 `.env` 再导入 app，可直接 `uv run python scripts/<脚本>`：

- `run_test_order_flow.py [shopify_order_id]`：把 Shopify 订单送入 O2C 工作流并走完
  received → … → closed（13 步 + 4 个人工 gate + effect 记账 + 对账）；传订单号则幂等。
- `simulate_return_refund.py [shopify_order_id]`：整单退货退款闭环——自建测试订单（带支付交易），
  5 个人工审批门（四眼），Odoo 贷项通知单过账，Shopify refundCreate（有父交易用 manual，否则 cash），
  effect ledger + 三方对账；无参数时每次新建一笔测试订单。
- `simulate_feedback_to_catalog.py`：Feedback → 聚类 → AI 候选（draft→candidate→frozen→scored→official，
  AI 仅建议不批准）→ 商品修订审批 → 上架 effect planned；检测到 simulated-v1 候选即跳过（`--force` 重跑）。
- `simulate_procurement.py`：采购闭环——需求→RFQ→审批（budget_owner 四眼）→收货（warehouse_staff）→
  账单（accountant ×2 四眼）→ closed；worker 段真实执行 Odoo PO 创建/确认、收货（button_validate）、
  账单创建+过账，effect ledger 全链路记账，对账 0 差异。

说明：API/后台创建的 Shopify 订单不推送 webhook（Shopify 限制，仅结账流程触发），脚本因此直接驱动本地工作流；
对账差异一律进入 `MANUAL_RECONCILIATION`，不自动抹平。

## 代码规范

```bash
cd backend
uv run ruff check app
uv run ruff format --check app
```

规则见 `backend/pyproject.toml` 的 `[tool.ruff]`。

## 与 DBOS 的关系

- `app/workflows` 使用 DBOS 编排跨系统工作流（状态持久化、重试、恢复），并通过 `app/core` 的会话、效果台账与消息表持久化中间状态。
- `app/core`、`app/models`、`app/schemas` **不依赖 DBOS**，可独立运行与测试；`app/main.py` 仅在此包内组装 FastAPI 与 DBOS 运行时。

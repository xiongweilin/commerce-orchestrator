# Runbook：本地开发环境

## 前置条件

- Python 3.12（见 `backend/.python-version`）、uv
- Node.js（Next.js 16.2.12 / React 19.2.8，需支持当前 LTS）
- Docker + Docker Compose（用于 PostgreSQL 等依赖）
- 本仓库已 clone 到本地，分支 `main`

## 启动步骤

### 1. 启动依赖

```bash
docker compose up -d postgres
```

服务清单、健康检查与数据目录以 [infra/README.md](../../infra/README.md) 为准。

### 2. 后端

完整说明以 [backend/README.md](../../backend/README.md) 为准：

```bash
cd backend
uv sync                # 按 uv.lock 安装依赖（不要手动 pip install）
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
```

DBOS 系统库初始化（如未自动创建）：`uv run dbos migrate`。

### 3. 控制台

完整说明以 [console/README.md](../../console/README.md) 为准：

```bash
cd console
npm install
npm run dev
```

## 环境变量

所有配置经环境注入（`backend/.env` 或进程环境），样例见根目录 `.env.example`；**真实密钥只放被 gitignore 的 `.env`，禁止入库**。

| 变量 | 用途 | 必填 |
|---|---|---|
| `DATABASE_URL` | 业务数据库连接（PostgreSQL） | 是 |
| `DBOS_SYSTEM_DATABASE_URL` | DBOS 系统库连接 | 是 |
| `SECRET_KEY` / `JWT_SECRET` | JWT 签名密钥 | 是 |
| `ENCRYPTION_KEY` | 原始 payload 加密密钥（AES-GCM） | 是 |
| `SHOPIFY_SHOP_DOMAIN` | Shopify 店铺域名 | 是 |
| `SHOPIFY_API_VERSION` | 固定 `2026-07`（禁止 latest） | 是 |
| `SHOPIFY_ADMIN_TOKEN` | Shopify Admin token | 是 |
| `SHOPIFY_WEBHOOK_SECRET` | webhook HMAC 校验密钥 | 是 |
| `ODOO_BASE_URL` | Odoo 19 地址 | 是 |
| `ODOO_API_KEY` | External JSON-2 bearer API key | 是 |
| `ODOO_DB` | Odoo 数据库名 | 是 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP 导出端点（可空） | 否 |

## 常用命令

```bash
uv run pytest                      # 测试（tests/）
uv run ruff check .                # 静态检查
uv run ruff format .               # 格式化
uv run alembic revision --autogenerate -m "<描述>"   # 生成迁移
uv run alembic upgrade head        # 应用迁移
uv run dbos migrate                # DBOS 系统表迁移
```

## 常见问题

- **端口占用**：8000（api）或 console 端口被占用时，先 `Get-NetTCPConnection -LocalPort 8000` 定位进程，或改用其他端口。
- **连接数据库失败**：确认 `docker compose up -d postgres` 已启动，`DATABASE_URL`/`DBOS_SYSTEM_DATABASE_URL` 与 compose 暴露端口一致。
- **迁移未跑**：启动 api 前必须 `alembic upgrade head`，否则表缺失报错。
- **Next.js 构建期联网**：`next/font/google` 等需要在构建期联网；离线环境需预置字体或允许构建期网络。
- **Windows 注意**：在 PowerShell 中以 `uv run` 前缀执行，避免依赖系统 Python 环境。

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

> 实测（2026-08-10）：worker（`python -m app.worker`）启动时 DBOS 会自动对系统库应用 schema 迁移（46+ 个迁移），
> 无需手工 `dbos migrate`；后者仅在需要特权手工迁移时使用。

### 3. 控制台

完整说明以 [console/README.md](../../console/README.md) 为准：

```bash
cd console
npm install
npm run dev
```

### 4. Odoo 19（P0 验证用，可选 profile）

```bash
cp .env.example .env   # 若尚未创建
docker compose --profile odoo up -d
docker exec commerce-odoo19 odoo -d odoo -i base --stop-after-init \
  --db_host=postgres --db_port=5432 --db_user=commerce --db_password=commerce
docker restart commerce-odoo19
```

- 管理员默认 `admin / ${ODOO_ADMIN_PASSWORD:-admin}`（仅开发环境）。
- 生成 External JSON-2 API key（必须挂在 active 用户下，shell 需显式 commit）：

```bash
docker exec -i commerce-odoo19 odoo shell -d odoo \
  --db_host=postgres --db_port=5432 --db_user=commerce --db_password=commerce \
  --no-http --log-level=warn <<'EOF'
key = env['res.users.apikeys'].with_user(2)._generate(None, 'commerce', None)
env.cr.commit()
print(key)
EOF
```

- JSON-2 请求必须带 `Authorization: bearer <key>` 与 `X-Odoo-Database: odoo` 头；create 用 `vals_list`、write 用 `ids`+`vals`（详见 ADR-0008 P0 实测结论）。
- 实测结论（2026-08-10）：Odoo 19 Community JSON-2 可用，`backend/app/connectors/odoo.py` 的 probe/create/update 已对真实容器端到端验证通过。

## 环境变量

所有配置经环境注入（`backend/.env` 或进程环境），样例见根目录 `.env.example`；**真实密钥只放被 gitignore 的 `.env`，禁止入库**。

| 变量 | 用途 | 必填 |
|---|---|---|
| `COMMERCE_DATABASE_URL` | 业务数据库连接（PostgreSQL） | 是 |
| `COMMERCE_DBOS_SYSTEM_DATABASE_URL` | DBOS 系统库连接 | 是 |
| `COMMERCE_JWT_SECRET` | JWT 签名密钥 | 是 |
| `COMMERCE_ENCRYPTION_KEY` | 原始 payload 加密密钥（Fernet） | 是 |
| `COMMERCE_SHOPIFY_SHOP_NAME` | Shopify 店铺名（开发店） | 连接器启用时 |
| `COMMERCE_SHOPIFY_API_VERSION` | 固定 `2026-07`（禁止 latest） | 是 |
| `COMMERCE_SHOPIFY_ACCESS_TOKEN` | Shopify Admin token | 连接器启用时 |
| `COMMERCE_SHOPIFY_WEBHOOK_SECRET` | webhook HMAC 校验密钥 | webhook 启用时 |
| `COMMERCE_ODOO_BASE_URL` | Odoo 19 地址 | Odoo 连接器启用时 |
| `COMMERCE_ODOO_API_KEY` | External JSON-2 bearer API key | Odoo 连接器启用时 |
| `COMMERCE_ODOO_DB` | Odoo 数据库名 | Odoo 连接器启用时 |
| `COMMERCE_OTLP_ENDPOINT` | OTLP 导出端点（可空） | 否 |
| `COMMERCE_RAW_PAYLOAD_RETENTION_DAYS` | 原始 webhook payload 保留天数（默认 30） | 否 |

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
- **连接数据库失败**：确认 `docker compose up -d postgres` 已启动，`COMMERCE_DATABASE_URL`/`COMMERCE_DBOS_SYSTEM_DATABASE_URL` 与 compose 暴露端口一致。
- **迁移未跑**：启动 api 前必须 `alembic upgrade head`，否则表缺失报错。
- **Next.js 构建期联网**：`next/font/google` 等需要在构建期联网；离线环境需预置字体或允许构建期网络。
- **Windows 注意**：在 PowerShell 中以 `uv run` 前缀执行，避免依赖系统 Python 环境。
- **Odoo JSON-2 401 Invalid apikey**：key 必须挂在 active 用户（如 admin id=2）下；`odoo shell` 默认 user 是 inactive 的 `__system__`，需 `with_user(2)` 且显式 `env.cr.commit()`。
- **Odoo "No database is selected"**：JSON-2 请求必须带 `X-Odoo-Database: odoo` 头。
- **Odoo 404 "the model ... does not exist"**：对应模块未安装（如 `product`），用 `odoo -d odoo -i <module> --stop-after-init ...` 安装后重启容器。

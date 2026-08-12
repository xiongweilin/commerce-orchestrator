# Runbook：本地开发环境

## 前置条件

- Python 3.12（见 `backend/.python-version`）、uv
- Node.js（Next.js 16.2.12 / React 19.2.8，需支持当前 LTS）
- Docker + Docker Compose（PostgreSQL 16、监控栈等依赖）
- 本仓库已 clone 到本地，分支 `main`

## 启动步骤

### 1. 完整栈（Compose，推荐联调/演示）

```bash
cp .env.example .env   # 首次必做；真实密钥只放被 gitignore 的 .env
docker compose up -d
```

启动顺序（依赖由 compose 表达，`db-bootstrap`/`migrate` 为一次性服务）：

```text
postgres healthy → db-bootstrap completed → migrate completed
→ api + worker（并行）→ api/worker ready
→ console + prometheus + grafana + alertmanager + alert-receiver + metabase
```

- `db-bootstrap`：幂等执行 `infra/postgres/bootstrap.sql`（最小权限角色引导）。
- `migrate`：以 `commerce_migrator` 角色执行 `uv run alembic upgrade head`；api/worker 启动时不自行改 schema。
- Odoo 19 默认不启动：`docker compose --profile odoo up -d`。
- 服务清单、健康检查、数据卷与端口见 [infra/README.md](../../infra/README.md)。

启动后访问：

| 服务 | 地址 |
|---|---|
| API（readyz） | http://localhost:8000/readyz |
| Console（BFF） | http://localhost:3200 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000（admin / `GRAFANA_ADMIN_PASSWORD`） |
| Alertmanager | http://localhost:9093 |
| Metabase | http://localhost:3201（业务库连接用 `commerce_readonly`，仅 SELECT） |
| Odoo 19（需 profile） | http://localhost:8069 |

### 2. 后端（本地开发，不经 Docker）

完整说明以 [backend/README.md](../../backend/README.md) 为准：

```bash
cd backend
uv sync                # 按 uv.lock 安装依赖（不要手动 pip install）
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000   # api
```

健康检查与运维端点：`GET /livez`（进程存活）、`GET /healthz`（/livez 别名）、
`GET /readyz`（数据库 + Alembic head + adapter 配置 + worker heartbeat，全绿 200 否则 503）、
`GET /v1/me`、`GET /v1/ops/inbox?status=failed`、`GET /v1/ops/runtime`（后三者需认证/角色）。

### 3. Worker（DBOS v2 + inbox relay）

```bash
cd backend
uv run python -m app.worker --help
uv run python -m app.worker            # 默认轮询 500ms、批次 50、lease 30s
uv run python -m app.worker --poll-interval-ms 200
```

要点：

- worker 启动时注册 v2 definitions + v1 legacy slice 并 `DBOS.launch()`；**bootstrap/DBOS launch 失败非零退出**（无 idle-loop 回退），供 compose healthcheck 判定。
- worker 心跳写入 `runtime_heartbeat`（upsert，`started_at` 不刷新）；`/livez` + `/metrics` 监听 9101（`COMMERCE_WORKER_METRICS_PORT`）。
- 主循环：回收过期 lease → inbox relay（`FOR UPDATE SKIP LOCKED` 认领 → DBOS start/send → processed；失败指数退避 ≤10 次进 failed）→ 指标快照 → 隐私清理（默认 24h 周期，`COMMERCE_PII_HASH_KEY` 未配置时跳过）。
- 停止 worker 超过 30 秒后 `/readyz` 的 worker 分项为 fail、`WorkerUnavailable` 告警触发；恢复后 pending inbox 自动继续（见 [worker-failure.md](worker-failure.md)）。

### 4. 控制台（Next.js BFF 安全会话）

完整说明以 [console/README.md](../../console/README.md) 为准：

```bash
cd console
npm install
npm run dev
```

- 客户端只访问同源 BFF（`/api/session`、`/api/me`、`/api/backend/[...path]`），不再向 `NEXT_PUBLIC_API_BASE` 直连发 token；JWT 不再入 `localStorage`。
- 会话 cookie `commerce_session`（HttpOnly / SameSite=Strict / 非 dev Secure / Max-Age=min(JWT TTL, 8h)）+ `commerce_csrf`（非 HttpOnly）；非 GET 请求需 `X-CSRF-Token` 与 `Origin` 校验（ADR-0014）。
- 服务端私有环境变量 `COMMERCE_API_BASE`（指向 `http://api:8000` 或本地 8000）；联调 mock `COMMERCE_SESSION_MOCK=1` 仅限开发（生产永不生效，联调后移除）。

### 5. Odoo 19（P0/P7 沙盒验证用，可选 profile）

```bash
cp .env.example .env   # 若尚未创建
docker compose --profile odoo up -d
docker exec commerce-odoo19 odoo -d odoo -i base --stop-after-init \
  --db_host=postgres --db_port=5432 --db_user=odoo_app --db_password=<odoo_app password from .env>
docker restart commerce-odoo19
```

- Odoo 使用独立数据库 `odoo` 与角色 `odoo_app`（与业务主库 `commerce` 隔离，不再使用 owner 角色 `commerce`）。
- 管理员默认 `admin / ${ODOO_ADMIN_PASSWORD:-admin}`（仅开发环境）。
- 生成 External JSON-2 API key（必须挂在 active 用户下，shell 需显式 commit）：

```bash
docker exec -i commerce-odoo19 odoo shell -d odoo \
  --db_host=postgres --db_port=5432 --db_user=odoo_app --db_password=<odoo_app password> \
  --no-http --log-level=warn <<'EOF'
key = env['res.users.apikeys'].with_user(2)._generate(None, 'commerce', None)
env.cr.commit()
print(key)
EOF
```

- JSON-2 请求必须带 `Authorization: bearer <key>` 与 `X-Odoo-Database: odoo` 头；create 用 `vals_list`、write 用 `ids`+`vals`（详见 ADR-0008 P0 实测结论）。

## 环境变量

所有配置经环境注入（`backend/.env` 或进程环境），样例见根目录 `.env.example`（**变量名单一事实来源**）；**真实密钥只放被 gitignore 的 `.env`，禁止入库**。

| 变量 | 用途 | 必填 |
|---|---|---|
| `COMMERCE_DATABASE_URL` | 业务数据库连接（本地裸机开发用 owner 角色） | 是 |
| `COMMERCE_API_DATABASE_URL` | api 进程最小权限连接（`commerce_api`） | compose 中 |
| `COMMERCE_WORKER_DATABASE_URL` | worker 进程最小权限连接（`commerce_worker`） | compose 中 |
| `COMMERCE_DBOS_SYSTEM_DATABASE_URL` | DBOS 系统库连接（`dbos_app`） | 是 |
| `COMMERCE_JWT_SECRET` | JWT 签名密钥 | 是 |
| `COMMERCE_ENCRYPTION_KEY` | sensitive_payload 加密密钥（Fernet） | 是 |
| `COMMERCE_INBOX_POLL_INTERVAL_MS` | worker 轮询间隔（默认 500） | 否 |
| `COMMERCE_INBOX_BATCH_SIZE` | 认领批次（默认 50） | 否 |
| `COMMERCE_INBOX_LEASE_SECONDS` | inbox lease（默认 30） | 否 |
| `COMMERCE_INBOX_MAX_ATTEMPTS` | 失败重试上限（默认 10） | 否 |
| `COMMERCE_EFFECT_MAX_RETRIES` | effect 重试上限（默认 3） | 否 |
| `COMMERCE_PII_HASH_KEY` | customer ref HMAC 密钥（`pii:` 伪匿名） | 隐私 job 启用时 |
| `COMMERCE_PRIVACY_RETENTION_DAYS` | 敏感 payload 保留天数（默认 30） | 否 |
| `COMMERCE_PRIVACY_CLEANUP_INTERVAL_HOURS` | 清理周期（默认 24） | 否 |
| `COMMERCE_CONSOLE_ORIGIN` | BFF Origin 校验允许值 | console 启用时 |
| `COMMERCE_SHOPIFY_SHOP_NAME` | Shopify 店铺名（开发店） | 连接器启用时 |
| `COMMERCE_SHOPIFY_API_VERSION` | 固定 `2026-07`（禁止 latest） | 是 |
| `COMMERCE_SHOPIFY_ACCESS_TOKEN` | Shopify Admin token | 连接器启用时 |
| `COMMERCE_SHOPIFY_WEBHOOK_SECRET` | webhook HMAC 校验密钥 | webhook 启用时 |
| `COMMERCE_ODOO_BASE_URL` / `COMMERCE_ODOO_API_KEY` / `COMMERCE_ODOO_DB` | Odoo JSON-2 连接 | Odoo 启用时 |
| `COMMERCE_OTLP_ENDPOINT` | OTLP 导出端点（可空） | 否 |

## 常用命令

```bash
uv run pytest                      # 测试（tests/；验收用 PostgreSQL 16）
uv run ruff check .                # 静态检查
uv run ruff format .               # 格式化
uv run alembic revision --autogenerate -m "<描述>"   # 生成迁移
uv run alembic upgrade head        # 应用迁移
uv run dbos migrate                # DBOS 系统表迁移（worker 启动会自动应用，一般无需手工）
docker compose up db-bootstrap     # 幂等重跑角色引导
docker compose up migrate          # 重跑迁移
```

## 常见问题

- **端口占用**：8000/3200/9090/3000/9093/3201/8069 被占用时，`Get-NetTCPConnection -LocalPort ...` 定位进程；监控栈宿主端口可用 `.env` 的 `PROMETHEUS_PORT`/`GRAFANA_PORT` 等覆盖。本机若已有同名全局监控容器，compose `container_name` 已加 `commerce-` 前缀规避命名冲突。
- **连接数据库失败**：确认 `docker compose up -d postgres` 已启动；compose 内数据库主机名为 `postgres`，`.env` 中 `localhost` 仅用于本地裸机。
- **迁移未跑 / `/readyz` alembic fail**：先 `docker compose up migrate` 或 `alembic upgrade head`；api/worker 启动时不自行改 schema。
- **`/readyz` worker fail**：worker 未运行或心跳超过 30 秒；启动 worker 或查看 [worker-failure.md](worker-failure.md)。
- **`/readyz` adapters fail**：`COMMERCE_SHOPIFY_*`/`COMMERCE_ODOO_*` 未配置完整，属 fail-closed（worker 不 ready 是预期行为）。
- **Next.js 构建期联网**：`next/font/google` 等需要构建期联网；离线环境需预置字体或允许构建期网络。
- **Windows 注意**：在 PowerShell 中以 `uv run` 前缀执行，避免依赖系统 Python 环境。
- **Odoo JSON-2 401 Invalid apikey**：key 必须挂在 active 用户（如 admin id=2）下；`odoo shell` 默认 user 是 inactive 的 `__system__`，需 `with_user(2)` 且显式 `env.cr.commit()`。
- **Odoo "No database is selected"**：JSON-2 请求必须带 `X-Odoo-Database: odoo` 头。
- **Odoo 404 "the model ... does not exist"**：对应模块未安装（如 `product`），用 `odoo -d odoo -i <module> --stop-after-init ...` 安装后重启容器。

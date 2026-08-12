# 运营控制台（Operations Console）

电商运营控制塔的**内部运营控制台**（Next.js 16 + TypeScript，App Router）。用于查看工作流、审批收件箱、对账差异、失败 inbox 运维，以及发起运营命令。后端为 FastAPI（默认 `http://localhost:8000`，API v1），通过同源 **BFF 安全会话**访问，不再直连后端或保存 JWT 到 localStorage。

## 技术栈

- Next.js 16.x（`output: "standalone"`，App Router，服务端组件 + 客户端表单）
- React 19.x、TypeScript（strict）
- 零额外运行时依赖：无 Tailwind、无 UI 库，样式为手写 CSS（`app/globals.css`，浅色主题 + 深色头部，中文界面）

## 快速开始

要求：Node.js >= 20.9（推荐 24）、npm 11。

```bash
npm install
npm run dev        # 开发模式 http://localhost:3000
npm run build      # 生产构建（输出 .next/standalone，供 Dockerfile 使用）
npm start          # 生产启动 http://localhost:3000
npm run gen:types  # 从后端 OpenAPI 生成 TypeScript 类型（lib/generated/openapi.ts）
```

> Windows 注意：本机 3001-3100 端口属于系统保留段（`netsh interface ipv4 show excludedportrange protocol=tcp`），
> 绑定会报 `EACCES`。开发时请用保留段之外的端口，例如 `npm run dev -- -p 3200` 或
> `npm start -- -H 127.0.0.1 -p 3200`。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `COMMERCE_API_BASE` | `http://localhost:8000` | 后端 FastAPI 地址（**服务器私有**，仅 BFF/服务端组件使用；客户端只访问同源 BFF） |
| `CONSOLE_ORIGIN` | 请求自身 origin | 允许的 console origin（BFF 非 GET 请求的 Origin 校验；默认取请求自身 origin） |
| `COMMERCE_SESSION_MOCK` | 未设置 | 仅开发模式（非 production）可用：`1` 时 POST /api/session 跳过后端 `/v1/me` 验证（WP6 联调后移除） |
| `OPENAPI_URL` | `<COMMERCE_API_BASE>/openapi.json` | `npm run gen:types` 拉取 OpenAPI 的完整地址 |

## 认证

采用 **Next.js BFF 安全会话**（整改计划 §四.3）：

- 右上角输入一次 JWT，`POST /api/session` 由 BFF 调后端 `/v1/me` 验证后，把 JWT 写入 **HttpOnly cookie**（`commerce_session`，`SameSite=Strict`、`Path=/`、非 dev 强制 `Secure`，Max-Age 不超过 JWT 剩余 TTL 且 ≤ 8 小时），同时生成非敏感 `commerce_csrf` cookie。
- 客户端组件一律访问同源 BFF `/api/backend[...]`，绝不接触 JWT；非 GET 请求携带 `X-CSRF-Token`（与 `commerce_csrf` cookie 一致）+ `Origin` 校验。
- 服务端组件经 `lib/server-auth.ts` 读取 HttpOnly 会话并直连 `COMMERCE_API_BASE`。
- `DELETE /api/session` 退出并清除两个 cookie。

> 后端 `/v1/me` 由 WP6 在 Wave 2 实现；未就绪时登录接口返回 `502 backend_not_ready`，开发模式可用 `COMMERCE_SESSION_MOCK=1` 临时跳过后端验证。

## 页面

| 路径 | 页面 | 主要 API |
| --- | --- | --- |
| `/` | 概览：工作流总数卡片、快捷入口、系统状态 | `GET /v1/workflows?limit=1`（读取 total） |
| `/workflows` | 工作流列表：状态筛选、刷新、分页，行链接到详情 | `GET /v1/workflows?status=&limit=&offset=` |
| `/workflows/[id]` | 工作流详情：状态头、当前步骤、版本、事件时间线、效果台账、工作项决策表单 | `GET /v1/workflows/{id}`、`POST /v1/work-items/{id}/decisions` |
| `/approvals` | 审批收件箱：待审批工作项卡片 + 内联批准/拒绝表单 | `GET /v1/work-items?status=pending` |
| `/reconciliations` | 对账：发起对账（带 Idempotency-Key）+ 运行列表 | `POST/GET /v1/reconciliations` |
| `/reconciliations/[runId]` | 对账详情：差异表（MANUAL_RECONCILIATION 高亮）+ 解决备注表单 | `GET /v1/reconciliations/{runId}`、`POST /v1/reconciliations/{runId}/diffs/{diffId}/resolve` |
| `/ops/inbox` | 运维收件箱：failed inbox 查看 + retry（仅 system_admin 可见导航） | `GET /v1/ops/inbox?status=failed`、`POST /v1/ops/inbox/{id}/retry` |
| `/commands` | 命令发起：类型选择 + JSON 负载 + 每次新的 Idempotency-Key，展示 202 结果 | `POST /v1/catalog-revisions`、`/v1/listing-publications`、`/v1/procurements`、`/v1/returns`、`/v1/reconciliations` |

概览页包含 worker / inbox / effect / reconciliation 四张健康卡片，数据源为 `GET /readyz` 与 `GET /v1/ops/runtime`（由 WP6 实现）。

## 目录结构

```text
console/
├── app/                     # App Router 页面（layout / 概览 / 工作流 / 审批 / 对账 / 命令 / ops）
│   ├── api/                 # BFF 路由：session / me / backend 代理（CSRF + Origin 校验）
│   ├── globals.css          # 全局样式（手写 CSS）
│   ├── workflows/[id]/     # 工作流详情
│   ├── reconciliations/[runId]/  # 对账详情
│   └── ops/inbox/           # failed inbox 查看 + retry（system_admin）
├── components/              # StatusBadge / ErrorBox / Loading / 表单 / 刷新按钮 / 会话管理 / 健康卡片
├── lib/
│   ├── api.ts               # fetch 封装：服务端直连 COMMERCE_API_BASE / 客户端走同源 BFF + CSRF
│   ├── types.ts             # API v1 契约类型（读模型按 api-contract.md；命令类型来自 generated）
│   ├── generated/openapi.ts # 由 scripts/gen-types.mjs 从 OpenAPI 生成（禁止手改）
│   ├── session.ts           # 会话 cookie 常量（客户端/服务端共用）
│   ├── session-server.ts    # 会话 cookie 读写 / CSRF / Max-Age 计算（服务端）
│   ├── format.ts            # 时间 / 短 ID / JSON 展示辅助
│   └── server-auth.ts       # 服务端从 HttpOnly 会话读取 JWT / 当前用户（fail-closed）
├── scripts/gen-types.mjs    # OpenAPI -> TypeScript 类型生成（确定性输出）
├── public/favicon.svg
├── package.json
├── package-lock.json        # 由 npm install 生成
├── tsconfig.json
├── next.config.ts           # output: "standalone"
└── next-env.d.ts
```

## 备注（实现时做的假设）

- 工作流状态、工作项状态、对账状态的取值以后端为准；未知状态显示原始字符串（中性灰徽章）。`MANUAL_RECONCILIATION` 会以紫色高亮徽章显示“人工对账”。
- 对账 `summary` 的差异数字段名未固定，兼容 `diffCount / diff_count / unmatched / mismatchCount / unresolved` 等命名，取不到时显示“—”。
- 工作流列表的状态筛选集合为计划 §二.1 的七个状态：`accepted / running / awaiting_approval / completed / needs_reconciliation / failed / cancelled`。
- 对账差异的 resolve 接口可能尚未接线：后端返回 404/405/501 或对应错误码时，页面显示“接口未就绪”提示而不是崩溃。
- 工作流事件契约字段为 `type`（不再使用 `eventType`）；审批决策提交 `expectedWorkflowVersion`（兼容读取 legacy `expectedVersion`）。
- 后端 `/v1/me`、`/readyz`、`/livez`、`/v1/ops/*` 由 WP6 在 Wave 2 实现；未就绪时 BFF 返回 502/404，页面显示「待 WP6 联调」。
- 所有数据页均为动态渲染（`force-dynamic`），构建时不请求后端；后端不可达时页面显示错误提示而不是构建失败。

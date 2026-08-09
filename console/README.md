# 运营控制台（Operations Console）

电商运营控制塔的**内部运营控制台**（Next.js 16 + TypeScript，App Router）。用于查看工作流、审批收件箱、对账差异，以及发起运营命令。后端为 FastAPI（默认 `http://localhost:8000`，API v1）。

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
```

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE` | `http://localhost:8000` | 后端 FastAPI 地址（构建时内联，修改后需重新 `npm run build`） |

## 认证

在右上角输入 Token：保存在浏览器 `localStorage`（键 `commerce_token`），并同步写入同名 cookie，供服务端组件在页面请求时携带。所有 API 调用自动附加 `Authorization: Bearer <token>`（客户端直接读取 localStorage；服务端组件读取 cookie）。

## 页面

| 路径 | 页面 | 主要 API |
| --- | --- | --- |
| `/` | 概览：工作流总数卡片、快捷入口、系统状态 | `GET /v1/workflows?limit=1`（读取 total） |
| `/workflows` | 工作流列表：状态筛选、刷新、分页，行链接到详情 | `GET /v1/workflows?status=&limit=&offset=` |
| `/workflows/[id]` | 工作流详情：状态头、当前步骤、版本、事件时间线、效果台账、工作项决策表单 | `GET /v1/workflows/{id}`、`POST /v1/work-items/{id}/decisions` |
| `/approvals` | 审批收件箱：待审批工作项卡片 + 内联批准/拒绝表单 | `GET /v1/work-items?status=pending` |
| `/reconciliations` | 对账：发起对账（带 Idempotency-Key）+ 运行列表 | `POST/GET /v1/reconciliations` |
| `/reconciliations/[runId]` | 对账详情：差异表（MANUAL_RECONCILIATION 高亮）+ 解决备注表单 | `GET /v1/reconciliations/{runId}`、`POST /v1/reconciliations/{runId}/diffs/{diffId}/resolve` |
| `/commands` | 命令发起：类型选择 + JSON 负载 + 每次新的 Idempotency-Key，展示 202 结果 | `POST /v1/catalog-revisions`、`/v1/listing-publications`、`/v1/procurements`、`/v1/returns`、`/v1/reconciliations` |

## 目录结构

```text
console/
├── app/                     # App Router 页面（layout / 概览 / 工作流 / 审批 / 对账 / 命令）
│   ├── globals.css          # 全局样式（手写 CSS）
│   ├── workflows/[id]/     # 工作流详情
│   └── reconciliations/[runId]/  # 对账详情
├── components/              # StatusBadge / ErrorBox / Loading / 表单 / 刷新按钮 / Token 输入
├── lib/
│   ├── api.ts               # fetch 封装：BASE URL、Bearer、Idempotency-Key、错误包解析
│   ├── types.ts             # API v1 数据类型
│   ├── format.ts            # 时间 / 短 ID / JSON 展示辅助
│   └── server-auth.ts       # 服务端从 cookie 读取 token
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
- 工作流列表的状态筛选值为常见猜测（pending/running/completed/failed/cancelled），与后端不一致时列表为空，可切回“全部状态”。
- 对账差异的 resolve 接口可能尚未接线：后端返回 404/405/501 或对应错误码时，页面显示“接口未就绪”提示而不是崩溃。
- 所有数据页均为动态渲染（`force-dynamic`），构建时不请求后端；后端不可达时页面显示错误提示而不是构建失败。

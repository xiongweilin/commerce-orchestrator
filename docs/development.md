# 开发与契约变更流程

## 1. 文档与契约是第一公民

- 全仓库文档默认中文；代码、路径、命令、事件类型、effect 操作、角色等英文 identifier 保持原样。
- `docs/contracts/` 是**全仓库唯一契约事实源**：api-contract、event-contract、data-ownership。后端 `backend/app`、控制台 `console`、测试必须与契约一致；契约与实现不一致时，以契约为准并修复实现。
- 共享词汇（事件类型、effect 操作、反馈类型、角色、审批边界）为不可变命名；新增或修改必须先走 ADR 与契约更新。

## 2. 契约变更流程

1. 若变更涉及架构/可靠性/安全取舍，先写 ADR（见下节）。
2. 更新对应契约文档（`docs/contracts/*`），保持字段、命名、错误码一致。
3. 同步实现：后端 schema/路由、控制台类型与表单、测试用例。
4. 按 ADR-0010 验收门禁执行故障与性能验证。
5. 由负责人评审：审批边界、四眼原则、幂等与对账语义变更需合规/财务确认。

## 3. ADR 流程

- 新决策在 `docs/adr/` 新建 `NNNN-<slug>.md`，编号顺延；格式：标题、Status（Accepted/Proposed/Superseded）、Date、Context、Decision、Consequences。
- 每个 ADR 必须写明权衡与替代方案；被替代的 ADR 标记 Superseded 并指向新编号。
- 变更技术栈中间件、外部系统集成方式、可靠性模型时必须新增 ADR。

## 4. 命名与代码约定

- 事件类型：`<domain>.<past_tense>`，见 [event-contract.md](contracts/event-contract.md)。
- effect 操作：`system.operation`（`shopify.*` / `odoo.*`），见 event-contract。
- 角色：11 类固定角色（catalog_owner、commerce_lead、finance_approver、procurement_lead、budget_owner、warehouse_staff、inventory_supervisor、accountant、customer_service、compliance、system_admin）。
- 审批边界与四眼原则在服务端强制，不依赖前端（见 data-ownership.md）。

## 5. 质量与测试入口

- 静态检查与格式：`uv run ruff check .`（配置见 `backend/pyproject.toml`）。
- 单元/集成测试：`uv run pytest`（tests 目录；asyncio_mode=auto）。
- 数据库迁移：`uv run alembic revision --autogenerate -m "<描述>"`，随后 `uv run alembic upgrade head`。
- 故障注入与性能门禁：按 ADR-0010 执行（kill injection、重放、p95/p99 压力测试）。

## 6. 本地命令速查

详细步骤见 [runbooks/dev-environment.md](runbooks/dev-environment.md)：

```bash
docker compose up -d postgres   # 启动依赖
cd backend && uv sync           # 安装后端依赖
uv run alembic upgrade head     # 迁移
uv run uvicorn app.main:app --reload   # 启动 api
cd ../console && npm install && npm run dev   # 启动控制台
```

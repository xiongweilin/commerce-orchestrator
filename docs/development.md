# 开发与契约变更流程

## 1. 文档与契约是第一公民

- 全仓库文档默认中文；代码、路径、命令、事件类型、effect 操作、角色等英文 identifier 保持原样。
- `docs/contracts/` 是**全仓库唯一契约事实源**：api-contract、event-contract、data-ownership。后端 `backend/app`、控制台 `console`、测试必须与契约一致；OpenAPI schema 是前后端接口的机器可读契约源（控制台经 `npm run gen:types` 生成类型）。P7 整改期以代码为准同步文档：发现不一致时修复文档并记录。
- 共享词汇（事件类型、effect 操作、反馈类型、角色、审批边界）为不可变命名；新增或修改必须先走 ADR 与契约更新。

## 2. 契约变更流程

1. 若变更涉及架构/可靠性/安全取舍，先写 ADR（见下节）。
2. 更新对应契约文档（`docs/contracts/*`），保持字段、命名、错误码一致。
3. 同步实现：后端 schema/路由、控制台类型与表单、测试用例；控制台类型由 `cd console && npm run gen:types` 生成，CI 用 `git diff --exit-code` 校验。
4. 按 ADR-0010 验收门禁执行故障与性能验证；完成后跑 `rg` identifier sweep（清理 legacy/stub/旧字段名与错误端口引用）。
5. 由负责人评审：审批边界、四眼原则、幂等与对账语义变更需合规/财务确认。

## 3. ADR 流程

- 新决策在 `docs/adr/` 新建 `NNNN-<slug>.md`，编号顺延（当前 0001–0014）；格式：标题、Status（Accepted/Proposed/Superseded）、Date、Context、Decision、Consequences。
- 每个 ADR 必须写明权衡与替代方案；被替代的 ADR 标记 Superseded 并指向新编号。
- 变更技术栈中间件、外部系统集成方式、可靠性模型时必须新增 ADR。
- 历史 ADR 不删除；新 ADR 在其正文中明确 supersede 旧 ADR 中被修正的部分（如 ADR-0011 supersede ADR-0005 的 inbox 实现部分）。

## 4. 命名与代码约定

- 事件类型：`<domain>.<past_tense>`，见 [event-contract.md](contracts/event-contract.md)。
- effect 操作：`system.operation`（`shopify.*` / `odoo.*`），见 event-contract。
- 角色：11 类固定角色（catalog_owner、commerce_lead、finance_approver、procurement_lead、budget_owner、warehouse_staff、inventory_supervisor、accountant、customer_service、compliance、system_admin）。
- 审批边界与四眼原则在服务端强制，不依赖前端（见 data-ownership.md）。

## 5. 质量与测试入口

- 静态检查与格式：`uv run ruff check .`（配置见 `backend/pyproject.toml`）。
- 单元/集成测试：`uv run pytest`（tests 目录；asyncio_mode=auto）。CI/验收使用 **PostgreSQL 16**（禁止只靠 SQLite）：从空库执行全部 Alembic migration、从 0002 fixture 升级到新 head、并发幂等/审批/inbox `SKIP LOCKED`/cleanup 集成用例。
- DBOS 集成测试：command API → inbox → DBOS workflow 自动启动；`workflow.accepted` 重放去重；decision 早于 `DBOS.recv` 可收到；worker 在各阶段被 kill 后可恢复；缺 adapter 时 readiness 失败；`outcome_unknown` 不进入 retry；definite retryable 最多 3 次。
- Adapter contract 测试：每个 `EFFECT_OPS` 都有生产 adapter 参数模型与测试 adapter；集合不一致时测试失败（`validate_effect_parameter_coverage`）。
- 数据库迁移：`uv run alembic revision --autogenerate -m "<描述>"`，随后 `uv run alembic upgrade head`。
- 故障注入与性能门禁：按 ADR-0010 执行（kill injection、重放、p95/p99 压力测试）。

## 6. 本地命令速查

详细步骤见 [runbooks/dev-environment.md](runbooks/dev-environment.md)：

```bash
docker compose up -d            # 全栈：postgres → db-bootstrap → migrate → api+worker → console+监控
cd backend && uv sync           # 安装后端依赖
uv run alembic upgrade head     # 迁移（compose 由 migrate 服务以 commerce_migrator 执行）
uv run uvicorn app.main:app --reload   # 启动 api（/livez、/readyz、/healthz、/v1/me、/v1/ops/*）
uv run python -m app.worker --help     # worker：inbox relay + DBOS v2 + 9101 /metrics
cd ../console && npm install && npm run dev   # 启动控制台（BFF 会话）
```

详细启动顺序与环境变量见 [runbooks/dev-environment.md](runbooks/dev-environment.md) 与 `infra/README.md`。

# 开发与契约变更流程

## 1. 文档、代码与契约

- 仓库 prose 文档默认中文或英文均可，但代码、路径、命令、事件类型、effect 操作、角色等 identifier 保持英文原名。
- `docs/contracts/` 保存规范性契约：API、事件、data ownership，以及 Commerce responsibility specialization/compatibility。
- FastAPI OpenAPI 是前后端 HTTP schema 的机器可读事实源；控制台通过 `npm run gen:types` 生成类型。
- `docs/current-implementation.md` 是“当前代码已经实现了什么”的人工可读快照；它不替代 ADR 或规范性 contract。
- 当实现与 prose 文档漂移时，先判断是实现违反既定 contract，还是文档落后于已接受实现。若是后者，应在同一变更中同步文档。

当前契约目录至少包括：

- `api-contract.md`
- `event-contract.md`
- `data-ownership.md`
- `responsibility-alignment.md`
- `responsibility-compatibility.toml`

## 2. 契约变更流程

1. 若变更涉及架构、可靠性、安全、authority、responsibility 或 fact ownership 取舍，先确认是否需要 ADR。
2. 更新对应 `docs/contracts/*`，保持字段、命名、错误码和负向不变量一致。
3. 同步实现：后端 schema/路由/服务、控制台类型与表单、测试。
4. 若修改 FastAPI contract，执行 `cd console && npm run gen:types` 并检查生成 diff。
5. 更新 `docs/current-implementation.md`、README/子目录 README 中受影响的当前能力描述。
6. 执行 identifier sweep，清理 legacy/stub/旧字段名和已失效的 endpoint/port 引用。
7. 执行与变更风险匹配的测试与 acceptance checks。

## 3. ADR 流程

- 新决策在 `docs/adr/` 新建 `NNNN-<slug>.md`，编号顺延；当前已有 ADR-0001 至 ADR-0015。
- 格式至少包含：标题、Status、Date、Context、Decision、Consequences。
- 被替代的历史 ADR 不删除；在旧 ADR 标记 superseded 范围，并在新 ADR 中说明替代边界。
- 变更 durable workflow 主线、外部 effect 语义、fact ownership、authority model、responsibility semantic 或外部系统集成方式时，应优先新增 ADR，而不是只改 README。

## 4. 当前不可破坏的语义边界

- AI candidate != Decision。
- ExperienceUseAdmission.allowed != Decision。
- Decision != ExecutionAuthorization。
- PublicationQualification.qualified != Authorization。
- EffectLedger.succeeded != ConfirmedOutcome。
- `outcome_unknown` 不自动重发。
- reconciliation disposition != verified repair。
- `WorkflowRun.completed` != universal responsibility discharge。
- console/client-side inference != execution authority。
- discharged responsibility obligation != deleted historical fact。

这些边界以 `docs/contracts/responsibility-alignment.md` 为准。

## 5. 命名与代码约定

- 事件类型：`<domain>.<past_tense_or_explicit_action>`，完整清单见 `event-contract.md`。当前 workflow 特例包括 `workflow.completion_recheck_requested`。
- effect 操作：`system.operation`（`shopify.*` / `odoo.*`）。
- 角色：11 类固定角色：`catalog_owner`、`commerce_lead`、`finance_approver`、`procurement_lead`、`budget_owner`、`warehouse_staff`、`inventory_supervisor`、`accountant`、`customer_service`、`compliance`、`system_admin`。
- approval boundary、four-eyes、authorization profile 与 dispatch eligibility 均由服务端强制，前端只展示/请求。
- work item 若存在 server-owned `authorizationProfile`，approve 必须走 `authorized-decisions`；普通 `decisions` 不能绕过该 profile。

## 6. 质量与测试入口

后端：

```bash
cd backend
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run alembic upgrade head
```

当前迁移链已到 `0010_responsibility_workflow_refs.py`；不要再假设 `0001_initial.py` 创建全部当前表。

需要重点覆盖的测试语义：

- command API → inbox relay → DBOS workflow；
- `workflow.accepted` 重放去重；
- decision 早于 `DBOS.recv` 仍可 durable 收到；
- authorization-required approval 无法通过普通 decision endpoint 绕过；
- authorization exact subject/version/fingerprint/operation/environment revalidation；
- worker kill/restart 后恢复；
- `outcome_unknown` 不进入自动 retry；
- definite retryable effect 最多按配置重试；
- realization verification 与 ConfirmedOutcome 分离；
- reconciliation 缺 reader/未检查域不能报告成功；
- responsibility inspector CURRENT/HISTORICAL 不互相升级；
- discharged obligation 仍保留历史；
- bounded completion blocked 后仅通过 durable recheck 重新评估。

控制台：

```bash
cd console
npm install
npm run gen:types
npm run build
```

生成的 `lib/generated/openapi.ts` 不手工修改。

## 7. 本地命令速查

```bash
docker compose up -d

cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload

# 另一个终端
cd backend
uv run python -m app.worker

# 控制台
cd console
npm install
npm run dev
```

API probes：`/livez`、`/readyz`、`/healthz`、`/metrics`。worker 暴露自身 liveness/metrics（默认 metrics port 9101）。

详细环境、备份、对账、worker failure、privacy cleanup 和 alerting 操作见 `docs/runbooks/`。

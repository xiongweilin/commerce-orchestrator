# Runbook：worker 退出 / 心跳丢失处置

## 目标

worker 承载 inbox relay、DBOS v2 工作流、effect 执行、对账与隐私清理；worker 异常退出或心跳丢失会阻塞新流程推进并触发告警。本 runbook 覆盖检测、定位、恢复与验证。

## 1. 检测与症状

| 症状 | 信号 |
|---|---|
| `WorkerUnavailable` 告警 | `time() - commerce_worker_heartbeat_timestamp_seconds > 30` 持续 30 秒（critical） |
| API readiness 不过 | `GET /readyz` → `503`，`checks.worker.status="fail"`（“no worker heartbeat recorded”或“stale”） |
| 运行时快照 | `GET /v1/ops/runtime` → `worker.status="down|warn"` |
| compose 健康检查 | `docker compose ps` 显示 worker `unhealthy` 或 `exited` |
| 流程停滞 | pending inbox 事件不再被消费（`/v1/ops/inbox?status=pending` 增长、`InboxBacklog` 告警） |

## 2. 常见根因

- **bootstrap/DBOS launch 失败**：adapter 配置缺失、DBOS 注册/启动异常——worker 按设计**非零退出**（无 idle-loop 回退），这是 fail-closed 行为，不是 bug。
- 进程崩溃/OOM/被杀；容器重启策略未生效。
- 数据库不可达（`COMMERCE_WORKER_DATABASE_URL` 配错、postgres 未就绪）。
- 迁移未执行：schema 与代码不匹配导致启动期异常。

## 3. 定位

```bash
docker compose ps worker                          # 状态 / 退出码
docker compose logs -f --tail=200 worker          # 找 worker_bootstrap_failed / worker_iteration_failed
curl -s http://localhost:8000/readyz              # 看 worker 分项与其它分项
curl -s -H "Authorization: Bearer <jwt>" http://localhost:8000/v1/ops/runtime
```

退出码非零时重点看 `worker_bootstrap_failed` 日志（配置/DBOS）；退出码 0 却心跳停止多为外部原因（网络/数据库/容器调度）。

## 4. 恢复

1. **修复根因**（配置/迁移/资源），再重启：

   ```bash
   docker compose restart worker        # 或 docker compose up -d worker
   ```

2. **自动恢复项**（无需手工）：
   - worker 启动时回收 lease 已过期的 `processing` 项（回到 `pending`，不涨 attempts）；
   - DBOS 从最后完成的步骤恢复 workflow，step 至少一次、事务步骤恰好一次；
   - 重复启动/send 被确定性 workflow id / decision id 幂等去重，不产生第二条业务流程或重复 effect。
3. **failed inbox 处置**（若存在 `failed` 事件）：
   - `GET /v1/ops/inbox?status=failed` 查看（仅 system_admin）；
   - `POST /v1/ops/inbox/{id}/retry`（**必须带 `Idempotency-Key`**）重置为 `pending` 重投；
   - 反复 failed 的事件按 `lastError` 修复根因后再 retry。
4. **等待 pending backlog 消化**：worker 默认 500ms 轮询、批次 50，恢复后自动继续处理；`InboxBacklog`/`FailedInbox` 告警随之恢复。

## 5. 恢复后验证

- `GET /readyz` → 200（worker heartbeat ≤ 30s）。
- `GET /v1/ops/runtime` → `worker.status="ok"`；inbox pending/failed 回落。
- 跑一次对账：每个必需域 `checked > 0`、`skippedDomains=[]`，无新增 diff（证明无重复/丢失 effect）。
- 观察 `commerce_workflow_recoveries_total` 与 effect 账本无重复 remote reference。

## 6. 预防

- worker 独立 Docker healthcheck（主进程存活 + postgres 可达）+ `WorkerUnavailable` 告警（30s 阈值，与 `/readyz` 一致）。
- 每轮整改按 ADR-0010 kill injection 门禁验证恢复（worker 在 transaction 前/effect 前/effect 后/结果落库前被 kill 均可恢复）。
- 配置变更（Shopify/Odoo adapter、DB URL）走 compose + `.env.example` 同步，避免启动期 fail-closed。

## 关联

- [alerting.md](alerting.md)：`WorkerUnavailable` / `InboxBacklog` / `FailedInbox`
- [dev-environment.md](dev-environment.md)：worker 启动与环境变量
- [reconciliation-drift.md](reconciliation-drift.md)：恢复后对账验证

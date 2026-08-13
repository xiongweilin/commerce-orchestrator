# Runbook：告警目录与处置入口

## 目标

汇总试验环境全部告警的条件、级别与处置 runbook；每条告警必须能链接到具体处置文档。试验环境 Alertmanager 只投递到仓库内本地 alert-receiver（记录 alert 名称/级别/runbook URL，**不记录业务 payload**）；外部通知渠道（邮件/Slack/PagerDuty 等）当前不启用。

## 告警总表

规则文件：`infra/prometheus/rules/alerts.yml`；指标名见 WP3-REPORT 指标契约（未落地前规则无数据、不误报）。

| 告警 | 条件 | 级别 | 处置 runbook |
|---|---|---|---|
| `WorkerUnavailable` | worker heartbeat 超过 30 秒 | critical | [worker-failure.md](worker-failure.md) |
| `InboxBacklog` | inbox 最老事件年龄超过 120 秒 | warning | [worker-failure.md](worker-failure.md) |
| `FailedInbox` | failed 数量 > 0 | warning | [worker-failure.md](worker-failure.md) |
| `OutcomeUnknown` | 任一资金/库存 effect 出现 `outcome_unknown` | critical | [reconciliation-drift.md](reconciliation-drift.md) |
| `ApiErrors5xx` | 5xx 占比 > 1% 持续 5 分钟 | warning | [dev-environment.md](dev-environment.md) |
| `ApiLatencyP99` | p99 > 500ms 持续 10 分钟 | warning | [dev-environment.md](dev-environment.md) |
| `ReconciliationIncomplete` | failed/skipped 域 > 0 | warning | [reconciliation-drift.md](reconciliation-drift.md) |
| `ReconciliationDrift` | 未解决 diff > 0 | warning | [reconciliation-drift.md](reconciliation-drift.md) |
| `CleanupOverdue` | 过期敏感 payload 超过 24 小时未删除 | warning | [privacy-cleanup.md](privacy-cleanup.md) |
| `ApprovalExpiryRisk` | pending work item 超过 29 天（临近 30 天审批到期） | warning | [worker-failure.md](worker-failure.md)（审批积压处置见下） |

> 说明：`ApprovalExpiryRisk` 规则当前 `runbook_url` 指向 `docs/runbooks/approval-expiry.md`
> （WP3 编写规则时的占位，该文件不在 WP8 清单内）；试验环境联调时由 root 决定改为
> 本文件 `alerting.md` 或新建专用文件。审批积压通用处置：通知项目所有者，逐条评估
> pending work item（30 天超期自动取消语义，DBOS.recv 超时取消），必要时人工审批
> 或取消；清理积压后告警自动恢复。

## 处置流程（通用）

1. 收到告警：在 alert-receiver 日志确认 `alertname`/`severity`/`runbook_url`/`starts_at`。
2. 按上表打开对应 runbook 执行定位与恢复。
3. 处置后验证指标回落（Grafana 看板或 `curl /metrics`），告警自动恢复（Alertmanager `send_resolved=true`）。
4. 记录：时间、告警名、根因、处置动作、验证结果（台账/REPORT，不含业务 payload）。

## 本地 alert-receiver

- 端点：`POST /alert`（Alertmanager webhook）；`GET /healthz`（存活）。
- 记录：alertname、severity、runbook_url、status、starts_at → stdout + 命名卷 `alert-logs`。
- **不记录**业务 payload、原始请求体、PII、token。
- 查看：

  ```bash
  docker compose logs -f alert-receiver
  # 或查看卷内日志目录（alert-logs）
  ```

## 关联

- [worker-failure.md](worker-failure.md) / [reconciliation-drift.md](reconciliation-drift.md) / [privacy-cleanup.md](privacy-cleanup.md) / [dev-environment.md](dev-environment.md)
- `infra/prometheus/rules/alerts.yml`、`infra/alertmanager/alertmanager.yml`、`infra/alert-receiver/`

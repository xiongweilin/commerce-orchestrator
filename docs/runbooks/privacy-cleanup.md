# Runbook：敏感数据保留清理（30 天）与审计

## 目标

按默认策略把 raw webhook、敏感 shipping/customer payload 统一保留 **30 天**，到期后安全清理并留下 tombstone 与审计证据；任何路径不得输出明文 PII（ADR-0014）。

## 1. 机制

- **存储**：`sensitive_payload` vault（Fernet 加密，密钥 `COMMERCE_ENCRYPTION_KEY`）；`expires_at = 写入时间 + privacy_retention_days`（默认 30）。
- **伪匿名**：需要匹配但无需还原的 customer ref 用 `COMMERCE_PII_HASH_KEY` HMAC（`pii:` 前缀）；workflow input / outbox payload 只保存最小字段与 `sensitivePayloadId`。
- **清理 job**：worker 内每日执行（`privacy_cleanup_interval_hours` 默认 24）；`COMMERCE_PII_HASH_KEY` 未配置时跳过。
- **backfill**：对旧 `SalesOrder.customer_ref`、shipping JSON、`ReturnCase.customer_ref` 加密替换（幂等，已带 `pii:`/`sensitivePayloadId` 的行跳过）。

## 2. 正常执行路径

```text
到期检查（expires_at ≤ now 且 deleted_at IS NULL）
→ 先清 ciphertext（置空）
→ 再写 tombstone（deleted_at = now）
→ 记录指标 cleanup_deleted_total / cleanup_errors_total / cleanup_overdue_age_seconds
→ 审计日志（数量与年龄，不含内容）
```

## 3. 告警与处置

| 告警 | 条件 | 处置 |
|---|---|---|
| `CleanupOverdue` | 最老过期 payload 超过 24 小时未删除 | 见下 |

1. 确认 worker 运行（[worker-failure.md](worker-failure.md)）；清理 job 随 worker 主循环执行，worker 停机会导致积压。
2. 检查指标：`cleanup_overdue_age_seconds`、`cleanup_errors_total`、`cleanup_deleted_total`。
3. 若 `cleanup_errors_total` 增长：查看审计日志（不含内容），定位失败行（如单行异常）；修复后 job 自动重试。
4. 若 worker 正常但未清理：核对 `privacy_retention_days`/`privacy_cleanup_interval_hours` 配置与 `expires_at` 是否写入。

## 4. 手工触发与验证

```bash
# 重启 worker 或等待周期；确认 job 输出：
docker compose logs -f --tail=100 worker | Select-String "worker_privacy_cleanup"
# 只读抽查（不得输出明文；仅看元数据）：
psql "$COMMERCE_DATABASE_URL" -c "SELECT count(*) FROM sensitive_payload WHERE deleted_at IS NOT NULL;"
psql "$COMMERCE_DATABASE_URL" -c "SELECT count(*) FROM sensitive_payload WHERE expires_at <= now() AND deleted_at IS NULL;"
```

验证点：
- 过期行已置 `deleted_at` 且 `ciphertext` 为空；
- 日志/trace/指标中无明文 PII、原始请求体、地址、邮箱或 token；
- 审计记录含删除数量、失败数量、最老过期年龄。

## 5. 明文 PII 清空（不可逆操作，需单独批准）

计划三.1 第 7 步的“清空旧明文列”（如 `sales_order.customer_ref` 明文列在 backfill 后仍保留的副本）**不可逆**：

1. 前提：新代码稳定、备份验证通过、回滚期结束；
2. 执行前创建并验证最小回滚备份（见 [backup-restore.md](backup-restore.md)），并记录台账；
3. 执行需**单独明确批准**（命名动作与受影响资源）；
4. 执行后验证新旧记录数量、解密可读性、引用完整性，更新台账后删除回滚副本。

## 关联

- [alerting.md](alerting.md)：`CleanupOverdue`
- [data-ownership.md](../contracts/data-ownership.md) §5：字段契约与保留
- ADR-0014：BFF 会话与敏感 payload

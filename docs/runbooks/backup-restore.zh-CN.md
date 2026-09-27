# Backup / Restore runbook

> 权威英文原文：[backup-restore.md](backup-restore.md)。

恢复目标是重新建立业务状态、workflow state、effect identity 与外部现实的一致性。

1. 记录 deployment/version 和恢复范围。
2. 停止或隔离继续写入的 worker。
3. 验证 backup 来源和时间点。
4. 恢复数据库/持久状态。
5. 检查 migration/schema compatibility。
6. 启动后先读取状态，不立即重放外部 effect。
7. 对恢复点附近 in-flight / unknown operation 做 reconciliation。
8. 验证 health、关键 invariant 和外部 authoritative state。

Restore 不会把历史 unknown effect 变成 retry permission。

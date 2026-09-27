# Privacy cleanup runbook

> 权威英文原文：[privacy-cleanup.md](privacy-cleanup.md)。

清理必须区分 canonical data、derived data、日志、缓存、backup 和外部系统副本。

1. 明确 subject、scope 和需要删除或遮蔽的数据类型。
2. 找到每类数据的 authoritative owner。
3. 对 canonical data 执行有边界的删除或匿名化。
4. 清理 index/cache/derived projection，并确保它们可从清理后的 canonical state 重建。
5. 检查日志和 artifact 是否仍保存被清理值。
6. Backup 按既定 retention/restore 规则处理；实时删除不代表历史 backup 同时消失。
7. 外部系统需要单独确认实际状态。
8. 记录清理依据和验证结果，但不把已清理内容重新写入审计记录。

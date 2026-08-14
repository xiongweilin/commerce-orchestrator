# N=60 校验集复核说明

待审核文件：`data/manual-audit/holdout-audit.csv`。

逐行确认：

1. 工单正文是否自然表达该问题。
2. `gold_problem_type` 是否与正文一致。
3. `gold_product_area` 是否与正文一致。
4. `gold_issue_family` 是否确实表示同一重复问题。
5. `gold_escalation` 是否与影响信号一致。

填写：

- `audit_label_text_consistent`：`yes` 或 `no`。
- `audit_notes`：不一致原因与修正建议。
- `auditor`：审核者名字或稳定代号。

## 当前状态

数据版本 `v3-natural-singletons` 已完成 60/60 条 AI 辅助候选一致性复核，复核范围包括文本与标签、一次性问题可区分性、显式影响信号、确定性责任路由、严重度与升级策略。该结果不能称为独立人工审计，也不能证明真实业务有效性。

V2 配套的 V4 以及后续 V5、V6 锁定集均已完成 AI 辅助一致性复核和历史回放，且已被读取。V7 使用全新的 `v7-frozen-signature-clustering-holdout-20260702`，也完成 60/60 条 AI 辅助一致性复核并评分。所有审核文件都必须明确标注“AI 辅助、非独立人工审计”；任何已读锁定集都不能再作为调参后的未见评测集。

若后续由人员独立复核，应使用稳定审核者代号覆盖 `auditor`，按实际判断填写 `yes`/`no`，并保留所有不一致记录；不得以本次 AI 辅助结果代替人工判断。

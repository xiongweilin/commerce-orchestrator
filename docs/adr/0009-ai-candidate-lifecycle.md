# ADR-0009：AI 候选生命周期

- **Status**: Accepted
- **Date**: 2026-08-10

## Context

AI 用于从反馈聚类生成改进建议；若 AI 可直接修改业务数据或自动执行，将绕过审批边界与四眼原则，且建议过程不可审计。

## Decision

- **候选状态机**：`draft → candidate → frozen → scored → official | rejected → deprecated`。
- **冻结（frozen）后不可修改原候选**：只能生成新候选或进入审批；修改需求通过新候选表达。
- **AI 只生成建议，不批准、不执行**：批准与执行必须由人工按审批边界完成（data-ownership.md）。
- **每个候选必须保存**：`sourceRefs`（证据引用）、`sourceRevision`、`sanitizerVersion`、`modelId`、`promptVersion`、`ruleVersion`、`proposalHash`、证据位置（evidence locations）、审核人（approver）、最终决策（approved/rejected + reason）。
- 事件映射：`feedback.candidate_created` / `feedback.reviewed` / `feedback.promoted` / `feedback.rejected`。

## Consequences

**正面**：建议可复现、可审计；审批边界不被绕过；模型/规则升级可追溯到每个候选。

**负面/约束**：候选生成成本（元数据齐全）；冻结机制带来版本管理复杂度；AI 质量依赖证据采集完整度。

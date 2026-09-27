# ADR 0016：Listing Integrity Steward 持久责任实验

[English](0016-listing-integrity-steward-experiment.md) | [简体中文](0016-listing-integrity-steward-experiment.zh-CN.md)

- 状态：Experimental
- Milestone：C20
- 范围：不做 schema migration，不增加 effect authority，不改变既有 listing publication authorization profile。

## 背景

C16–C19 已在 bounded workflow 内建立 current-use qualification、Decision 与 ExecutionAuthorization 分离、effect realization、ConfirmedOutcome、open/discharged obligation，以及非权威 responsibility inspector。

新的问题是：**一次 workflow 完成后，谁继续拥有长期责任？** Listing integrity 是持续性 concern；一次 diagnosis/repair 成功并不意味着组织可以永久停止关注未来 drift。

## 决策

在 `backend/app/experiments/` 引入非 canonical 的 `Listing Integrity Steward` 实验。

Standing mission：

> 依据当前 qualified catalog state 维持 Shopify listing integrity。

候选流程：

```text
Standing mission
→ Commerce catalog facts + current publication qualification
→ Shopify readback evidence
→ Situation assessment
→ non-authority-bearing work proposal
→ bounded resource commitment
→ read-only diagnosis / evidence preparation
→ 若需要 mutation：沿用 human Decision -> ExecutionAuthorization
→ existing Effect -> realization -> ConfirmedOutcome
→ reassess mission
→ mission remains active
```

## 待证伪的 responsibility cut

- observation != situation assessment；
- situation assessment != work proposal；
- work proposal != resource commitment；
- commitment != ExecutionAuthorization；
- resource allocation != external-effect authority；
- no observed failure != verified health；
- diagnostic Work completed != standing mission discharged；
- standing responsibility != permanent authority。

## Fact ownership 与 authority

Catalog revision/publication qualification 仍由 Commerce 持有；Shopify readback 由 Shopify fact owner 提供；Steward 只拥有 projection、assessment、proposal 和 bounded commitment；现有 workflow/effect/reconciliation owner 不变。

所有新 experimental record 都是 `authority_bearing = false`。Steward 可以在 resource envelope 内自主 diagnosis/prepare evidence，但不能 approve publication、mint ExecutionAuthorization、把 resource allocation 当 external effect permission、绕过 current qualification，或把 proposed repair 当 realized repair。

需要外部 listing mutation 时，进入 `HUMAN_DECISION_REQUIRED`，复用现有 Commerce responsibility chain。

## Resource governance 与 promotion

`StewardResourceRequest` / `StewardResourceEnvelope` 显式表示 API call、compute、human-attention 等资源承诺，并与 effect authorization 分离。

实验类型不会因单域成功自动 canonicalize。只有在独立 domain 中反复出现 collapse counterexample，证明合并 responsibility position 会导致 unsafe shortcut、historical ambiguity 或 unverifiable state，才允许 promotion。

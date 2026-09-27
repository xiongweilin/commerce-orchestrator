# Commerce Responsibility Alignment Contract

[English](responsibility-alignment.md) | [简体中文](responsibility-alignment.zh-CN.md)

本文件是 Commerce responsibility specialization contract 的中文版。

通用 responsibility 语义由 portable contract 持有；Commerce 只持有自己的 domain mapping 和 fact ownership。DBOS 继续是 durable workflow substrate，不被 responsibility layer 替代。

必须保持以下边界：

- candidate 不等于 Decision；
- historical reliance 不等于 current qualification；
- Decision 不等于 execution authorization；
- qualification 不等于 authorization；
- effect execution success 不等于 ConfirmedOutcome；
- unknown outcome 不等于自动重试权限；
- repair disposition 不等于 verified repair；
- workflow completion 不等于 universal responsibility discharge；
- UI/client inference 不产生 execution authority。

Effect dispatch 需要当前 subject/version/fingerprint、适用的 Experience-use state、明确 Decision、匹配且当前有效的 execution authorization、current Commerce qualification，以及不存在 blocking obligation。任何一个输入都不能替代其他输入。

Responsibility Inspector 只提供非权威 projection：historical 部分保留当时实际依赖；currentResponsibility 重新评估当前资格。

Completion 永久使用 declared-scope-only 覆盖声明。Recheck 只触发 durable reevaluation，不创造证据，也不直接完成 workflow。历史 approval、effect 或 reconciliation record 不会被 retroactively 升级成更强语义。

# Worker failure runbook

> 权威英文原文：[worker-failure.md](worker-failure.md)。

Worker 异常时先区分 durable workflow state 与 process state。

- Process crash 不代表业务 operation 没发生。
- 已提交 durable step 可以由新 worker 恢复；外部副作用仍依赖 idempotency/reconciliation。
- Started/unknown provider call 不能因为 worker restart 直接重复发送。
- 检查 workflow state、effect identity、provider reference 和 authoritative read-back。
- 将纯本地可重放步骤与 network-boundary effect 分开。
- 恢复后检查 backlog、lease/lock、失败队列和业务 case state。

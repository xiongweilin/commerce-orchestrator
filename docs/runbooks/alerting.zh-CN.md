# Alerting runbook

> 权威英文原文：[alerting.md](alerting.md)。

告警只表示 observation 越过监控阈值，不等于 root cause、业务失败或行动授权。

1. 确认 alert source、时间窗口和 affected service。
2. 读取当前 health、metric 和 log，而不是只依赖告警文本。
3. 判断持续问题、瞬时抖动或监控自身故障。
4. 把业务影响与基础设施影响分开。
5. 有外部副作用风险时，先限制进一步执行，再检查 durable attempt/reconciliation state。
6. 修复后验证实际服务和业务 postcondition；仅告警消失不够。

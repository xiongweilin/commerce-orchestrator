# Reconciliation drift runbook

> 权威英文原文：[reconciliation-drift.md](reconciliation-drift.md)。

当 provider reality、内部 effect record 与 reconciliation projection 不一致时使用。

1. 确定 case/effect identity 和 provider reference。
2. 读取权威外部状态，不从本地 desired state 推断现实。
3. 区分未执行、已执行但确认丢失、projection 陈旧、重复 event 和无法确认。
4. Ambiguous result 保持 UNKNOWN，不能因 timeout 或 retry count 直接重发。
5. 使用同一 durable identity 完成 reconciliation。
6. Domain postcondition 被独立验证后才能推进 completion。
7. 无法建立现实状态时进入人工处理，不伪造结论。

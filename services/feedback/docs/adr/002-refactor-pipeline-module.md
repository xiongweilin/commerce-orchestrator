# ADR 002: Pipeline 独立模块

## 状态
已采纳（2026-07）

## 上下文
service.py 的 process_job 认知复杂度超 Sonar 阈值。rebuild_clusters 164 行包含缓存、聚类、数据提取三类职责。

## 决策
提取独立的 pipeline.py，rebuild_clusters 拆为 7 个 helper。

## 理由
- service 负责编排，pipeline 负责计算
- helper 可独立测试
- 更换算法只需改 pipeline

## 后果
- pipeline.py 当前 0% 覆盖率（helper 未被 service 层测试触达）
- 待补 pipeline 单元测试

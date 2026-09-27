# 重建配置

[English](rebuild-configuration.md) | [简体中文](rebuild-configuration.zh-CN.md)

本文记录冻结快照的非敏感配置结构与重建规则。敏感值由 Git 之外的专门 secret owner 管理；中文版不重复任何凭据值或敏感配置内容。

## 配置所有权

- Repository 内的 Compose、Dockerfile、application config、database bootstrap、monitoring config 和 deployment script 是服务定义事实源。
- 活跃环境文件是本地 deployment input，不提交到 Git。
- Secret 只通过外部 secret owner materialize。
- 变量名和默认非敏感结构以仓库当前配置文件为准。

## 重建输入

主要重建 surface 包括：

- root Compose 和 README；
- backend / console image 与运行说明；
- database initialization 与 migration；
- monitoring / alerting；
- catalog / feedback / Odoo sandbox service；
- backup / restore runbook；
- dependency/revision verification script。

## 重建顺序

1. 按 Compose/script 期望路径恢复 repository。
2. 在启动依赖服务前恢复需要的 volume/database backup。
3. 从 secret owner 注入敏感配置，不把它们写进 Git。
4. 按 README、Compose 和 deployment script 的启动顺序执行。
5. Container 正在运行不等于重建完成；启动后必须运行项目 verification，并检查数据库、worker、adapter、reconciliation 与外部 sandbox state。

本仓库已经冻结，因此这里记录的是历史环境重建方式，不是当前 AIOS 的部署 authority。

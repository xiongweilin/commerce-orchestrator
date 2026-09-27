# 开发环境 runbook

> 权威英文原文：[dev-environment.md](dev-environment.md)。

本仓库是冻结的历史快照。开发环境只用于复现、研究和维护历史代码，不代表当前 AIOS production topology。

- 按仓库锁定文件安装依赖。
- 使用仓库提供的 Compose/服务配置启动本地依赖。
- 私密配置通过本地环境或专门 owner 提供，不写入 Git。
- 修改前确认目标属于历史维护，而不是把新功能继续堆回 frozen snapshot。
- 运行与改动直接相关的测试、lint 和 contract check。
- 需要当前架构时，回到 `xiongweilin/aios`、`guide` 等当前 owner。

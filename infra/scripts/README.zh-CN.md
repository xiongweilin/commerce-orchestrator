# infra/scripts

[English](README.md) | [简体中文](README.zh-CN.md)

部署/运维 helper script 目录。

v1 当前没有额外 script：

- 启动、停止、日志：见上级 README 中的 docker compose 命令。
- 数据库初始化：PostgreSQL 第一次启动时自动执行 postgres/init.sql。

后续增加 backup、health-check 或 migration helper 时，应使用清晰的 PowerShell 或 shell 文件名，并在 infra/README.md 登记用途。

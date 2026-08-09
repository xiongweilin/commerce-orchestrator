# infra/scripts

部署/运维辅助脚本目录。当前 v1 无额外脚本：

- 启动/停止/日志：见 [../README.md](../README.md)（`docker compose` 命令）。
- 数据库初始化：由 [../postgres/init.sql](../postgres/init.sql) 在 Postgres 首次启动时自动执行。

后续如新增备份、健康检查、迁移辅助脚本，建议命名 `*.ps1` / `*.sh` 双语并存，并在 `infra/README.md` 登记用法。

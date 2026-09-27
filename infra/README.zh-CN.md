# infra — 部署与运维

[English](README.md) | [简体中文](README.zh-CN.md)

本目录保存 commerce-orchestrator 的容器部署、数据库初始化和 observability stack。

主要目录包括 PostgreSQL 初始化、Prometheus 采集与告警、Grafana dashboard、Alertmanager、本地 alert receiver 和运维脚本。

## 启动拓扑

启动顺序：

```text
postgres healthy
→ db-bootstrap completed
→ migrate completed
→ api + worker
→ console + prometheus + grafana + alertmanager + alert-receiver + metabase
```

Odoo 19 默认不启动，只在 odoo profile 下启用。

Database bootstrap 创建 least-privilege role；migration 单独执行 Alembic；API/worker 启动不改 schema。API 与 worker 使用同一 image、不同 process。Console 使用 Next.js BFF。Prometheus/Grafana/Alertmanager 负责观测；Metabase business connection 必须使用 read-only role。

v1 不使用 Redis、RabbitMQ、Kafka、Elasticsearch 或 Kubernetes；async/idempotency 来自 DBOS + PostgreSQL。

## 快速开始

先从 .env.example 创建本地 .env；真实敏感值不得提交。

```bash
docker compose up -d
docker compose up migrate
docker compose up db-bootstrap
docker compose logs -f --tail=100
docker compose build
```

启用 Odoo：

```bash
docker compose --profile odoo up -d
```

## Monitoring 与配置

Prometheus 读取 alert rules；Grafana 自动 provision dashboard；Alertmanager 在 sandbox 中只发送到本地 alert-receiver。

环境变量名以根 .env.example 为事实源。Container 内数据库 host 使用 postgres；localhost 只用于 bare-metal 开发。

## 运维注意事项

- 端口冲突先检查宿主 listener。
- .env 缺失会阻止依赖它的 service 启动。
- docker compose down 不删除 volume；down -v 会丢数据。
- Odoo 只有显式 profile 才启动。
- Metabase business connection 必须使用只读账号。

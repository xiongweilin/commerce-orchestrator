# ADR-0002：技术栈固定（v1 不引入中间件）

- **Status**: Accepted
- **Date**: 2026-08-10

## Context

需要一组稳定、低认知负担的技术栈；避免为“可能用到”的能力提前引入基础设施；同时为长流程、幂等、对账提供足够的开发效率。

## Decision

固定以下技术栈，v1 不再引入 Redis / RabbitMQ / Kafka / Elasticsearch / Kubernetes：

- **后端**：Python 3.12、FastAPI、Pydantic、SQLAlchemy 2、Alembic、uv（依赖以 `backend/uv.lock` 为唯一真相）。
- **长流程引擎**：DBOS OSS + PostgreSQL（见 ADR-0001）。
- **前端**：Next.js（React） + TypeScript；运营控制台。
- **编排/部署**：Docker Compose。
- **可观测性**：OpenTelemetry（OTLP over HTTP）、Prometheus、Grafana。
- **依赖快照（2026-08-10 核实的上限版本）**：fastapi 0.141.1、uvicorn 0.52.1、pydantic 2.13.4、sqlalchemy 2.0.51、alembic 1.19.1、psycopg 3.3.4、structlog 26.1.0、pyjwt 2.13.0、cryptography 49.0.0、uuid6 2025.0.1、OTEL 1.44.x、dbos 2.29。
- 外部系统版本锚点：Shopify Admin GraphQL 冻结稳定版本 2026-07；Odoo 19；Metabase OSS 镜像 `metabase/metabase:v0.61.2.x`（上架前核实确切 tag）；Next.js 16.2.12（React 19.2.8）。

## Consequences

**正面**：栈内一致性高；中间件少，运维与故障面小；依赖由 uv.lock 精确锁定，可复现。

**负面/约束**：引入新中间件（队列、缓存、搜索、K8s）必须先新增 ADR；单机 Compose 部署限制扩展性（v1 可接受）。

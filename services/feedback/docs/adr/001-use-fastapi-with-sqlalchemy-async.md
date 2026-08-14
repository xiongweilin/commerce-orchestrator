# ADR 001: FastAPI + SQLAlchemy Async

## 状态
已采纳（2025）

## 上下文
需要一个高性能、类型安全的 Python 后端处理工单分析、聚类、SOP 生成。

## 决策
FastAPI + SQLAlchemy 2.0 async + PostgreSQL。

## 理由
- 自动 OpenAPI 文档 + Pydantic 校验减少集成测试工作量
- Async event loop 统一处理 DB 查询和 LLM 调用
- PostgreSQL JSON 和全文搜索适合工单文本分析

## 后果
- 所有 DB 操作用 async with 会话
- Alembic 需异步配置
- 前端 Next.js 通过 REST 通信

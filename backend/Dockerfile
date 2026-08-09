# syntax=docker/dockerfile:1
#
# commerce-orchestrator backend 镜像（api 与 worker 共用，仅启动命令不同）。
# 多阶段构建：builder 中按 uv.lock 装入 venv 并安装项目，
# runtime 阶段只保留 venv + 源码，不保留编译工具链（hatchling、uv 缓存等）。
#
# uv 版本固定为生成 backend/uv.lock 时的版本：0.12.3。

# ------------------------- builder：解析锁定依赖 + 安装项目 -------------------------
FROM python:3.12-slim AS builder

# 从官方 uv 镜像复制静态二进制（固定版本，与 uv.lock 生成环境一致）
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# 先只拷贝依赖清单，最大化层缓存
COPY pyproject.toml uv.lock ./
# 仅安装非 dev 依赖（--frozen 严格按 uv.lock，不重新解析）
RUN uv sync --frozen --no-dev --no-install-project

# 应用源码 + alembic 迁移树
COPY app/ ./app/
COPY alembic/ ./alembic/

# 安装项目本身（editable），保证 `uv run uvicorn ...` / `uv run python -m app.worker` 可用
RUN uv sync --frozen --no-dev

# ------------------------- runtime：精简、非 root、无构建工具 -------------------------
FROM python:3.12-slim AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# curl 供 compose 健康检查使用（http://localhost:8000/healthz）
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 非 root 运行用户
RUN groupadd --system app && useradd --system --gid app --no-create-home --home-dir /app app

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app /app/app
COPY --from=builder /app/alembic /app/alembic

USER app

EXPOSE 8000

# 默认以 API 进程启动；compose 中 worker 服务用 command 覆盖为 `uv run python -m app.worker`
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

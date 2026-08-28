# syntax=docker/dockerfile:1
#
# commerce-orchestrator backend 镜像（api、worker、migrate 共用，仅启动命令不同）。
# 多阶段构建：从 Compose additional_contexts 提供的 portable-runtime 源码构建
# wheel，把 wheel 安装进共享 venv；runtime 阶段只保留 venv + 应用源码。
#
# uv 版本固定为生成 backend/uv.lock 时的版本：0.12.3。

# Canonical runtime dependency pin; tools/Verify-PortableRuntimeRevision.ps1 checks
# that the additional build context is checked out at this exact revision.
ARG PORTABLE_RUNTIME_REV=b26487a6efe3933c5d92d637b522eac211e153c1

# ------------------------- portable-runtime：构建运行时 wheel -------------------------
FROM python:3.12-slim AS portable-runtime-wheel

# Compose 通过 additional_contexts 提供名为 portable_runtime 的本地 sibling 源码树。
# 构建输入的 revision 由 tools/Verify-PortableRuntimeRevision.ps1 在构建前校验。
ARG PORTABLE_RUNTIME_REV

# 从官方 uv 镜像复制静态二进制（固定版本，与 uv.lock 生成环境一致）
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /src/portable-runtime
COPY --from=portable_runtime pyproject.toml README.md ./
COPY --from=portable_runtime src ./src
COPY --from=portable_runtime contracts ./contracts

RUN uv build --wheel --out-dir /wheels .

# ------------------------- builder：解析锁定依赖 + 安装项目 -------------------------
FROM python:3.12-slim AS builder

# 从官方 uv 镜像复制静态二进制（固定版本，与 uv.lock 生成环境一致）
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# portable-runtime 是 Commerce 的正式运行时依赖。它的源码由 Compose
# additional_contexts 提供，先在独立 stage 构建 wheel，再安装到最终 runtime venv；
# 不依赖 GitHub 下载，也不把 upstream 源码 vendor 到 Commerce 仓库。
ARG PORTABLE_RUNTIME_REV

# 先只拷贝依赖清单，最大化层缓存（README.md 是 hatchling 构建所需的 readme 文件）
COPY pyproject.toml uv.lock README.md ./
# 仅安装非 dev 依赖（--frozen 严格按 uv.lock，不重新解析）
RUN uv sync --frozen --no-dev --no-install-project

# 应用源码 + alembic 迁移树
COPY app/ ./app/
COPY alembic/ ./alembic/
# Alembic 配置：migrate 服务在 /app 下执行 `uv run alembic upgrade head`，
# 必须把 alembic.ini 一并装入镜像（alembic 以当前工作目录定位配置文件）。
COPY alembic.ini ./

# 安装项目本身（editable），保证 `uv run uvicorn ...` / `uv run python -m app.worker` 可用
RUN uv sync --frozen --no-dev

# 让 `docker build --target builder` 也验证 portable-runtime wheel stage；
# 最终安装仍在 runtime stage 对共享 venv 执行。
COPY --from=portable-runtime-wheel /wheels /portable-runtime-wheels
RUN test -f /portable-runtime-wheels/portable_runtime-*.whl

# ------------------------- runtime：精简、非 root、无构建工具 -------------------------
FROM python:3.12-slim AS runtime

ARG PORTABLE_RUNTIME_REV

LABEL org.metratio.portable-runtime.revision="${PORTABLE_RUNTIME_REV}"

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # runtime 的 venv 由 builder 按 uv.lock 完整构建并原样拷贝，无需（也不应）在启动时重新同步；
    # UV_NO_SYNC=1 让 `uv run` 跳过 sync/重建，避免无网络/无写权限时启动失败
    UV_NO_SYNC=1 \
    # 非 root 用户 app 对 /app 无写权限，uv 缓存放到 /tmp（容器内可写）
    UV_CACHE_DIR="/tmp/uv-cache"

# curl 供 compose 健康检查使用（http://localhost:8000/healthz）
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 非 root 运行用户
RUN groupadd --system app && useradd --system --gid app --no-create-home --home-dir /app app
RUN mkdir -p /var/lib/commerce-responsibility \
    && chown app:app /var/lib/commerce-responsibility

WORKDIR /app

# uv 可执行文件：compose 的 api/worker 启动命令都是 `uv run ...`，runtime 必须包含 uv
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

# pyproject.toml + uv.lock + README.md 供 `uv run` 做项目发现（README 为 hatchling 构建兜底）
COPY pyproject.toml uv.lock README.md ./
# Alembic 配置文件（runtime 阶段同样需要，供 migrate 服务使用）
COPY alembic.ini ./

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app /app/app
COPY --from=builder /app/alembic /app/alembic
COPY --from=portable-runtime-wheel /wheels /wheels

# 安装 portable-runtime wheel 到共享 backend venv；api、worker、migrate 因而使用
# 完全相同的运行时依赖集合。wheel 已在 portable-runtime-wheel stage 中构建。
RUN uv pip install --python /app/.venv/bin/python --no-deps /wheels/portable_runtime-*.whl \
    && /app/.venv/bin/python -c 'from portable_runtime.public_contracts.catalog import contract_catalog; c = contract_catalog(); assert c["catalog_version"] == "portable-runtime-contracts-v1"; assert c["owner"] == "portable-runtime/contracts"' \
    && rm -rf /wheels /tmp/uv-cache \
    && mkdir -p /tmp/uv-cache \
    && chown app:app /tmp/uv-cache

USER app

EXPOSE 8000

# 默认以 API 进程启动；compose 中 worker 服务用 command 覆盖为 `uv run python -m app.worker`
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

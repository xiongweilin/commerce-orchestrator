# commerce-orchestrator 常用开发命令（Linux/macOS）
# Windows 用户不需要 make：直接使用各目标注释中给出的 docker compose / uv 等价命令。

.PHONY: setup dev-up dev-up-odoo dev-down logs migrate test lint console build

## 安装全部依赖（backend: uv sync；console: npm ci）
setup:
	cd backend && uv sync --frozen --extra dev
	cd console && npm ci

## 启动完整栈（postgres + api + worker + metabase）
dev-up:
	docker compose up -d

## 启动完整栈并启用 Odoo 19（P0 JSON-2 API 验证）
dev-up-odoo:
	docker compose --profile odoo up -d

## 停止并移除容器（保留数据卷）
dev-down:
	docker compose down

## 跟踪日志
logs:
	docker compose logs -f --tail=100

## 数据库迁移（Windows: cd backend && uv run alembic upgrade head）
migrate:
	cd backend && uv run alembic upgrade head

## 后端测试（Windows: cd backend && uv run pytest）
test:
	cd backend && uv run pytest

## 后端 lint + 格式检查（Windows: cd backend && uv run ruff check app）
lint:
	cd backend && uv run ruff check app
	cd backend && uv run ruff format --check app

## 本地启动 console 开发服务器（Windows: cd console && npm run dev）
console:
	cd console && npm run dev

## 构建全部镜像
build:
	docker compose build

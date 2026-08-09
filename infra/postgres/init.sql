-- ============================================================================
-- commerce-orchestrator PostgreSQL 首次初始化脚本
--
-- 挂载到 postgres 容器的 /docker-entrypoint-initdb.d/init.sql，
-- 仅当数据卷为空、容器首次启动时由官方镜像以超级用户执行一次。
-- 所有语句均写成幂等形式（可安全重放，但 docker-entrypoint-initdb.d 不会重跑）。
-- ============================================================================

-- 1) 主库（commerce）启用 pgcrypto（加密辅助函数）
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 2) DBOS 系统数据库（若不存在则创建，归属 commerce 角色）
--    psql 不能在条件语句/事务块中直接执行 CREATE DATABASE，
--    故用 SELECT 生成语句 + \gexec 动态执行（幂等）。
SELECT 'CREATE DATABASE dbos OWNER commerce'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'dbos')\gexec

-- 3) Metabase 应用库（compose 中 MB_DB_DBNAME=metabase，须先存在）
SELECT 'CREATE DATABASE metabase OWNER commerce'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'metabase')\gexec

-- 4) Odoo 独立数据库（与业务主库隔离，避免 Odoo 在 commerce 库中建表）
SELECT 'CREATE DATABASE odoo OWNER commerce'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'odoo')\gexec

-- 5) 授权：commerce 是应用/worker/DBOS 的唯一角色，确保迁移与建表权限
GRANT ALL PRIVILEGES ON DATABASE commerce TO commerce;
GRANT ALL PRIVILEGES ON DATABASE dbos TO commerce;
GRANT ALL PRIVILEGES ON DATABASE metabase TO commerce;
GRANT ALL PRIVILEGES ON DATABASE odoo TO commerce;

-- 6) DBOS 库内启用 pgcrypto，并把 public schema 归属 commerce
--    （PostgreSQL 15+ 的 public schema 不再默认对库内所有角色开放写权限）
\connect dbos
CREATE EXTENSION IF NOT EXISTS pgcrypto;
ALTER SCHEMA public OWNER TO commerce;

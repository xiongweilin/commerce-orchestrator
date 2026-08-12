-- ============================================================================
-- commerce-orchestrator PostgreSQL 角色与权限引导（幂等）
--
-- 由 compose 的 db-bootstrap 服务执行（psql -v ON_ERROR_STOP=1 -f bootstrap.sql），
-- 以容器超级用户（POSTGRES_USER，默认 commerce）连接 commerce 库运行。
--
-- 职责：
--   1) 幂等创建最小权限角色（不删除/重命名现有 owner 角色 commerce，兼容旧部署）；
--   2) 幂等创建 dbos/metabase/odoo 应用库（首次引导由 init.sql 完成，此处覆盖非空卷升级）；
--   3) 移交各应用库所有权并设置 public schema owner；
--   4) 设置默认权限：commerce_migrator 后续（Alembic）建的表/序列按角色自动授权。
--
-- 安全提醒：以下口令全部为开发占位符（与 compose.yaml / .env.example 一致），
-- 仅用于本地影子环境；影子/生产环境必须通过 secret 注入真实口令，禁止提交真实值。
-- ============================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- 1) 最小权限角色（幂等创建）
--    commerce_migrator : Alembic DDL（CREATE TABLE/INDEX 等）
--    commerce_api      : 命令/决策/读取（command、webhook、decision 及查询）
--    commerce_worker   : workflow/domain/effect/reconciliation 写权限
--    commerce_readonly : 仅 SELECT projection/view（供 Metabase 连接业务库）
--    dbos_app          : DBOS 系统库（dbos）
--    metabase_app      : Metabase 自身应用库（metabase）
--    odoo_app          : Odoo 数据库（odoo）
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'commerce_migrator') THEN
    CREATE ROLE commerce_migrator LOGIN PASSWORD 'commerce_migrator' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'commerce_api') THEN
    CREATE ROLE commerce_api LOGIN PASSWORD 'commerce_api' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'commerce_worker') THEN
    CREATE ROLE commerce_worker LOGIN PASSWORD 'commerce_worker' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'commerce_readonly') THEN
    CREATE ROLE commerce_readonly LOGIN PASSWORD 'commerce_readonly' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dbos_app') THEN
    CREATE ROLE dbos_app LOGIN PASSWORD 'dbos_app' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'metabase_app') THEN
    CREATE ROLE metabase_app LOGIN PASSWORD 'metabase_app' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'odoo_app') THEN
    CREATE ROLE odoo_app LOGIN PASSWORD 'odoo_app' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 2) 应用库幂等创建（非空卷升级场景的兜底；首次引导由 init.sql 完成）
--    psql 不能在条件语句中直接执行 CREATE DATABASE，故用 \gexec 动态执行。
-- ---------------------------------------------------------------------------
SELECT 'CREATE DATABASE dbos OWNER commerce'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'dbos')\gexec

SELECT 'CREATE DATABASE metabase OWNER commerce'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'metabase')\gexec

SELECT 'CREATE DATABASE odoo OWNER commerce'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'odoo')\gexec

-- ---------------------------------------------------------------------------
-- 3) 所有权移交：各应用库归对应最小权限角色所有
-- ---------------------------------------------------------------------------
ALTER DATABASE dbos OWNER TO dbos_app;
ALTER DATABASE metabase OWNER TO metabase_app;
ALTER DATABASE odoo OWNER TO odoo_app;

-- ---------------------------------------------------------------------------
-- 4) 业务库 commerce 的 schema 与连接权限
-- ---------------------------------------------------------------------------
GRANT CREATE ON DATABASE commerce TO commerce_migrator;
GRANT CONNECT ON DATABASE commerce TO commerce_migrator, commerce_api, commerce_worker, commerce_readonly;
GRANT USAGE, CREATE ON SCHEMA public TO commerce_migrator;
GRANT USAGE ON SCHEMA public TO commerce_api, commerce_worker, commerce_readonly;

GRANT CONNECT ON DATABASE dbos TO dbos_app;
GRANT CONNECT ON DATABASE metabase TO metabase_app;
GRANT CONNECT ON DATABASE odoo TO odoo_app;

-- ---------------------------------------------------------------------------
-- 5) 默认权限：commerce_migrator 创建的表/序列/函数按角色自动授权
--    （只覆盖后续所有 Alembic 迁移创建的对象；存量对象由第 7 节归一处理）
-- ---------------------------------------------------------------------------
ALTER DEFAULT PRIVILEGES FOR ROLE commerce_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO commerce_api, commerce_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE commerce_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO commerce_api, commerce_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE commerce_migrator IN SCHEMA public
  GRANT SELECT ON TABLES TO commerce_readonly;

-- ---------------------------------------------------------------------------
-- 6) 各应用库内 public schema 归属（PostgreSQL 15+ 默认不再对任意角色开放写权限）
-- ---------------------------------------------------------------------------
\connect dbos
ALTER SCHEMA public OWNER TO dbos_app;

\connect metabase
ALTER SCHEMA public OWNER TO metabase_app;

\connect odoo
ALTER SCHEMA public OWNER TO odoo_app;
-- ---------------------------------------------------------------------------
-- 6b) DBOS 系统 schema（dbos 库内 dbos）存量归一：历史卷由旧 owner（commerce）创建，
--      dbos_app 无法在 information_schema 中看到该 schema 会触发重复创建；
--      这里移交属主并转移其内部对象，保证 DBOS 系统迁移可继续。
-- ---------------------------------------------------------------------------
\connect dbos

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'dbos') THEN
    ALTER SCHEMA dbos OWNER TO dbos_app;
  END IF;
END $$;

DO $$
DECLARE obj record;
BEGIN
  FOR obj IN
    SELECT c.relname, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'dbos'
      AND c.relkind IN ('r','p','v','m')
      AND c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = 'dbos_app')
  LOOP
    EXECUTE format('ALTER TABLE %I.%I OWNER TO dbos_app', 'dbos', obj.relname);
  END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- 7) 非空卷升级：现有 public 对象权限归一（幂等）
--    P7 之前的历史卷由旧 owner（commerce）创建全部对象，且未授予
--    commerce_migrator / commerce_api / commerce_worker 权限；
--    ALTER DEFAULT PRIVILEGES 只影响新对象，这里把存量对象补齐：
--    表/视图 owner 移交 commerce_migrator，并按角色授予最小读写权限。
-- ---------------------------------------------------------------------------
\connect commerce

DO $$
DECLARE obj record;
BEGIN
  FOR obj IN
    SELECT c.relname, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r','p','v','m','S')
      AND c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = 'commerce_migrator')
  LOOP
    IF obj.relkind IN ('r','p','v','m') THEN
      EXECUTE format('ALTER TABLE public.%I OWNER TO commerce_migrator', obj.relname);
    END IF;
    IF obj.relkind IN ('r','p') THEN
      EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.%I TO commerce_api, commerce_worker', obj.relname);
      EXECUTE format('GRANT SELECT ON TABLE public.%I TO commerce_readonly', obj.relname);
    ELSIF obj.relkind IN ('v','m') THEN
      EXECUTE format('GRANT SELECT ON TABLE public.%I TO commerce_api, commerce_worker, commerce_readonly', obj.relname);
    ELSIF obj.relkind = 'S' THEN
      EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE public.%I TO commerce_api, commerce_worker', obj.relname);
    END IF;
  END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- 7b) DBOS 应用 schema（commerce 库内 dbos）存量归一：历史卷由旧 owner（commerce）创建，
--      commerce_worker 无法看到该 schema 会触发 DBOS 重复建 schema 而失败；
--      移交属主与内部对象，DBOS 才能继续应用表迁移。
-- ---------------------------------------------------------------------------
\connect commerce

DO $$
DECLARE obj record;
BEGIN
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'dbos') THEN
    ALTER SCHEMA dbos OWNER TO commerce_worker;
  ELSE
    CREATE SCHEMA dbos AUTHORIZATION commerce_worker;
  END IF;
  FOR obj IN
    SELECT c.relname, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'dbos'
      AND c.relkind IN ('r','p','v','m')
      AND c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = 'commerce_worker')
  LOOP
    EXECUTE format('ALTER TABLE %I.%I OWNER TO commerce_worker', 'dbos', obj.relname);
  END LOOP;
END $$;

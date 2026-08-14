SELECT format('CREATE ROLE metabase_reader LOGIN PASSWORD %L', :'reader_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'metabase_reader')
\gexec

ALTER ROLE metabase_reader PASSWORD :'reader_password';
GRANT CONNECT ON DATABASE catalog_ops TO metabase_reader;
GRANT USAGE ON SCHEMA public TO metabase_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO metabase_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE catalog IN SCHEMA public
    GRANT SELECT ON TABLES TO metabase_reader;

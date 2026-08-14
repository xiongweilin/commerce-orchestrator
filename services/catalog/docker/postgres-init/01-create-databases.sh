#!/bin/sh
set -eu

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE DATABASE catalog_erp;
CREATE DATABASE metabase;
SQL

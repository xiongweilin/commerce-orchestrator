# infra/scripts

Deployment/ops helper script directory. v1 currently has no extra scripts:

- Start/stop/logs: see [../README.md](../README.md) (`docker compose` commands).
- Database initialization: executed automatically by [../postgres/init.sql](../postgres/init.sql) on Postgres first start.

If backup, health-check, or migration helper scripts are added later, prefer naming them `*.ps1` / `*.sh` bilingually and register their usage in `infra/README.md`.

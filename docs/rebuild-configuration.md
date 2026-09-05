# Rebuild configuration

This document records the current non-secret configuration shape and values. Secret values are stored outside Git and are restored from the Bitwarden item ratio-rebuild-local-compose-secrets.

## Source

- Working tree: D:\infrastructure\compose\commerce-orchestrator
- Compose and application files in this repository remain the service-definition source of truth.
- The active .env file is a local deployment input and is intentionally not committed.

## Compose and bootstrap files

- .pytest_cache/README.md
- backend/.pytest_cache/README.md
- backend/Dockerfile
- backend/README.md
- compose.yaml
- console/components/HealthCards.tsx
- console/Dockerfile
- console/README.md
- docs/runbooks/backup-restore.md
- infra/alert-receiver/Dockerfile
- infra/README.md
- infra/scripts/README.md
- README.md
- services/catalog/.pytest_cache/README.md
- services/catalog/docker-compose.yml
- services/catalog/Dockerfile
- services/feedback/.pytest_cache/README.md
- services/feedback/docker-compose.yml
- services/feedback/Dockerfile
- services/feedback/docs/deployment.md
- services/feedback/migrations/README
- services/feedback/web/Dockerfile
- tools/Verify-PortableRuntimeRevision.ps1

## Current environment files

### .env

Assignments: 49; non-empty: 48.

BEGIN_ENV_VALUES
ALLOW_DEMO_ANALYZER=true
ALLOW_DEMO_COPYWRITER=true
CATALOG_DB_PASSWORD=<Bitwarden: ratio-rebuild-local-compose-secrets/CATALOG_DB_PASSWORD>
CATALOG_WORKFLOW_VERSION=catalog-copy-v1-candidate
CLUSTER_BLOCK_BY_PROBLEM_TYPE=true
CLUSTER_LINKAGE=complete
CLUSTER_RAW_TEXT_WEIGHT=0.2
COMMERCE_DATABASE_URL=<Bitwarden: ratio-rebuild-local-compose-secrets/COMMERCE_DATABASE_URL>
COMMERCE_DBOS_SYSTEM_DATABASE_URL=<Bitwarden: ratio-rebuild-local-compose-secrets/COMMERCE_DBOS_SYSTEM_DATABASE_URL>
COMMERCE_DIFY_API_KEY=<Bitwarden: ratio-rebuild-local-compose-secrets/COMMERCE_DIFY_API_KEY>
COMMERCE_DIFY_BASE_URL=http://127.0.0.1:18080
COMMERCE_DIFY_WORKFLOW_ID=37d8f594-68e1-4a7a-ab37-bc673b78af4f
COMMERCE_ENCRYPTION_KEY=<Bitwarden: ratio-rebuild-local-compose-secrets/COMMERCE_ENCRYPTION_KEY>
COMMERCE_ENVIRONMENT=dev
COMMERCE_JWT_EXPIRES_MINUTES=480
COMMERCE_JWT_SECRET=<Bitwarden: ratio-rebuild-local-compose-secrets/COMMERCE_JWT_SECRET>
COMMERCE_LOG_LEVEL=INFO
COMMERCE_ODOO_API_KEY=<Bitwarden: ratio-rebuild-local-compose-secrets/COMMERCE_ODOO_API_KEY>
COMMERCE_ODOO_BASE_URL=http://localhost:8069
COMMERCE_ODOO_DB=odoo
COMMERCE_ODOO_USERNAME=admin
COMMERCE_OTLP_ENDPOINT=
COMMERCE_PII_HASH_KEY=<Bitwarden: ratio-rebuild-local-compose-secrets/COMMERCE_PII_HASH_KEY>
COMMERCE_RAW_PAYLOAD_RETENTION_DAYS=30
COMMERCE_SHOPIFY_ACCESS_TOKEN=<Bitwarden: ratio-rebuild-local-compose-secrets/COMMERCE_SHOPIFY_ACCESS_TOKEN>
COMMERCE_SHOPIFY_API_VERSION=2026-07
COMMERCE_SHOPIFY_CLIENT_ID=2c35134bec2f50be47b8ca19eaf717f6
COMMERCE_SHOPIFY_CLIENT_SECRET=<Bitwarden: ratio-rebuild-local-compose-secrets/COMMERCE_SHOPIFY_CLIENT_SECRET>
COMMERCE_SHOPIFY_SHOP_NAME=metratio
COMMERCE_SHOPIFY_WEBHOOK_SECRET=<Bitwarden: ratio-rebuild-local-compose-secrets/COMMERCE_SHOPIFY_WEBHOOK_SECRET>
DIFY_BASE_URL=https://dify.metratio.com/v1
DIFY_CATALOG_WORKFLOW_API_KEY=<Bitwarden: ratio-rebuild-local-compose-secrets/DIFY_CATALOG_WORKFLOW_API_KEY>
DIFY_CLUSTER_WORKFLOW_API_KEY=<Bitwarden: ratio-rebuild-local-compose-secrets/DIFY_CLUSTER_WORKFLOW_API_KEY>
DIFY_FEEDBACK_WORKFLOW_API_KEY=<Bitwarden: ratio-rebuild-local-compose-secrets/DIFY_FEEDBACK_WORKFLOW_API_KEY>
DIFY_REPORT_WORKFLOW_API_KEY=<Bitwarden:commerce-orchestrator/DIFY_REPORT_WORKFLOW_API_KEY>
DIFY_SOP_WORKFLOW_API_KEY=<Bitwarden: ratio-rebuild-local-compose-secrets/DIFY_SOP_WORKFLOW_API_KEY>
DIFY_TIMEOUT_SECONDS=60
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
FEEDBACK_DB_PASSWORD=<Bitwarden: ratio-rebuild-local-compose-secrets/FEEDBACK_DB_PASSWORD>
METABASE_ADMIN_EMAIL=admin@catalog.local
METABASE_ADMIN_PASSWORD=<Bitwarden:commerce-orchestrator/METABASE_ADMIN_PASSWORD>
METABASE_READER_PASSWORD=<Bitwarden: ratio-rebuild-local-compose-secrets/METABASE_READER_PASSWORD>
ODOO_ADMIN_PASSWORD=<Bitwarden:commerce-orchestrator/ODOO_ADMIN_PASSWORD>
POSTGRES_DB=commerce
POSTGRES_PASSWORD=<Bitwarden: ratio-rebuild-local-compose-secrets/POSTGRES_PASSWORD>
POSTGRES_USER=commerce
ROUTING_POLICY_VERSION=feedback-routing-v3-candidate
RPA_TOKEN=<Bitwarden: ratio-rebuild-local-compose-secrets/RPA_TOKEN>
WORKFLOW_VERSION=feedback-structuring-v3-candidate
END_ENV_VALUES

## Rebuild rules

1. Restore the repository at the path expected by the compose files and scripts.
2. Restore Docker volumes and SQL dumps from D:\agent\docker备份 before starting dependent application services.
3. Materialize the secret variables from Bitwarden without committing them to Git.
4. Follow the repository README, compose files, and deployment scripts for start order and health checks.
5. A running container is not sufficient; run the project verification checks after startup.

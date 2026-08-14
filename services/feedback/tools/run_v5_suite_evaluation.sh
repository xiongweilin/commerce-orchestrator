#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

for key in \
  DIFY_FEEDBACK_WORKFLOW_API_KEY \
  DIFY_CLUSTER_WORKFLOW_API_KEY \
  DIFY_SOP_WORKFLOW_API_KEY \
  DIFY_REPORT_WORKFLOW_API_KEY
do
  if ! grep -qE "^${key}=app-.+" .env; then
    echo "suite evaluation blocked: ${key} is not configured" >&2
    exit 2
  fi
done

docker compose up -d --force-recreate feedback-api feedback-worker

docker compose exec -T feedback-worker python -m tools.evaluate \
  --analyzer dify \
  --embedding bge \
  --development data/candidate-evaluation/v4-holdout-locked.csv \
  --holdout data/suite-evaluation/v5-holdout-locked.csv \
  --manifest data/suite-evaluation/v5-manifest.json \
  --audit data/suite-evaluation/v5-holdout-audit.csv \
  --out artifacts/evaluation-v5-suite-candidate

docker compose exec -T feedback-worker python -m tools.evaluate_workflow_suite \
  --holdout data/suite-evaluation/v5-holdout-locked.csv \
  --manifest data/suite-evaluation/v5-manifest.json \
  --out artifacts/workflow-suite-v1-candidate

mkdir -p artifacts/evaluation-v5-suite-candidate artifacts/workflow-suite-v1-candidate
docker cp \
  feedback-analysis-agent-feedback-worker-1:/app/artifacts/evaluation-v5-suite-candidate/. \
  artifacts/evaluation-v5-suite-candidate/
docker cp \
  feedback-analysis-agent-feedback-worker-1:/app/artifacts/workflow-suite-v1-candidate/. \
  artifacts/workflow-suite-v1-candidate/

echo "v5 suite evaluation complete"

#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! grep -qE '^DIFY_FEEDBACK_WORKFLOW_API_KEY=app-.+' .env; then
  echo "candidate evaluation blocked: configure the imported v2 workflow API key in .env" >&2
  exit 2
fi

docker compose up -d --force-recreate feedback-api feedback-worker
docker compose exec -T feedback-worker python tools/evaluate.py \
  --analyzer dify \
  --embedding bge \
  --holdout data/candidate-evaluation/v4-holdout-locked.csv \
  --manifest data/candidate-evaluation/v4-manifest.json \
  --audit data/candidate-evaluation/v4-holdout-audit.csv \
  --out artifacts/evaluation-v2-candidate

mkdir -p artifacts/evaluation-v2-candidate
docker cp \
  feedback-analysis-agent-feedback-worker-1:/app/artifacts/evaluation-v2-candidate/evaluation.json \
  artifacts/evaluation-v2-candidate/evaluation.json
docker cp \
  feedback-analysis-agent-feedback-worker-1:/app/artifacts/evaluation-v2-candidate/evaluation.md \
  artifacts/evaluation-v2-candidate/evaluation.md
docker cp \
  feedback-analysis-agent-feedback-worker-1:/app/artifacts/evaluation-v2-candidate/status.json \
  artifacts/evaluation-v2-candidate/status.json

echo "candidate evaluation complete: artifacts/evaluation-v2-candidate/evaluation.json"

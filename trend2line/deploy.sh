#!/usr/bin/env bash
# Deploy to Cloud Run from source. Usage:
#   PROJECT_ID=my-proj MOCK_LLM=1 ./deploy.sh                       # first deploy, no key needed (live trend data, canned Gemini)
#   PROJECT_ID=my-proj GEMINI_MODEL=<flash-model-id> ./deploy.sh    # live, key from Secret Manager
set -euo pipefail
PROJECT_ID=${PROJECT_ID:?set PROJECT_ID}
REGION=${REGION:-asia-south1}
SERVICE=${SERVICE:-trend2line}
MOCK_LLM=${MOCK_LLM:-0}

./scripts/check_no_anthropic.sh

ARGS=(run deploy "$SERVICE" --source . --project "$PROJECT_ID" --region "$REGION"
      --allow-unauthenticated --memory 1Gi --cpu 1 --timeout 300 --max-instances 3)

if [[ "$MOCK_LLM" == "1" ]]; then
  ARGS+=(--set-env-vars "MOCK_LLM=1")
else
  GEMINI_MODEL=${GEMINI_MODEL:?set GEMINI_MODEL to the current Flash model ID from AI Studio}
  ARGS+=(--set-env-vars "MOCK_LLM=0,GEMINI_MODEL=$GEMINI_MODEL,GEMINI_AUTH=${GEMINI_AUTH:-auto},WIKI_USER_AGENT=${WIKI_USER_AGENT:-Trend2Line/0.2}"
         --set-secrets "GOOGLE_API_KEY=gemini-api-key:latest")
fi
# During judging, add: --min-instances 1   (avoids cold starts; small cost)
gcloud "${ARGS[@]}"

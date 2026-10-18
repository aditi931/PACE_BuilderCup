#!/usr/bin/env bash
# Deploy Trend2Line to Cloud Run with the full Google Cloud stack, live (no mock):
#
#   PROJECT_ID=my-proj GEMINI_MODEL=<gemini flash model id> ./deploy.sh
#
# What it sets up (idempotent; safe to re-run):
#   APIs        Cloud Run, Cloud Build, Artifact Registry, Secret Manager, Vertex AI, Firestore, BigQuery
#   Firestore   the (default) database, if missing            -> run history and audit trail
#   IAM         roles for the Cloud Run service account        -> Vertex AI, Firestore, BigQuery, logging
#   Cloud Run   the service, with Gemini on Vertex AI through the service account (no API key needed)
#
# Options (environment variables):
#   REGION=asia-south1          Cloud Run region
#   SERVICE=trend2line          service name (e.g. trend2line-dev for a test copy)
#   GEMINI_LOCATION=global      Vertex AI location for Gemini
#   FIRESTORE_LOCATION=asia-south1
#   AUTH=vertex_sa              vertex_sa (default: service account) | api_key (secret 'gemini-api-key')
#   SETUP=1                     0 skips APIs/Firestore/IAM for faster redeploys
#   MIN_INSTANCES=0             set 1 on demo day to avoid cold starts (small cost)
#   WARMUP=1                    after deploying, run the demo scenarios once (fills caches, checks verdicts)
#   SCHEDULE_WATCH=1            Cloud Scheduler job that runs the opportunity watch daily (WATCH_SCHEDULE, WATCH_TZ)
#   WIKI_USER_AGENT             identify yourself to Wikimedia; must contain a URL or email (robot policy),
#                               defaults to the service's own public URL
set -euo pipefail
PROJECT_ID=${PROJECT_ID:?set PROJECT_ID}
GEMINI_MODEL=${GEMINI_MODEL:?set GEMINI_MODEL (list models: python -m scripts.smoke_gemini)}
REGION=${REGION:-asia-south1}
SERVICE=${SERVICE:-trend2line}
GEMINI_LOCATION=${GEMINI_LOCATION:-global}
FIRESTORE_LOCATION=${FIRESTORE_LOCATION:-asia-south1}
AUTH=${AUTH:-vertex_sa}
SETUP=${SETUP:-1}
MIN_INSTANCES=${MIN_INSTANCES:-0}
# Wikimedia answers 403 to user agents without contact details: default to this service's public URL
EXISTING_URL=$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" \n  --format='value(status.url)' 2>/dev/null || true)
WIKI_USER_AGENT=${WIKI_USER_AGENT:-Trend2Line/0.4 (+${EXISTING_URL:-https://github.com/trend2line})}

./scripts/check_gemini_only.sh

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

if [[ "$SETUP" == "1" ]]; then
  echo "== Enabling APIs"
  gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    secretmanager.googleapis.com aiplatform.googleapis.com firestore.googleapis.com bigquery.googleapis.com \n    cloudscheduler.googleapis.com \
    --project "$PROJECT_ID"

  echo "== Firestore (default) database"
  if ! gcloud firestore databases describe --database='(default)' --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud firestore databases create --location="$FIRESTORE_LOCATION" --project "$PROJECT_ID"
  else
    echo "   already exists"
  fi

  echo "== IAM roles for $SA"
  for role in roles/aiplatform.user roles/datastore.user roles/bigquery.jobUser roles/logging.logWriter; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" --role="$role" \
      --condition=None --quiet >/dev/null
    echo "   $role"
  done
  if [[ "$AUTH" == "api_key" ]]; then
    gcloud secrets add-iam-policy-binding gemini-api-key --project "$PROJECT_ID" \
      --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor" --quiet >/dev/null
    echo "   roles/secretmanager.secretAccessor on gemini-api-key"
  fi
fi

# '@' as the list delimiter so values may contain commas or spaces
ENV="^@^GOOGLE_CLOUD_PROJECT=$PROJECT_ID@GOOGLE_CLOUD_LOCATION=$GEMINI_LOCATION@GEMINI_MODEL=$GEMINI_MODEL"
# Shared secret for the scheduled opportunity watch (POST /api/watch/run); rotated on every deploy
WATCH_TOKEN=${WATCH_TOKEN:-$(openssl rand -hex 16)}
ENV+="@MOCK_LLM=0@OFFLINE=0@RUN_STORE=firestore@ENABLE_GROUNDING=1@ENABLE_BIGQUERY=1@WIKI_USER_AGENT=$WIKI_USER_AGENT"
ENV+="@WATCH_TOKEN=$WATCH_TOKEN"
ARGS=(run deploy "$SERVICE" --source . --project "$PROJECT_ID" --region "$REGION" --service-account "$SA"
      --allow-unauthenticated --memory 1Gi --cpu 1 --timeout 300 --max-instances 3 --min-instances "$MIN_INSTANCES")
if [[ "$AUTH" == "api_key" ]]; then
  ARGS+=(--set-env-vars "${ENV}@GEMINI_AUTH=auto" --set-secrets "GOOGLE_API_KEY=gemini-api-key:latest")
else
  ARGS+=(--set-env-vars "${ENV}@GEMINI_AUTH=vertex_adc")
fi

echo "== Deploying $SERVICE (Cloud Build -> Artifact Registry -> Cloud Run)"
gcloud "${ARGS[@]}"

URL=$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')
echo "== Checking $URL"
curl -fsS "$URL/healthz" && echo
curl -fsS "$URL/api/config" | python3 -c "import json,sys; c=json.load(sys.stdin); s=c['services']; \
print('mode', c['mode'], '| gemini', s['gemini']['platform'], s['gemini']['auth'], s['gemini']['model'], \
'| grounding', s['grounding'], '| bigquery', s['bigquery'], '| runs', s['run_store'], \
'| fad reference', c['reference_status'], '| companies', len(c['companies']) - 1)"
if [[ "${SCHEDULE_WATCH:-1}" == "1" ]]; then
  echo "== Cloud Scheduler: daily opportunity watch"
  JOB="$SERVICE-watch"
  JOB_ARGS=(--project "$PROJECT_ID" --location "$REGION" --schedule "${WATCH_SCHEDULE:-0 6 * * *}"
            --time-zone "${WATCH_TZ:-Asia/Kolkata}" --uri "$URL/api/watch/run" --http-method POST
            --attempt-deadline 320s --quiet)
  if gcloud scheduler jobs describe "$JOB" --project "$PROJECT_ID" --location "$REGION" >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$JOB" "${JOB_ARGS[@]}" --update-headers "X-Watch-Token=$WATCH_TOKEN" >/dev/null
  else
    gcloud scheduler jobs create http "$JOB" "${JOB_ARGS[@]}" --headers "X-Watch-Token=$WATCH_TOKEN" >/dev/null
  fi
  echo "   $JOB runs ${WATCH_SCHEDULE:-0 6 * * *} (${WATCH_TZ:-Asia/Kolkata}). Run it now:"
  echo "   gcloud scheduler jobs run $JOB --project $PROJECT_ID --location $REGION"
fi
if [[ "${WARMUP:-1}" == "1" ]]; then
  echo "== Warming up and checking the demo scenarios (skip with WARMUP=0)"
  python3 scripts/warmup.py --url "$URL" || echo "   warm-up reported a problem; see above"
fi
echo "Open: $URL"

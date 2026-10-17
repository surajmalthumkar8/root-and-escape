#!/usr/bin/env bash
# One-command deploy for Google Cloud Shell. It is safe to re-run.
#   export PROJECT_ID=your-project-id
#   bash deploy/deploy.sh
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID first, e.g.  export PROJECT_ID=my-project}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-root-and-escape}"
DATASET="${DATASET:-plant_quality}"
BUCKET="${BUCKET:-${PROJECT_ID}-rae-photos}"
MODEL="${MODEL:-gemini-3.8-flash}"
SA_NAME="rae-runtime"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud config set run/region "$REGION" >/dev/null

step "1/8 Enable APIs"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  aiplatform.googleapis.com bigquery.googleapis.com firestore.googleapis.com storage.googleapis.com

step "2/8 Runtime service account (least privilege; no keys are created)"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NAME" --display-name="Root & Escape runtime"
for role in roles/aiplatform.user roles/bigquery.jobUser roles/bigquery.dataViewer roles/datastore.user roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" --role="$role" \
    --condition=None --quiet >/dev/null
done
PN=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:${PN}-compute@developer.gserviceaccount.com" \
  --role=roles/run.builder --condition=None --quiet >/dev/null 2>&1 || true

step "3/8 Firestore (default database; it is the one covered by the free tier)"
gcloud firestore databases describe --database='(default)' >/dev/null 2>&1 || \
  gcloud firestore databases create --database='(default)' --location=nam5 --type=firestore-native

step "4/8 Cloud Storage bucket for uploaded photos"
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$SA" \
  --role=roles/storage.objectAdmin >/dev/null

step "5/8 Load the synthetic plant data into BigQuery"
python3 -m venv .venv-deploy
# shellcheck disable=SC1091
. .venv-deploy/bin/activate
pip install -q "pandas>=2.2,<3" "numpy>=2,<3" "google-cloud-bigquery==3.46.2" "db-dtypes>=1.3" pyarrow
python scripts/load_bigquery.py --project "$PROJECT_ID" --dataset "$DATASET"
deactivate

step "6/8 Build and deploy to Cloud Run (scale to zero, max 3 instances)"
gcloud run deploy "$SERVICE" --source . --region "$REGION" --quiet \
  --service-account "$SA" --allow-unauthenticated \
  --min-instances 0 --max-instances 3 --cpu 1 --memory 1Gi --cpu-boost \
  --timeout 300 --concurrency 20 \
  --set-env-vars "RAE_BACKEND=gcp,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=global,GOOGLE_GENAI_USE_ENTERPRISE=true,RAE_USE_LLM=true,RAE_MODEL=$MODEL,RAE_DATASET=$DATASET,RAE_BQ_LOCATION=US,RAE_BUCKET=$BUCKET,RAE_LIVE_RUNS_PER_DAY=30,RAE_RUNS_PER_VISITOR_PER_DAY=5"
URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')

step "7/8 Smoke test"
curl -fsS "$URL/healthz"; echo
curl -fsS "$URL/api/meta"; echo

step "8/8 Warm the seed cases with a real Gemini run (uses 3 of today's 30 Gemini runs)"
for CASE in 118 124 121; do
  echo "-- case $CASE"
  curl -sN --max-time 280 "$URL/api/cases/$CASE/live?v=deploy-warmup-$CASE-$(date +%s)" \
    | grep -o '"type": "[a-z]*"' | sort | uniq -c || true
  curl -fsS "$URL/api/cases/$CASE" -H "X-Visitor: deploy-check-0001" \
    | python3 -c "import sys,json; r=json.load(sys.stdin)['result']; print('verdict', r['verdict'], 'modes', r['modes'], 'notes', r['notes'][:2])"
done

cat <<EOF

Live at: $URL
Next:
  - Submit this exact URL (the default run.app URL, as the AMA advised).
  - Set a budget alert and an Agent Platform spend cap (README > Cost and limits).
  - Keep the service live until results are announced (and until 4 Dec if you are a finalist).
EOF

#!/usr/bin/env bash
# One-command deploy for Google Cloud Shell. It is safe to re-run.
#   export PROJECT_ID=your-project-id
#   bash deploy/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root, wherever the script is called from

: "${PROJECT_ID:?Set PROJECT_ID first, e.g.  export PROJECT_ID=my-project}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-root-and-escape}"
DATASET="${DATASET:-plant_quality}"
BUCKET="${BUCKET:-${PROJECT_ID}-rae-photos}"
MODEL="${MODEL:-gemini-3.8-flash}"
SA_NAME="rae-runtime"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
PROBLEMS=()

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
warn() { printf '\033[33mWARNING: %s\033[0m\n' "$*"; PROBLEMS+=("$*"); }
# IAM changes take "typically 2 minutes, potentially 7 minutes or longer" to propagate, and a brand-new service
# account can be invisible for a while, so policy bindings are retried with backoff (about 4 minutes in total).
retry() {
  local n=0 delay=5
  until "$@"; do
    n=$((n + 1)); [ "$n" -ge 9 ] && return 1
    echo "  not ready yet; retrying in ${delay} s ($n/8)"; sleep "$delay"; delay=$((delay * 2 > 40 ? 40 : delay * 2))
  done
}

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud config set run/region "$REGION" >/dev/null

step "1/9 Enable APIs"
# Compute Engine is enabled only so its default service account exists: source deploys build with it.
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com compute.googleapis.com \
  aiplatform.googleapis.com bigquery.googleapis.com firestore.googleapis.com storage.googleapis.com iam.googleapis.com

step "2/9 Runtime service account (least privilege; no keys are created)"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NAME" --display-name="Root & Escape runtime"
for role in roles/aiplatform.user roles/bigquery.jobUser roles/bigquery.dataViewer roles/datastore.user roles/logging.logWriter; do
  retry gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" --role="$role" \
    --condition=None --quiet >/dev/null
done
PN=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
# Source deploys build with the default compute service account, which needs the Cloud Run builder role.
if ! retry gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:${PN}-compute@developer.gserviceaccount.com" \
  --role=roles/run.builder --condition=None --quiet >/dev/null 2>&1; then
  warn "Could not grant roles/run.builder to ${PN}-compute@developer.gserviceaccount.com. If the build in step 7 fails with a permission error, grant it in IAM and re-run."
fi

step "3/9 Firestore (default database; it is the one covered by the free tier)"
gcloud firestore databases describe --database='(default)' >/dev/null 2>&1 || \
  gcloud firestore databases create --database='(default)' --location=nam5 --type=firestore-native
# Per-visitor runs carry an expires_at field; this policy deletes them after 21 days. Not fatal if it fails.
gcloud firestore fields ttls update expires_at --collection-group=results --enable-ttl --async --quiet >/dev/null 2>&1 || \
  warn "Could not set the Firestore TTL policy on results.expires_at (old per-visitor runs will just stay)."

step "4/9 Cloud Storage bucket for uploaded photos"
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access
retry gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$SA" \
  --role=roles/storage.objectAdmin >/dev/null

step "5/9 Load the synthetic plant data into BigQuery"
python3 -m venv .venv-deploy 2>/dev/null || virtualenv -p python3 .venv-deploy
# shellcheck disable=SC1091
. .venv-deploy/bin/activate
pip install -q "pandas>=2.2,<3" "numpy>=2,<3" "google-cloud-bigquery==3.46.2" "db-dtypes>=1.3" pyarrow
python scripts/load_bigquery.py --project "$PROJECT_ID" --dataset "$DATASET"
deactivate

step "6/9 Check that no secrets are in the upload"
SECRETS=$(find . -path ./.venv -prune -o -path ./.venv-deploy -prune -o -path ./.git -prune -o -type f \
  \( -name '.env' -o -name '.env.*' -o -name '*credentials*.json' -o -name '*-key.json' -o -name '*.pem' \) -print)
if [ -n "$SECRETS" ]; then
  printf 'These files look like secrets. Remove them before deploying:\n%s\n' "$SECRETS" >&2
  exit 1
fi
echo "No secret-looking files found."

step "7/9 Build and deploy to Cloud Run (scale to zero, max 3 instances)"
gcloud run deploy "$SERVICE" --source . --region "$REGION" --quiet \
  --service-account "$SA" --allow-unauthenticated \
  --min-instances 0 --max-instances 3 --cpu 1 --memory 1Gi --cpu-boost \
  --timeout 600 --concurrency 20 \
  --set-env-vars "RAE_BACKEND=gcp,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=global,GOOGLE_GENAI_USE_ENTERPRISE=true,RAE_USE_LLM=true,RAE_MODEL=$MODEL,RAE_DATASET=$DATASET,RAE_BQ_LOCATION=US,RAE_BUCKET=$BUCKET,RAE_LIVE_RUNS_PER_DAY=30,RAE_RUNS_PER_VISITOR_PER_DAY=5"
URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')

step "8/9 Smoke test"
curl -fsS "$URL/healthz"; echo
META=$(curl -fsS "$URL/api/meta")
echo "$META"
echo "$META" | python3 -c "import sys,json; m=json.load(sys.stdin); sys.exit(0 if m['gemini'] else 1)" || \
  warn "Gemini is off: $(echo "$META" | python3 -c "import sys,json; print(json.load(sys.stdin).get('gemini_error'))")"

step "9/9 Warm the seed cases with a real Gemini run (uses 3 of today's 30 Gemini runs)"
for CASE in 118 124 121; do
  echo "-- case $CASE"
  curl -sN --max-time 290 "$URL/api/cases/$CASE/live?v=deploy-warmup-$CASE-$(date +%s)" \
    | grep -o '"type": "[a-z]*"' | sort | uniq -c || true
  CHECK=$(curl -fsS "$URL/api/cases/$CASE" -H "X-Visitor: deploy-check-0001" | python3 -c "
import sys, json
r = json.load(sys.stdin)['result'] or {}
a = r.get('analysis') or {}
print('verdict', r.get('verdict'), '| modes', r.get('modes'), '| BigQuery ML:', 'ok' if a.get('bqml_insights') else a.get('bqml_error', 'no insights'))
for n in (r.get('notes') or [])[:3]:
    print('  note:', n)
print('OK' if (r.get('modes') or {}).get('investigation') == 'gemini' else 'NOT_GEMINI')") || \
    CHECK="Could not read case $CASE back from the service. NOT_GEMINI"
  echo "$CHECK"
  case "$CHECK" in
    *NOT_GEMINI*) warn "Case $CASE is still showing the deterministic run. Check the notes above, then re-run step 9 by re-running this script." ;;
  esac
  case "$CHECK" in
    *"BigQuery ML: ok"*|*"no insights"*) ;;
    *) warn "BigQuery ML contribution analysis failed for case $CASE (the app falls back to pandas, so it still works). If the error is a permission denial, run this in the BigQuery console, then re-run the script:  GRANT \`roles/bigquery.dataEditor\` ON SCHEMA \`$PROJECT_ID.$DATASET\` TO \"serviceAccount:$SA\";" ;;
  esac
done

cat <<EOF

Live at: $URL
Next:
  - Submit this exact URL (the default run.app URL, as the AMA advised).
  - Set a budget alert and an Agent Platform spend cap (README > Cost and limits).
  - Keep the service live until results are announced (and until 4 Dec if you are a finalist).
EOF

if [ "${#PROBLEMS[@]}" -gt 0 ]; then
  printf '\n\033[33m%d thing(s) need a look:\033[0m\n' "${#PROBLEMS[@]}"
  printf '  - %s\n' "${PROBLEMS[@]}"
  exit 1
fi
echo "All checks passed."

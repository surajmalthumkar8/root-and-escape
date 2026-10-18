#!/usr/bin/env bash
# Read-only check of every part the deploy script sets up. It changes nothing and starts no Gemini run.
#   export PROJECT_ID=your-project-id
#   bash scripts/verify_cloud.sh
# Paste the output to whoever is helping you. It prints no secrets: API keys are listed by name only.
set -u
: "${PROJECT_ID:?Set PROJECT_ID first, e.g.  export PROJECT_ID=my-project}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-root-and-escape}"
DATASET="${DATASET:-plant_quality}"
BUCKET="${BUCKET:-${PROJECT_ID}-rae-photos}"
SA="rae-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
NP=0; NW=0; NF=0
pass() { printf '\033[32mPASS\033[0m  %s\n' "$*"; NP=$((NP + 1)); }
warn() { printf '\033[33mWARN\033[0m  %s\n' "$*"; NW=$((NW + 1)); }
fail() { printf '\033[31mFAIL\033[0m  %s\n' "$*"; NF=$((NF + 1)); }
head_() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
q() { "$@" </dev/null 2>/dev/null; }  # quiet, never waits for input

head_ "1 Project and billing"
if [ "$(q gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)')" = "True" ]; then
  BA=$(q gcloud billing projects describe "$PROJECT_ID" --format='value(billingAccountName)'); BA=${BA#billingAccounts/}
  pass "billing is linked (account $BA)"
else
  BA=""; fail "billing is not linked to $PROJECT_ID: Billing > My projects > Change billing"
fi

head_ "2 APIs switched on"
ENABLED=$(q gcloud services list --enabled --format='value(config.name)')
for api in run cloudbuild artifactregistry aiplatform bigquery firestore storage iam; do
  if grep -qx "${api}.googleapis.com" <<<"$ENABLED"; then pass "$api.googleapis.com"; else fail "$api.googleapis.com is off (re-run deploy/deploy.sh)"; fi
done

head_ "3 Runtime identity and keys"
if q gcloud iam service-accounts describe "$SA" >/dev/null; then pass "service account $SA exists"; else fail "service account $SA is missing"; fi
ROLES=$(q gcloud projects get-iam-policy "$PROJECT_ID" --flatten='bindings[].members' \
  --filter="bindings.members:serviceAccount:$SA" --format='value(bindings.role)')
for role in roles/aiplatform.user roles/bigquery.jobUser roles/bigquery.dataViewer roles/datastore.user roles/logging.logWriter; do
  if grep -qx "$role" <<<"$ROLES"; then pass "$SA has $role"; else fail "$SA lacks $role (re-run deploy/deploy.sh)"; fi
done
UKEYS=$(q gcloud iam service-accounts keys list --iam-account="$SA" --managed-by=user --format='value(name)')
if [ -z "$UKEYS" ]; then pass "no downloadable key exists for $SA (the app uses none)"; else fail "$SA has a downloadable key: delete it under IAM > Service accounts > Keys"; fi
if AK=$(q gcloud services api-keys list --format='value(displayName)'); then
  if [ -z "$AK" ]; then pass "no API keys in the project (the app uses none)"
  else warn "API key(s) exist: $(tr '\n' ',' <<<"$AK" | sed 's/,$//'). The app does not use them. Delete them: APIs & Services > Credentials"; fi
else
  warn "could not list API keys; check APIs & Services > Credentials by eye"
fi

head_ "4 Firestore, Cloud Storage, BigQuery"
FS=$(q gcloud firestore databases describe --database='(default)' --format='value(locationId,type,freeTier)')
if [ -n "$FS" ]; then pass "Firestore (default): $FS"; else fail "Firestore (default) database is missing"; fi
LOC=$(q gcloud storage buckets describe "gs://$BUCKET" --format='value(location)')
if [ -n "$LOC" ]; then pass "bucket gs://$BUCKET in $LOC"; else fail "bucket gs://$BUCKET is missing"; fi
for t in production_lots process_params inspection_log shipments past_8d; do
  ROWS=$(q bq show --format=json "$PROJECT_ID:$DATASET.$t" | python3 -c "import sys,json; print(json.load(sys.stdin).get('numRows',''))" 2>/dev/null)
  if [ -n "$ROWS" ] && [ "$ROWS" != "0" ]; then pass "BigQuery $DATASET.$t: $ROWS rows"; else fail "BigQuery $DATASET.$t is missing or empty"; fi
done

head_ "5 Cloud Run service"
# Each check prints "LEVEL message"; report() reads them in this shell so the counts add up.
report() {
  local line level msg
  while IFS= read -r line; do
    line=${line%$'\r'}; level=${line%% *}; msg=${line#* }
    case "$level" in PASS) pass "$msg" ;; WARN) warn "$msg" ;; FAIL) fail "$msg" ;; NOTE) echo "      note: $msg" ;; URL) URL=$msg ;; esac
  done
}
URL=""
SVC=$(q gcloud run services describe "$SERVICE" --region "$REGION" --format=json)
if [ -z "$SVC" ]; then
  fail "Cloud Run service $SERVICE not found in $REGION"
else
  report < <(SVC_JSON="$SVC" SA="$SA" python3 - <<'PY'
import json, os
s = json.loads(os.environ["SVC_JSON"]); sa = os.environ["SA"]
t = s.get("spec", {}).get("template", {}); ann = t.get("metadata", {}).get("annotations", {}); spec = t.get("spec", {})
st = s.get("status", {})
ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in st.get("conditions", []))
env = {e.get("name"): e.get("value", "") for c in spec.get("containers", []) for e in c.get("env", [])}
mn = str(ann.get("autoscaling.knative.dev/minScale", "0")); mx = str(ann.get("autoscaling.knative.dev/maxScale", "?"))
print("URL", st.get("url", ""))
print("PASS" if ready else "FAIL", "service is ready, serving revision", st.get("latestReadyRevisionName"))
print("PASS" if spec.get("serviceAccountName") == sa else "FAIL", "runs as", spec.get("serviceAccountName"))
print("PASS" if mn == "0" else "WARN", f"min instances {mn} (0 = no idle cost)")
print("PASS" if mx == "3" else "WARN", f"max instances {mx}")
print("PASS" if int(spec.get("timeoutSeconds") or 0) >= 600 else "WARN", "request timeout", spec.get("timeoutSeconds"), "s")
print("PASS" if env.get("RAE_USE_LLM") == "true" and env.get("RAE_BACKEND") == "gcp" else "FAIL",
      "env: RAE_BACKEND", env.get("RAE_BACKEND"), "RAE_USE_LLM", env.get("RAE_USE_LLM"), "model", env.get("RAE_MODEL"))
risky = [k for k in env if any(w in k.upper() for w in ("KEY", "SECRET", "TOKEN", "PASSWORD"))]
print("FAIL" if risky else "PASS", f"secret-looking env vars: {risky}" if risky else "no secret-looking environment variables")
PY
)
  if q gcloud run services get-iam-policy "$SERVICE" --region "$REGION" --format=json | grep -q '"allUsers"'; then
    pass "public: anyone with the link can open it (judges need this)"
  else
    fail "not public: judges would get 403 (re-run deploy/deploy.sh)"
  fi
fi

head_ "6 The live app"
if [ -n "$URL" ]; then
  echo "URL: $URL"
  if curl -fsS --max-time 30 "$URL/api/health" >/dev/null 2>&1; then pass "/api/health answers"
  else warn "/api/health does not answer: the running build is older than the repo; re-run deploy/deploy.sh"; fi
  META=$(curl -fsS --max-time 60 "$URL/api/meta" 2>/dev/null)
  if [ -n "$META" ]; then
    report < <(META_JSON="$META" python3 - <<'PY'
import json, os
m = json.loads(os.environ["META_JSON"])
print("PASS" if m.get("gemini") else "FAIL", f"Gemini on: {m.get('model')}" if m.get("gemini") else f"Gemini off: {m.get('gemini_error')}")
print("PASS" if m.get("bqml") else "WARN", "BigQuery ML on" if m.get("bqml") else "BigQuery ML off (pandas fallback)")
print("PASS" if "BigQuery" in (m.get("data_source") or "") else "FAIL", "data source:", m.get("data_source"))
qu = m.get("quota") or {}
print("PASS", f"Gemini runs used today: {qu.get('used_today')} of {qu.get('limit_today')}")
PY
)
  else
    fail "/api/meta does not answer"
  fi
  for CASE in 118 124 121; do
    CJ=$(curl -fsS --max-time 60 "$URL/api/cases/$CASE" -H "X-Visitor: verify-check-0001" 2>/dev/null)
    if [ -z "$CJ" ]; then fail "case $CASE does not load"; continue; fi
    report < <(CASE_JSON="$CJ" CASE="$CASE" python3 - <<'PY'
import json, os
r = json.loads(os.environ["CASE_JSON"]).get("result") or {}
m = r.get("modes") or {}; a = r.get("analysis") or {}
lvl = "PASS" if m.get("investigation") == "gemini" else "WARN"
print(lvl, f"case {os.environ['CASE']} stored run: verdict {r.get('verdict')} | intake {m.get('intake')} | investigation "
      f"{m.get('investigation')} | writer {m.get('writer')} | BigQuery ML "
      f"{'ok' if a.get('bqml_insights') else a.get('bqml_error', 'no insights')}"
      + ("" if lvl == "PASS" else " (shows the deterministic run until a Gemini run is stored)"))
for n in (r.get("notes") or [])[:2]:
    print("NOTE", n)
PY
)
  done
fi

head_ "7 Firebase Hosting (deploy/firebase.sh)"
SITE="${SITE:-$PROJECT_ID}"; WEB="https://${SITE}.web.app"
if PAGE=$(curl -fsS --max-time 30 "$WEB/" 2>/dev/null) && [ -n "$PAGE" ]; then
  if [ -n "$URL" ] && grep -q "content=\"${URL}\"" <<<"$PAGE"; then pass "$WEB serves the page and points it at $URL"
  else warn "$WEB answers but doesn't point at the Cloud Run URL: re-run deploy/firebase.sh"; fi
  if [ -n "$URL" ] && curl -sS --max-time 30 -o /dev/null -D - -X OPTIONS "$URL/api/meta" -H "Origin: $WEB" \
       -H "Access-Control-Request-Method: GET" -H "Access-Control-Request-Headers: x-visitor" 2>/dev/null \
       | grep -qi "^access-control-allow-origin: ${WEB}"; then pass "Cloud Run accepts API calls from $WEB"
  else fail "Cloud Run refuses API calls from $WEB, so that copy can't load cases: re-run deploy/deploy.sh, then deploy/firebase.sh"; fi
else
  warn "no Firebase Hosting site at $WEB yet: run  bash deploy/firebase.sh"
fi

head_ "8 Errors in the last 6 hours"
ERRS=$(q gcloud logging read "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\" AND severity>=ERROR" \
  --freshness=6h --limit=8 --format='value(timestamp,textPayload)')
if [ -z "$ERRS" ]; then pass "no errors logged"; else warn "recent errors (newest first):"; cut -c1-220 <<<"$ERRS" | sed 's/^/      /'; fi

head_ "9 Budget"
if [ -n "$BA" ] && B=$(q gcloud billing budgets list --billing-account="$BA" --format='value(displayName)') && [ -n "$B" ]; then
  pass "budget(s): $(tr '\n' ',' <<<"$B" | sed 's/,$//')"
else
  warn "no budget found (or it can't be listed): Billing > Budgets & alerts > Create budget, \$30, alerts only"
fi

printf '\n%d passed, %d warnings, %d failed\n' "$NP" "$NW" "$NF"
[ "$NF" -eq 0 ]

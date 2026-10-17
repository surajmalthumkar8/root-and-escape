#!/usr/bin/env bash
# Run ONLY after the competition is over. The app must stay live through judging.
set -euo pipefail
: "${PROJECT_ID:?Set PROJECT_ID first}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-root-and-escape}"
DATASET="${DATASET:-plant_quality}"
BUCKET="${BUCKET:-${PROJECT_ID}-rae-photos}"
read -r -p "Delete service $SERVICE, dataset $DATASET and bucket gs://$BUCKET in $PROJECT_ID? Type DELETE to confirm: " ok
[ "$ok" = "DELETE" ] || { echo "Cancelled."; exit 1; }
gcloud run services delete "$SERVICE" --region "$REGION" --project "$PROJECT_ID" --quiet || true
bq rm -r -f "$PROJECT_ID:$DATASET" || true
gcloud storage rm -r "gs://$BUCKET" --project "$PROJECT_ID" || true
echo "Done. Firestore documents (cases, results, approvals, quota) can be deleted from the console."

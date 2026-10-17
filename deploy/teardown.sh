#!/usr/bin/env bash
# Run ONLY after the competition is over. The app must stay live through judging.
set -euo pipefail
: "${PROJECT_ID:?Set PROJECT_ID first}"
REGION="${REGION:-us-central1}"
read -r -p "Delete the Root & Escape service, dataset and bucket in $PROJECT_ID? Type DELETE to confirm: " ok
[ "$ok" = "DELETE" ] || { echo "Cancelled."; exit 1; }
gcloud run services delete root-and-escape --region "$REGION" --quiet || true
bq rm -r -f "$PROJECT_ID:plant_quality" || true
gcloud storage rm -r "gs://${PROJECT_ID}-rae-photos" || true
echo "Done. Firestore documents (cases, results, approvals, quota) can be deleted from the console."

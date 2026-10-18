#!/usr/bin/env bash
# Serve the page from Firebase Hosting too, at https://<site>.web.app. Run it after deploy/deploy.sh; safe to re-run.
#   export PROJECT_ID=your-project-id
#   bash deploy/firebase.sh
# Hosting serves only the page files (HTML, CSS, JS, photos). The page then calls the Cloud Run API directly,
# because a Hosting rewrite to Cloud Run cuts each request at 60 s and buffers the live progress stream, and a
# live Gemini run takes one to two minutes. Cloud Run accepts those calls from the site's two origins only.
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

: "${PROJECT_ID:?Set PROJECT_ID first, e.g.  export PROJECT_ID=my-project}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-root-and-escape}"
SITE="${SITE:-$PROJECT_ID}"
BUILD=.firebase-build

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*"; exit 1; }
if command -v firebase >/dev/null 2>&1; then FB=(firebase); else FB=(npx --yes firebase-tools); fi
gcloud config set project "$PROJECT_ID" >/dev/null

step "1/5 Cloud Run URL"
URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)' 2>/dev/null || true)
[ -n "$URL" ] || die "Cloud Run service $SERVICE not found in $REGION. Run deploy/deploy.sh first."
echo "$URL"

step "2/5 Firebase CLI sign-in"
"${FB[@]}" projects:list >/dev/null 2>&1 || \
  die "The Firebase CLI is not signed in. Run  firebase login --no-localhost  (open the link, paste the code), then run this script again."
echo "signed in"

step "3/5 Add Firebase to $PROJECT_ID (free; billing is already linked, so it is on the Blaze plan)"
if "${FB[@]}" projects:list 2>/dev/null | grep -qw "$PROJECT_ID"; then
  echo "already a Firebase project"
else
  "${FB[@]}" projects:addfirebase "$PROJECT_ID" || die "Could not add Firebase. Open https://console.firebase.google.com, \
choose Create a project > Add Firebase to Google Cloud project, pick $PROJECT_ID, then run this script again."
fi

step "4/5 Hosting site $SITE"
if "${FB[@]}" hosting:sites:get "$SITE" --project "$PROJECT_ID" >/dev/null 2>&1; then
  echo "site exists"
else
  "${FB[@]}" hosting:sites:create "$SITE" --project "$PROJECT_ID" || \
    die "Could not create site $SITE (the name may be taken). Try:  SITE=${PROJECT_ID}-8d bash deploy/firebase.sh"
fi
WEB="https://${SITE}.web.app"
if [ "$SITE" != "$PROJECT_ID" ]; then
  # The API allows https://<project>.web.app by default; another site name has to be added.
  # Re-run this script after any deploy/deploy.sh, which resets the service's environment variables.
  gcloud run services update "$SERVICE" --region "$REGION" --quiet \
    --update-env-vars "^##^RAE_CORS_ORIGINS=${WEB},https://${SITE}.firebaseapp.com" >/dev/null
fi

step "5/5 Build and deploy the page"
rm -rf "$BUILD"; mkdir -p "$BUILD/public/static"
cp -R web/. "$BUILD/public/static/"
sed "s#<meta name=\"rae-api\" content=\"\">#<meta name=\"rae-api\" content=\"${URL}\">#" web/index.html > "$BUILD/public/index.html"
grep -q "content=\"${URL}\"" "$BUILD/public/index.html" || die "Could not point the page at $URL"
cat > "$BUILD/firebase.json" <<JSON
{
  "hosting": {
    "site": "${SITE}",
    "public": "public",
    "headers": [{ "source": "**", "headers": [{ "key": "Cache-Control", "value": "no-cache" }] }]
  }
}
JSON
(cd "$BUILD" && "${FB[@]}" deploy --only hosting --project "$PROJECT_ID" --non-interactive)

step "Check"
if curl -fsS --max-time 30 "$WEB/" | grep -q "content=\"${URL}\""; then echo "page is live at $WEB"
else echo "WARNING: $WEB does not serve the new page yet; wait a minute and open it"; fi
if curl -sS --max-time 30 -o /dev/null -D - -X OPTIONS "$URL/api/meta" -H "Origin: $WEB" \
     -H "Access-Control-Request-Method: GET" -H "Access-Control-Request-Headers: x-visitor" \
     | grep -qi "^access-control-allow-origin: ${WEB}"; then
  echo "Cloud Run accepts API calls from $WEB"
else
  echo "WARNING: Cloud Run does not accept calls from $WEB yet. Re-run deploy/deploy.sh (it ships the API change), then this script."
fi
echo
echo "Firebase Hosting: $WEB   (the run.app URL keeps working: $URL)"

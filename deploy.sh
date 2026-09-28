#!/usr/bin/env bash
# Deploy the AttributionService (API + MCP server) to Cloud Run.
#
# Prereq: gcloud must have valid credentials. If you see `invalid_grant`, run:
#     gcloud auth login
#
# Without DATABASE_URL the service uses SQLite on the container filesystem. That
# resets on every redeploy and on scale-to-zero — fine for a demo you re-seed,
# wrong for anything you want to keep.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-valueledger}"
REPO="${REPO:-valueledger}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/api:latest"

[ -n "${PROJECT_ID}" ] || { echo "PROJECT_ID not set and no gcloud default"; exit 1; }

echo "→ project ${PROJECT_ID} / region ${REGION} / service ${SERVICE}"

if ! gcloud auth print-access-token >/dev/null 2>&1; then
  echo "✗ gcloud has no valid credentials. Run:  gcloud auth login"
  exit 1
fi

echo "→ enabling APIs (idempotent)"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  --project "${PROJECT_ID}"

echo "→ ensuring Artifact Registry repo"
gcloud artifacts repositories describe "${REPO}" \
  --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1 || \
gcloud artifacts repositories create "${REPO}" \
  --repository-format=docker --location="${REGION}" --project="${PROJECT_ID}" \
  --description="ValueLedger AttributionService"

echo "→ building image via Cloud Build"
gcloud builds submit . --tag "${IMAGE}" --project "${PROJECT_ID}"

echo "→ deploying to Cloud Run"
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 4 \
  --memory 512Mi \
  --cpu 1 \
  --timeout 120 \
  ${CLOUDSQL_INSTANCE:+--add-cloudsql-instances "${CLOUDSQL_INSTANCE}"} \
  ${DATABASE_URL:+--set-env-vars "DATABASE_URL=${DATABASE_URL}"}

URL=$(gcloud run services describe "${SERVICE}" --region "${REGION}" \
        --project "${PROJECT_ID}" --format='value(status.url)')

echo
echo "  Deployed: ${URL}"
echo "    admin UI   ${URL}/"
echo "    API docs   ${URL}/docs"
echo "    MCP        ${URL}/mcp"
echo
echo "  The ledger starts empty. Seed a demo org:"
echo "    curl -s -X POST ${URL}/v1/orgs -H 'Content-Type: application/json' \\"
echo "      -d '{\"name\":\"Northwind Financial\"}'"
echo "  (SQLite on container disk — this resets on redeploy and scale-to-zero.)"

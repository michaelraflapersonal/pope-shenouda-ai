#!/usr/bin/env bash
# Deploy the private app to Google Cloud (Cloud Run + Vertex AI + a private Cloud Storage bucket).
# Usage: PROJECT=my-project REGION=europe-west1 BUCKET=my-psai-library ACCESS_CODE=choose ./scripts/deploy_gcp.sh
# Run `python -m psai.ingest ...` first so data/library.sqlite exists. Review each step before running it.
set -euo pipefail
: "${PROJECT:?}" "${REGION:?}" "${BUCKET:?}"
SA="psai-run@${PROJECT}.iam.gserviceaccount.com"

gcloud services enable run.googleapis.com aiplatform.googleapis.com documentai.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com --project "$PROJECT"

# private bucket, no public access; only the library index goes here, never the PDFs
gcloud storage buckets create "gs://${BUCKET}" --project "$PROJECT" --location "$REGION" \
  --uniform-bucket-level-access --public-access-prevention || true
gcloud storage cp data/library.sqlite "gs://${BUCKET}/library.sqlite"

gcloud iam service-accounts create psai-run --project "$PROJECT" || true
gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" --role roles/aiplatform.user
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" --member "serviceAccount:$SA" --role roles/storage.objectViewer

# Claude on Vertex AI must be enabled once in Model Garden for the project (accept Anthropic's terms there).
gcloud run deploy psai --project "$PROJECT" --region "$REGION" --source . \
  --service-account "$SA" --execution-environment gen2 \
  --add-volume name=lib,type=cloud-storage,bucket="$BUCKET",readonly=true \
  --add-volume-mount volume=lib,mount-path=/data \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=global,PSAI_ACCESS_CODE=${ACCESS_CODE:-}" \
  --no-allow-unauthenticated
echo "Deployed with IAM-only access. To let people use it with just the access code, grant roles/run.invoker to allUsers"
echo "(or put IAP / a Firebase-auth front in front of it). Decide deliberately: the books are copyrighted."

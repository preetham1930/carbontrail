#!/usr/bin/env bash
# Deploy CarbonTrail to Cloud Run. Run from the repo root in Cloud Shell:
#   PROJECT=my-project bash deploy/deploy.sh
set -euo pipefail
PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-asia-south1}"
SERVICE="${SERVICE:-carbontrail}"
MODEL_INTAKE="${MODEL_INTAKE:-gemini-3-flash-preview}"
MODEL_REASON="${MODEL_REASON:-$MODEL_INTAKE}"
MIN_INSTANCES="${MIN_INSTANCES:-1}"
SA="carbontrail-run@${PROJECT}.iam.gserviceaccount.com"
BUCKET="${PROJECT}-carbontrail-docs"
gcloud config set project "$PROJECT" >/dev/null

echo "== Enabling APIs"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  aiplatform.googleapis.com firestore.googleapis.com storage.googleapis.com iam.googleapis.com

echo "== Service account with least privilege"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create carbontrail-run --display-name "CarbonTrail Cloud Run"
for role in roles/aiplatform.user roles/datastore.user roles/storage.objectAdmin roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" --role "$role" --condition=None >/dev/null
done

echo "== Cloud Storage bucket for uploaded documents"
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://$BUCKET" --location "$REGION" --uniform-bucket-level-access

echo "== Firestore (Native mode) for run results"
gcloud firestore databases describe --database='(default)' >/dev/null 2>&1 || \
  gcloud firestore databases create --location="$REGION" --type=firestore-native

echo "== Building and deploying to Cloud Run"
gcloud run deploy "$SERVICE" --source . --region "$REGION" --service-account "$SA" \
  --allow-unauthenticated --memory 1Gi --cpu 1 --no-cpu-throttling \
  --min-instances "$MIN_INSTANCES" --max-instances 1 --concurrency 80 --timeout 900 --session-affinity \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=global,CT_MODEL_INTAKE=$MODEL_INTAKE,CT_MODEL_REASON=$MODEL_REASON,CT_GCS_BUCKET=$BUCKET,CT_FIRESTORE=1" \
  --quiet

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')
echo "== Live at $URL"
curl -s "$URL/api/health"; echo

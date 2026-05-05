#!/bin/bash
# Deploy the Carrier Sales API to Google Cloud Run.
# Idempotent — safe to re-run if any step fails.
set -e

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_ID="${PROJECT_ID:-happyrobot-fde-$(date +%s | tail -c 5)}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-carrier-sales-api}"

# Read API keys from env file if not already in environment
if [ -f .env ]; then
  set -a; source .env; set +a
fi
API_KEY="${API_KEY:?API_KEY env var required (set in .env or shell)}"
FMCSA_WEB_KEY="${FMCSA_WEB_KEY:?FMCSA_WEB_KEY env var required}"

# ---------------------------------------------------------------------------
# 1. Project
# ---------------------------------------------------------------------------
echo "==> Project: $PROJECT_ID"
if ! gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  echo "    Creating new project..."
  gcloud projects create "$PROJECT_ID" --name="HappyRobot FDE"
else
  echo "    Project already exists — reusing."
fi
gcloud config set project "$PROJECT_ID" >/dev/null

# ---------------------------------------------------------------------------
# 2. Billing
# ---------------------------------------------------------------------------
echo "==> Billing"
BILLING_LINKED=$(gcloud billing projects describe "$PROJECT_ID" --format="value(billingEnabled)" 2>/dev/null || echo "False")
if [ "$BILLING_LINKED" = "True" ]; then
  echo "    Billing already linked."
else
  BILLING_ACCT=$(gcloud billing accounts list --filter="open=true" --format="value(name)" --limit=1 2>/dev/null)
  if [ -z "$BILLING_ACCT" ]; then
    echo ""
    echo "    No open billing account found. Create one in the console:"
    echo "    https://console.cloud.google.com/billing"
    echo "    Then re-run this script."
    exit 1
  fi
  BILLING_ACCT_ID="${BILLING_ACCT##*/}"
  echo "    Linking billing account $BILLING_ACCT_ID..."
  gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCT_ID"
fi

# ---------------------------------------------------------------------------
# 3. APIs
# ---------------------------------------------------------------------------
echo "==> Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  --quiet

# ---------------------------------------------------------------------------
# 4. IAM permissions for the default Compute service account
#    (needed for Cloud Build to push images and read source uploads)
# ---------------------------------------------------------------------------
echo "==> Granting IAM permissions to Cloud Build service account..."
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

for ROLE in \
  "roles/cloudbuild.builds.builder" \
  "roles/artifactregistry.writer" \
  "roles/storage.objectViewer" \
  "roles/run.developer"
do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$COMPUTE_SA" \
    --role="$ROLE" \
    --condition=None \
    --quiet >/dev/null
  echo "    ✓ $ROLE"
done

# ---------------------------------------------------------------------------
# 5. Deploy to Cloud Run
# ---------------------------------------------------------------------------
echo "==> Deploying to Cloud Run..."
# --max-instances 1 is a deliberate constraint for this PoC: SQLite lives on the
# instance's ephemeral disk, so multiple concurrent instances would each have their
# own DB and writes would split-brain. Production would migrate to Cloud SQL and
# remove this cap.
# --min-instances 1 keeps the service warm so there's no cold start during demo.
gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars "API_KEY=$API_KEY,FMCSA_WEB_KEY=$FMCSA_WEB_KEY" \
  --memory 512Mi \
  --min-instances 1 \
  --max-instances 1 \
  --quiet

# ---------------------------------------------------------------------------
# 6. Output the URL
# ---------------------------------------------------------------------------
URL=$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --format 'value(status.url)')
echo ""
echo "==> Done!"
echo "    Service URL: $URL"
echo "    Dashboard:   $URL/dashboard"
echo "    Health:      $URL/healthz"
echo ""
echo "    Smoke test:"
echo "      curl $URL/healthz"
echo "      curl $URL/loads/DRY001 -H \"x-api-key: $API_KEY\""

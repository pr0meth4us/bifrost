#!/usr/bin/env bash
# Deploys bifrost to Cloud Run and wires up Cloud Scheduler.
#
# Run ./scripts/seed_secrets.sh first. Safe to re-run: the first pass discovers
# the service URL, the second pins it as BIFROST_PUBLIC_URL. That string is the
# OIDC issuer relying parties pin, so it is set explicitly rather than left to
# the forwarded-header fallback.
set -euo pipefail

PROJECT="${PROJECT:-bifrost-prod-2026}"
REGION="${REGION:-asia-southeast1}"
SERVICE="${SERVICE:-bifrost}"
CRON_SA="bifrost-cron@${PROJECT}.iam.gserviceaccount.com"
RUNTIME_SA="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"

SECRET_KEYS=(
  SECRET_KEY JWT_SECRET_KEY MONGO_URI EMAIL_PASSWORD REDIS_URL
  BIFROST_BOT_TOKEN BIFROST_BOT_SECRET BIFROST_CLIENT_SECRET
  PAYWAY_API_KEY PAYWAY_MERCHANT_ID ABA_RECURRING_API_TOKEN
)

# Let the runtime service account read the secrets it is about to be handed.
for key in "${SECRET_KEYS[@]}"; do
  name="$(echo "$key" | tr 'A-Z_' 'a-z-')"
  gcloud secrets describe "$name" --project "$PROJECT" >/dev/null 2>&1 || continue
  gcloud secrets add-iam-policy-binding "$name" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role=roles/secretmanager.secretAccessor \
    --project "$PROJECT" >/dev/null
done

# Build --set-secrets from whichever secrets actually exist.
set_secrets=""
for key in "${SECRET_KEYS[@]}"; do
  name="$(echo "$key" | tr 'A-Z_' 'a-z-')"
  gcloud secrets describe "$name" --project "$PROJECT" >/dev/null 2>&1 || continue
  set_secrets="${set_secrets:+$set_secrets,}${key}=${name}:latest"
done

# Non-sensitive config. BIFROST_SCHEDULER=external is what keeps the in-process
# reaper thread from running here: Cloud Run throttles CPU between requests, so
# that thread would stall and expired subscriptions would never be downgraded.
env_vars="BIFROST_SCHEDULER=external,CRON_SERVICE_ACCOUNT=${CRON_SA}"
for key in DB_NAME PAYWAY_API_URL BIFROST_CLIENT_ID ADMIN_CHAT_ID PAYMENT_GROUP_ID GUMROAD_PRODUCT_PERMALINK; do
  value="$(python3 -c "
from dotenv import dotenv_values
print(dotenv_values('.env').get('$key') or '', end='')
")"
  [ -n "$value" ] && env_vars="${env_vars},${key}=${value}"
done

deploy() {
  gcloud run deploy "$SERVICE" \
    --source . --project "$PROJECT" --region "$REGION" \
    --port 8000 --allow-unauthenticated \
    --set-secrets "$set_secrets" \
    --set-env-vars "$@"
}

echo "==> Pass 1: deploy and discover the service URL"
deploy "$env_vars"

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format='value(status.url)')"
echo "==> Service URL: $URL"

echo "==> Pass 2: pin it as the OIDC issuer"
deploy "${env_vars},BIFROST_PUBLIC_URL=${URL}"

echo "==> Cloud Scheduler (2 jobs; free tier is 3 per billing account)"
gcloud scheduler jobs describe bifrost-reaper --project "$PROJECT" --location "$REGION" >/dev/null 2>&1 \
  && SUB=update || SUB=create
gcloud scheduler jobs $SUB http bifrost-reaper \
  --project "$PROJECT" --location "$REGION" \
  --schedule "0 * * * *" --http-method POST \
  --uri "${URL}/internal/cron/reap" \
  --oidc-service-account-email "$CRON_SA" \
  --attempt-deadline 300s

gcloud scheduler jobs describe bifrost-payment-sla --project "$PROJECT" --location "$REGION" >/dev/null 2>&1 \
  && SUB=update || SUB=create
gcloud scheduler jobs $SUB http bifrost-payment-sla \
  --project "$PROJECT" --location "$REGION" \
  --schedule "*/15 * * * *" --http-method POST \
  --uri "${URL}/internal/cron/payment-sla" \
  --oidc-service-account-email "$CRON_SA" \
  --attempt-deadline 300s

cat <<EOF

Deployed: $URL

Verify:
  curl -s $URL/.well-known/openid-configuration | python3 -m json.tool | head -5
      the "issuer" must read exactly $URL

  gcloud scheduler jobs run bifrost-reaper --project $PROJECT --location $REGION
  gcloud run services logs read $SERVICE --project $PROJECT --region $REGION --limit 20
      expect the Reaper log line, not a 401/403

Still to do by hand:
  - re-register the Telegram webhook against $URL
  - set BIFROST_URL=$URL for EDCORE and prolong (phases 03 and 04)
EOF

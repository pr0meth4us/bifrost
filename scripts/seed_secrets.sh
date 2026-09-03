#!/usr/bin/env bash
# Seeds Secret Manager from bifrost/.env for the Cloud Run deploy.
#
# Values are piped straight from .env into gcloud and never printed. Run from
# the bifrost repo root:  ./scripts/seed_secrets.sh
#
# Re-running is safe: an existing secret gets a new version rather than an error,
# so this is also how you rotate one after editing .env.
set -euo pipefail

PROJECT="${PROJECT:-bifrost-prod-2026}"
ENV_FILE="${ENV_FILE:-.env}"

# Sensitive values only. DB_NAME, PAYWAY_API_URL, BIFROST_CLIENT_ID,
# ADMIN_CHAT_ID, PAYMENT_GROUP_ID and GUMROAD_PRODUCT_PERMALINK are plain
# config and are passed with --set-env-vars at deploy time instead.
SECRETS=(
  SECRET_KEY
  JWT_SECRET_KEY
  MONGO_URI
  EMAIL_PASSWORD
  REDIS_URL
  BIFROST_BOT_TOKEN
  BIFROST_BOT_SECRET
  BIFROST_CLIENT_SECRET
  PAYWAY_API_KEY
  PAYWAY_MERCHANT_ID
  ABA_RECURRING_API_TOKEN
)

[ -f "$ENV_FILE" ] || { echo "No $ENV_FILE here. Run from the bifrost repo root." >&2; exit 1; }

for key in "${SECRETS[@]}"; do
  value="$(python3 -c "
from dotenv import dotenv_values
print(dotenv_values('$ENV_FILE').get('$key') or '', end='')
")"

  if [ -z "$value" ]; then
    echo "skip    $key (not set in $ENV_FILE)"
    continue
  fi

  name="$(echo "$key" | tr 'A-Z_' 'a-z-')"

  if gcloud secrets describe "$name" --project "$PROJECT" >/dev/null 2>&1; then
    printf '%s' "$value" \
      | gcloud secrets versions add "$name" --data-file=- --project "$PROJECT" >/dev/null
    echo "rotated $name"
  else
    printf '%s' "$value" \
      | gcloud secrets create "$name" --data-file=- \
          --replication-policy=automatic --project "$PROJECT" >/dev/null
    echo "created $name"
  fi
done

echo
echo "Done. Verify with:  gcloud secrets list --project $PROJECT"

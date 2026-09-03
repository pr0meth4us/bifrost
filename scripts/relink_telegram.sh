#!/usr/bin/env bash
# Points every Telegram bot at this deployment.
#
# Bifrost runs two webhook systems and moving hosts breaks both:
#   master bot  -> /internal/telegram-webhook          (BIFROST_BOT_TOKEN)
#   tenant bots -> /api/v1/webhooks/telegram/<client>  (token per app in Mongo)
#
# Usage:  ./scripts/relink_telegram.sh [base-url]
# Defaults to the deployed Cloud Run URL — NOT .env. .env is the local config,
# so its BIFROST_PUBLIC_URL is a localhost literal, and defaulting to it would
# point every production bot at somebody's laptop.
#
# Prints what each bot reports back, because setWebhook returns 200 for a URL
# Telegram can never reach — the only real confirmation is getWebhookInfo, and
# a non-empty last_error_message there means it is still broken.
set -euo pipefail

cd "$(dirname "$0")/.."

PROJECT="${PROJECT:-bifrost-prod-2026}"
REGION="${REGION:-asia-southeast1}"
SERVICE="${SERVICE:-bifrost}"

BASE="${1:-}"
if [ -z "$BASE" ]; then
  BASE="$(gcloud run services describe "$SERVICE" \
    --project "$PROJECT" --region "$REGION" \
    --format='value(status.url)' 2>/dev/null || true)"
fi
[ -n "$BASE" ] || {
  echo "Could not read the deployed URL for $SERVICE. Pass one explicitly:" >&2
  echo "  $0 https://your-service.a.run.app" >&2
  exit 1
}
BASE="${BASE%/}"

# The webhook must match the issuer relying parties pinned, or Telegram delivers
# to an origin the rest of the system does not consider to be Bifrost.
DEPLOYED_ISSUER="$(gcloud run services describe "$SERVICE" \
  --project "$PROJECT" --region "$REGION" \
  --format='value(spec.template.spec.containers[0].env)' 2>/dev/null \
  | tr ';' '\n' | grep -a "BIFROST_PUBLIC_URL" | head -1 || true)"
if [ -n "$DEPLOYED_ISSUER" ] && ! echo "$DEPLOYED_ISSUER" | grep -qa "$BASE"; then
  echo "WARNING: target $BASE does not match the deployed BIFROST_PUBLIC_URL:" >&2
  echo "  $DEPLOYED_ISSUER" >&2
  echo "Continuing anyway — pass the URL explicitly if this is deliberate." >&2
  echo >&2
fi

echo "Relinking every Telegram bot to $BASE"
echo

SSL_CERT_FILE="$(.venv/bin/python -m certifi)" .venv/bin/python - "$BASE" <<'PY'
import sys
import requests
from dotenv import dotenv_values

base = sys.argv[1]
env = dotenv_values('.env')


def relink(label, token, path, secret=None):
    url = f"{base}{path}"
    payload = {"url": url, "drop_pending_updates": True}
    if secret:
        # Telegram echoes this back on every update; the route rejects mismatches.
        payload["secret_token"] = secret

    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/setWebhook",
                          json=payload, timeout=30).json()
    except Exception as exc:
        print(f"  {label}: request failed: {exc}")
        return

    if not r.get("ok"):
        print(f"  {label}: FAILED {r.get('description')}")
        return

    # setWebhook says ok for a URL Telegram cannot reach. Ask what it actually sees.
    info = requests.get(f"https://api.telegram.org/bot{token}/getWebhookInfo",
                        timeout=30).json().get("result", {})
    err = info.get("last_error_message")
    print(f"  {label}: -> {info.get('url')}")
    print(f"     pending={info.get('pending_update_count', 0)}"
          + (f"  LAST ERROR: {err}" if err else "  no errors"))


master = env.get('BIFROST_BOT_TOKEN')
if master:
    print("Master bot:")
    relink("bifrost", master, "/internal/telegram-webhook",
           secret=env.get('BIFROST_BOT_SECRET'))
else:
    print("Master bot: BIFROST_BOT_TOKEN not in .env, skipping")

print()
print("Tenant bots:")
mongo_uri = env.get('MONGO_URI')
if not mongo_uri:
    print("  MONGO_URI not in .env, skipping tenant bots")
    sys.exit(0)

import pymongo
db = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=8000)[env.get('DB_NAME') or 'bifrost_db']
found = False
for app_doc in db.applications.find({"telegram_bot_token": {"$exists": True, "$ne": None, "$ne": ""}}):
    found = True
    client_id = app_doc.get("client_id")
    relink(client_id, app_doc["telegram_bot_token"],
           f"/api/v1/webhooks/telegram/{client_id}")
if not found:
    print("  none configured")
PY

#!/bin/bash
# Local dev: local Mongo, no sign-in, no MFA, no Atlas.
#
# Deliberately does NOT read .env — that file points at the Atlas cluster, and
# the whole point here is that dev can't touch it. Everything below is either a
# throwaway value or a loopback address.
set -e

export MONGO_URI="mongodb://localhost:27017"
export DB_NAME="bifrost_dev"
export BIFROST_DEV_MODE=1

# Cookies must survive plain http:// — the default is Secure=true, which Safari
# drops on http://localhost and then every session silently vanishes.
export SESSION_COOKIE_SECURE=false

# Config refuses to boot without these; nothing in dev mode sends mail or signs
# a real token, so throwaway values are the point.
export SECRET_KEY="dev-secret-not-for-deployment"
export JWT_SECRET_KEY="dev-jwt-not-for-deployment"
export EMAIL_PASSWORD="unused-in-dev"

export FLASK_DEBUG=true
export PORT="${PORT:-5001}"
export PYTHONPATH="$PYTHONPATH:."

if ! /opt/homebrew/bin/mongosh --quiet --eval 'db.runCommand({ping:1})' \
     "mongodb://localhost:27017" >/dev/null 2>&1; then
  echo "Starting local mongod..."
  brew services start mongodb-community >/dev/null 2>&1 \
    || { echo "No local mongod. Install: brew install mongodb-community"; exit 1; }
  sleep 2
fi

echo "→ http://localhost:$PORT  (signed in as dev@local, db=$DB_NAME)"
exec .venv/bin/python run.py

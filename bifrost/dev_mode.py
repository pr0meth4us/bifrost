"""Local-only dev flow: no sign-in, no MFA, no Atlas.

Enabled by BIFROST_DEV_MODE=1. Two independent conditions must BOTH hold or the
app refuses to boot:

  1. the env flag is explicitly set, and
  2. MONGO_URI points at a loopback host.

Condition 2 is the one that matters. An auth bypass that can only ever attach
itself to a database on your own machine cannot leak into a deployment, even if
someone copies the flag into a prod env file by accident — the process dies at
startup instead of serving an open console.
"""
import logging
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

log = logging.getLogger(__name__)

LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}

DEV_EMAIL = "dev@local"
DEV_APP_NAME = "Dev App"


def is_local_mongo(uri):
    """True only for a single-node URI whose host is loopback.

    mongodb+srv:// is rejected outright: SRV resolves through DNS to whatever
    the record says, so the hostname in the string proves nothing about where
    the connection lands.
    """
    if not uri:
        return False
    parsed = urlparse(uri)
    if parsed.scheme != "mongodb":
        return False
    hosts = (parsed.netloc.rsplit("@", 1)[-1]).split(",")
    return all(h.rsplit(":", 1)[0].strip().lower() in LOOPBACK for h in hosts if h.strip())


def enabled():
    return os.getenv("BIFROST_DEV_MODE") == "1"


def attach(app):
    """Wire the bypass, after proving we're pointed at a local database."""
    uri = app.config.get("MONGO_URI")
    if not is_local_mongo(uri):
        raise RuntimeError(
            "BIFROST_DEV_MODE=1 refuses to start: MONGO_URI is not a local "
            "mongodb:// loopback host. The login bypass is only ever allowed "
            "against a database on this machine. Point MONGO_URI at "
            "mongodb://localhost:27017 or unset BIFROST_DEV_MODE."
        )

    from flask import session
    from . import mongo

    @app.before_request
    def _dev_sign_in():
        # Re-stamped every request so the 30-minute idle timeout in
        # backoffice/__init__.py never boots you out mid-session.
        now = datetime.now(timezone.utc).isoformat()
        if not session.get("backoffice_user"):
            db = mongo.cx[app.config["DB_NAME"]]
            admin = db.admins.find_one({"email": DEV_EMAIL})
            if not admin:
                admin = _seed(db)
            session["backoffice_user"] = str(admin["_id"])
            session["is_heimdall"] = True
            session["role"] = "Heimdall"
            session["session_started_at"] = now
        session["last_seen_at"] = now

    log.warning("🔓 DEV MODE: login and MFA bypassed, signed in as %s", DEV_EMAIL)


def _seed(db):
    """Minimum rows for the console to render: one heimdall, one application."""
    admin_id = db.admins.insert_one({
        "email": DEV_EMAIL,
        "display_name": "Dev Heimdall",
        "role": "heimdall",
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }).inserted_id

    if not db.applications.find_one({"app_name": DEV_APP_NAME}):
        # Built through register_application, not a hand-written dict: the real
        # creator sets webhook_secret and enabled_services, and a seed that
        # skips them produces an app the console crashes on.
        from .models import BifrostDB
        from flask import current_app
        BifrostDB(db.client, current_app.config["DB_NAME"]).register_application(
            app_name=DEV_APP_NAME,
            callback_url="http://localhost:5001/callback",
            web_url="http://localhost:5001",
        )
        # Point the app at a local tenant Postgres, encrypted the same way the
        # real settings form does it. Without this the CMS, Schema and Payments
        # screens all bounce to "Tenant DB connection not configured" and can't
        # be exercised at all.
        from .utils.encryption import encrypt_value
        created = db.applications.find_one({"app_name": DEV_APP_NAME})
        db.applications.update_one({"_id": created["_id"]}, {"$set": {
            "db_mode": "custom",
            "db_connection": encrypt_value(
                os.getenv("DEV_TENANT_DB", "postgresql://localhost:5432/bifrost_tenant_dev"),
                created["webhook_secret"]),
        }})

    log.warning("🌱 DEV MODE: seeded %s and '%s'", DEV_EMAIL, DEV_APP_NAME)
    return db.admins.find_one({"_id": admin_id})

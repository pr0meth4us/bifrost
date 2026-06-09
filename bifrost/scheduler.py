import time
import schedule
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bson import ObjectId
from .models import BifrostDB
from . import mongo

log = logging.getLogger("bifrost_reaper")
UTC = ZoneInfo("UTC")

def run_expiration_check(app):
    """
    Checks for expired subscriptions and downgrades them.
    Triggers webhooks so client apps know immediately.
    """
    with app.app_context():
        db = BifrostDB(mongo.cx, app.config['DB_NAME'])
        now = datetime.now(UTC)

        # Find all links that are NOT 'user' (premium/admin) AND have expired
        query = {
            "$or": [
                {"app_specific_role": {"$ne": "user"}},
                {"role": {"$ne": "user"}}
            ],
            "expires_at": {"$lt": now}
        }

        expired_links = list(db.db.app_links.find(query))

        if not expired_links:
            log.info("🌾 Reaper: No expired subscriptions found.")
            return

        log.info(f"🌾 Reaper: Found {len(expired_links)} expired subscriptions. Processing...")

        for link in expired_links:
            user_id = link['account_id']
            app_id = link['app_id']
            old_role = link.get('app_specific_role') or link.get('role') or 'unknown'

            # 1. Downgrade in DB
            db.db.app_links.update_one(
                {"_id": link['_id']},
                {
                    "$set": {
                        "app_specific_role": "user",
                        "role": "user"
                    },
                    "$unset": {
                        "expires_at": "",
                        "warning_sent": ""  # Clear warning flag on downgrade
                    }
                }
            )

            # 2. Trigger Specific Expiration Webhook
            log.info(f"⬇️ Downgrading User {user_id} for App {app_id}")
            db._trigger_event_for_user(
                account_id=user_id,
                event_type="subscription_expired",
                specific_app_id=app_id,
                extra_data={
                    "previous_role": old_role,
                    "new_role": "user",
                    "reason": "expired"
                }
            )

def run_expiration_warning_check(app):
    """
    Checks for subscriptions expiring within 3 days and sends a warning webhook event.
    """
    with app.app_context():
        db = BifrostDB(mongo.cx, app.config['DB_NAME'])
        now = datetime.now(UTC)
        warning_limit = now + timedelta(days=3)

        # Find active premium/admin links expiring within 3 days that haven't been warned yet
        query = {
            "$or": [
                {"app_specific_role": {"$ne": "user"}},
                {"role": {"$ne": "user"}}
            ],
            "expires_at": {"$gt": now, "$lt": warning_limit},
            "warning_sent": {"$ne": True}
        }

        expiring_links = list(db.db.app_links.find(query))

        if not expiring_links:
            log.info("🌾 Reaper: No subscriptions expiring soon found.")
            return

        log.info(f"🌾 Reaper: Found {len(expiring_links)} subscriptions expiring soon. Processing warnings...")

        for link in expiring_links:
            user_id = link['account_id']
            app_id = link['app_id']
            expires_at = link.get('expires_at')

            # 1. Flag as warned in DB
            db.db.app_links.update_one(
                {"_id": link['_id']},
                {"$set": {"warning_sent": True}}
            )

            # Calculate days remaining (min 0)
            days_rem = 0
            if expires_at:
                exp_tz = expires_at.replace(tzinfo=UTC) if expires_at.tzinfo is None else expires_at.astimezone(UTC)
                days_rem = max(0, (exp_tz - now).days)

            # 2. Trigger Warning Webhook
            log.info(f"⚠️ Warning User {user_id} for App {app_id} - expiring in {days_rem} days")
            db._trigger_event_for_user(
                account_id=user_id,
                event_type="subscription_warning",
                specific_app_id=app_id,
                extra_data={
                    "expires_at": expires_at.isoformat() if expires_at else None,
                    "days_remaining": days_rem
                }
            )

def start_scheduler(app):
    """Starts the scheduler in a background thread."""
    import threading

    def job():
        while True:
            schedule.run_pending()
            time.sleep(60)

    # Run every 60 minutes
    schedule.every(60).minutes.do(run_expiration_check, app)
    schedule.every(60).minutes.do(run_expiration_warning_check, app)

    # Also run immediately on startup
    run_expiration_check(app)
    run_expiration_warning_check(app)

    t = threading.Thread(target=job, daemon=True)
    t.start()
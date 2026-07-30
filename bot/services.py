# bot/services.py
import logging
import requests
from requests.auth import HTTPBasicAuth
from bson import ObjectId
from .config import Config
from .database import get_db
import pymongo

# Import the logic directly from the Backend
try:
    from bifrost.models import BifrostDB
except ImportError:
    BifrostDB = None

log = logging.getLogger(__name__)


def check_admin_permission(telegram_id, client_id):
    """
    Checks if the Telegram User is an Admin for the specific Client App.
    Returns: Boolean
    """
    try:
        if BifrostDB is None:
            return False

        db_instance = get_db()
        logic = BifrostDB(db_instance.client, Config.DB_NAME)

        # 1. Resolve App first — the account lookup is scoped to its directory.
        app_doc = logic.get_app_by_client_id(client_id)
        if not app_doc:
            return False

        # 2. Resolve User
        user = logic.find_account_by_telegram(telegram_id, logic.directory_scope(app_doc))
        if not user:
            return False

        # 3. Check Role in App Links
        role = logic.get_user_role_for_app(user['_id'], app_doc['_id'])
        if role in ['admin', 'owner', 'super_admin']:
            return True

        return False
    except Exception as e:
        log.error(f"Permission Check Failed: {e}")
        return False


def call_grant_premium(user_identifier, target_client_id, payment_id=None):
    """
    Directly accesses the database to grant a role.
    user_identifier: Can be a Telegram ID (digits) OR a Bifrost ObjectId (hex string) OR a Postgres integer user ID.
    """
    try:
        if BifrostDB is None:
            log.error("CRITICAL: BifrostDB model not found. Cannot grant premium.")
            return False

        # 1. Get a DB connection and Logic Handle
        db_instance = get_db()
        logic = BifrostDB(db_instance.client, Config.DB_NAME)

        # 2. Find App
        app_doc = logic.get_app_by_client_id(target_client_id)
        if not app_doc:
            log.error(f"App {target_client_id} not found.")
            return False

        # --- DELEGATION TO POSTGRESQL MULTI-TENANT CMS ---
        db_connection = app_doc.get("db_connection")
        if db_connection:
            db_conn_str = db_connection.get("url") if isinstance(db_connection, dict) else str(db_connection)
            track_id = app_doc.get("default_track_id") or 1
            
            # If we don't have payment_id, attempt lookup on tenant Postgres DB
            if not payment_id:
                try:
                    postgres_user_id = None
                    if str(user_identifier).isdigit() and int(user_identifier) < 1000000: # Simple threshold to separate local primary keys from telegram ids
                        postgres_user_id = int(user_identifier)
                    else:
                        user_obj = None
                        if isinstance(user_identifier, str) and len(str(user_identifier)) == 24:
                            user_obj = logic.find_account_by_id(user_identifier)
                        if not user_obj:
                            user_obj = logic.find_account_by_telegram(
                                user_identifier, logic.directory_scope(app_doc))


                        if user_obj and user_obj.get("email"):
                            from bifrost.utils.tenant_db import get_tenant_db
                            with get_tenant_db(db_conn_str) as conn:
                                with conn.cursor() as cur:
                                    cur.execute("SELECT id FROM users WHERE email = %s LIMIT 1", [user_obj["email"]])
                                    row = cur.fetchone()
                                    if row:
                                        postgres_user_id = row[0]
                                        
                    if postgres_user_id:
                        from bifrost.utils.tenant_db import get_tenant_db
                        with get_tenant_db(db_conn_str) as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "SELECT id FROM payments WHERE user_id = %s AND status = 'pending' ORDER BY id DESC LIMIT 1",
                                    [postgres_user_id]
                                )
                                row = cur.fetchone()
                                if row:
                                    payment_id = row[0]
                except Exception as lookup_err:
                    log.error(f"Postgres payment lookup failed: {lookup_err}")

            if payment_id:
                success, _ = logic.approve_manual_payment(db_conn_str, int(payment_id), int(track_id), reviewer_id="telegram_bot")
                return success
            else:
                log.error("Could not find Postgres payment_id to approve.")
                return False
        # -------------------------------------------------

        # MongoDB Flow (Original)
        user = None
        if isinstance(user_identifier, str) and len(user_identifier) == 24:
            import re
            if re.match(r'^[0-9a-fA-F]{24}$', user_identifier):
                user = logic.find_account_by_id(user_identifier)
        if not user:
            user = logic.find_account_by_telegram(user_identifier, logic.directory_scope(app_doc))
        if not user:
            log.error(f"User identifier '{user_identifier}' not found in DB.")
            return False

        # 4. LOOK FOR PENDING TRANSACTION
        pending_tx = logic.db.transactions.find_one(
            {
                "account_id": user['_id'],
                "app_id": app_doc['_id'],
                "status": "pending"
            },
            sort=[("created_at", pymongo.DESCENDING)]
        )

        if pending_tx:
            log.info(f"🔄 Found pending transaction {pending_tx['transaction_id']}. Completing...")
            success, msg = logic.complete_transaction(pending_tx['transaction_id'])
            if success:
                log.info(f"✅ Transaction {pending_tx['transaction_id']} completed successfully.")
                return True
            else:
                log.error(f"❌ Failed to complete transaction: {msg}")

        # 5. FALLBACK: Manual Grant
        # Default to premium_user if manual, but this allows flexibility in future
        target_role = "premium_user"
        log.info(f"⚠️ No pending transaction found. Falling back to manual grant ({target_role}).")

        logic.link_user_to_app(user['_id'], app_doc['_id'], role=target_role, duration_str="1m", suppress_webhook=True)

        # Manually trigger subscription_success so the client app reacts correctly
        logic._trigger_event_for_user(
            account_id=user['_id'],
            event_type="subscription_success",
            specific_app_id=app_doc['_id'],
            extra_data={
                "transaction_id": "manual-grant",
                "role": target_role,
                "method": "admin_manual_web_upload",
                "duration": "1m"
            }
        )

        log.info(f"✅ Manually granted {target_role} to {user_identifier} for {target_client_id}")
        return True

    except Exception as e:
        log.error(f"Direct DB Grant failed: {e}")
        return False


def get_app_details(client_id):
    """Fetches App Name to display nicely in the Bot."""
    try:
        db = get_db()
        app = db.applications.find_one({"client_id": client_id})
        return app
    except Exception as e:
        log.error(f"DB Error fetching app details: {e}")
        return None


def get_transaction(transaction_id):
    """Fetches a transaction by ID."""
    db = get_db()
    return db.transactions.find_one({"transaction_id": transaction_id})


def get_app_by_id(app_id):
    """Fetches App by ObjectId, safely handling strings."""
    db = get_db()
    try:
        oid = ObjectId(app_id) if isinstance(app_id, str) else app_id
        return db.applications.find_one({"_id": oid})
    except Exception as e:
        log.error(f"Invalid App ID format: {e}")
        return None
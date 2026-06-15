from pymongo import ASCENDING
from zoneinfo import ZoneInfo
import logging
from bson import ObjectId
from ..services.webhook_service import WebhookService

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
UTC = ZoneInfo("UTC")

class BaseMixin:
    def __init__(self, mongo_client, db_name):
        self.db = mongo_client[db_name]
        self.init_indexes()

    def init_indexes(self):
        """Creates unique indexes to enforce data integrity."""
        def ensure_unique_sparse(collection, field):
            idx_name = f"client_id_1_{field}_1"
            try:
                collection.create_index(
                    [("client_id", ASCENDING), (field, ASCENDING)],
                    unique=True,
                    partialFilterExpression={field: {"$type": "string"}}
                )
            except Exception:
                try:
                    log.info(f"Recreating index for {field} to ensure partialFilter constraint...")
                    try:
                        collection.drop_index(idx_name)
                    except Exception as e:
                        if "index not found" not in str(e):
                            raise e
                    collection.create_index(
                        [("client_id", ASCENDING), (field, ASCENDING)],
                        unique=True,
                        partialFilterExpression={field: {"$type": "string"}}
                    )
                except Exception as e:
                    log.warning(f"Could not recreate partial index for {field}: {e}")

        # Ensure sparse indexes for optional fields per tenant
        ensure_unique_sparse(self.db.accounts, "email")
        ensure_unique_sparse(self.db.accounts, "username")
        ensure_unique_sparse(self.db.accounts, "telegram_id")
        ensure_unique_sparse(self.db.accounts, "google_id")
        ensure_unique_sparse(self.db.accounts, "phone_number")

        # Standard non-sparse indexes
        self.db.applications.create_index([("client_id", ASCENDING)], unique=True)
        self.db.app_links.create_index([("account_id", ASCENDING), ("app_id", ASCENDING)], unique=True)
        self.db.admins.create_index([("email", ASCENDING)], unique=True)
        self.db.verification_codes.create_index("created_at", expireAfterSeconds=600)
        self.db.verification_codes.create_index([("identifier", ASCENDING)])

        # Transactions
        self.db.transactions.create_index([("transaction_id", ASCENDING)], unique=True)
        self.db.transactions.create_index([("account_id", ASCENDING)])
        self.db.transactions.create_index([("app_id", ASCENDING)])

        # Payment Logs
        self.db.payment_logs.create_index([("trx_id", ASCENDING)], unique=True)
        self.db.payment_logs.create_index([("status", ASCENDING)])

    def _trigger_event_for_user(self, account_id, event_type, specific_app_id=None, token=None, extra_data=None):
        """
        Finds linked apps for a user and triggers the webhook.
        """
        try:
            query = {"account_id": ObjectId(account_id)}
            if specific_app_id:
                query["app_id"] = ObjectId(specific_app_id)

            links = list(self.db.app_links.find(query))
            if not links:
                return

            app_ids = list(set([link['app_id'] for link in links]))
            apps = self.db.applications.find({"_id": {"$in": app_ids}})

            for app_doc in apps:
                # Trigger the webhook (WebhookService handles the signing)
                WebhookService.send_event(
                    app_doc=app_doc,
                    event_type=event_type,
                    account_id=account_id,
                    token=token,
                    extra_data=extra_data
                )
        except Exception as e:
            log.error(f"Failed to trigger events for user {account_id}: {e}")

    def _trigger_event_for_app(self, app_id, event_type, extra_data=None):
        """
        Triggers a webhook specifically for an application configuration event.
        """
        try:
            app_doc = self.db.applications.find_one({"_id": ObjectId(app_id)})
            if not app_doc:
                return

            WebhookService.send_event(
                app_doc=app_doc,
                event_type=event_type,
                account_id=None,
                token=None,
                extra_data=extra_data
            )
        except Exception as e:
            log.error(f"Failed to trigger app event for {app_id}: {e}")
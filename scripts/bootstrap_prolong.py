import os
import sys
import secrets
import json
from bson import ObjectId

# Add bifrost project path to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bifrost import create_app
from config import Config

def bootstrap_prolong():
    app = create_app(Config)
    with app.app_context():
        from bifrost import mongo
        from bifrost.models import BifrostDB
        db = BifrostDB(mongo.cx, app.config['DB_NAME'])
        
        client_id = "ministry_exam_prep"
        app_name = "Ministry Exam Prep"
        
        # Check if already registered
        app_doc = db.db.applications.find_one({"$or": [{"client_id": client_id}, {"client_id": "prolong"}]})
        
        if not app_doc:
            webhook_secret = secrets.token_hex(32)
            new_app = {
                "client_id": client_id,
                "app_name": app_name,
                "webhook_secret": webhook_secret,
                "webhook_url": "https://api.ministryexamprep.kh/bifrost-webhook",
                "custom_domain": "backoffice.wkc.kh",
                "enabled_services": {
                    "secrets_vault": True,
                    "payment_bot": True,
                    "oauth_sso": True,
                    "sms_otp": True,
                    "email_otp": True,
                    "heimdall_monitor": True
                },
                "cms_config": {
                    "payment_queue": {
                        "table": "payments",
                        "id": "id",
                        "subject_key": "user_id",
                        "amount": "amount",
                        "reference": "txn_ref",
                        "receipt": "receipt_url",
                        "status": "status",
                        "open_states": ["pending"],
                        "settled": ["approved", "rejected", "refunded"],
                        "actions": {
                            "approve": "approved",
                            "reject": "rejected",
                            "refund": "refunded"
                        },
                        "grant": {
                            "table": "entitlements",
                            "subject_key": "user_id",
                            "scope_key": "exam_track_id",
                            "status": "status",
                            "on_approve": "active",
                            "on_revoke": "revoked"
                        }
                    }
                }
            }
            res = db.db.applications.insert_one(new_app)
            app_id = str(res.inserted_id)
            print(f"✨ Successfully registered NEW app: {app_name} (ID: {app_id})")
        else:
            app_id = str(app_doc["_id"])
            webhook_secret = app_doc.get("webhook_secret") or secrets.token_hex(32)
            db.db.applications.update_one(
                {"_id": app_doc["_id"]},
                {"$set": {
                    "client_id": client_id,
                    "app_name": app_name,
                    "webhook_secret": webhook_secret,
                    "enabled_services": {
                        "secrets_vault": True,
                        "payment_bot": True,
                        "oauth_sso": True,
                        "sms_otp": True,
                        "email_otp": True,
                        "heimdall_monitor": True
                    }
                }}
            )
            print(f"✅ Existing application updated: {app_name} (ID: {app_id})")

        # Output Prolong Integration JSON config to prolong repo directory
        prolong_config_path = "/Users/nicksng/code/prolong/bifrost_bootstrap.json"
        config_payload = {
            "app_id": app_id,
            "client_id": client_id,
            "app_name": app_name,
            "webhook_secret": webhook_secret,
            "bifrost_url": "https://melted-felipa-aupp-33e78e3e.koyeb.app",
            "backoffice_url": f"https://melted-felipa-aupp-33e78e3e.koyeb.app/backoffice/app/{app_id}",
            "cms_onboarding_url": f"https://melted-felipa-aupp-33e78e3e.koyeb.app/backoffice/app/{app_id}/cms/onboarding"
        }
        
        with open(prolong_config_path, "w", encoding="utf-8") as f:
            json.dump(config_payload, f, indent=2)
            
        print(f"📄 Saved Prolong bootstrap configuration to: {prolong_config_path}")
        print("\n--- Prolong Bifrost Bootstrap Snippet ---")
        print(f"App ID:         {app_id}")
        print(f"Client ID:      {client_id}")
        print(f"Webhook Secret: {webhook_secret}")
        print(f"CMS Setup URL:  https://melted-felipa-aupp-33e78e3e.koyeb.app/backoffice/app/{app_id}/cms/onboarding")

if __name__ == "__main__":
    bootstrap_prolong()

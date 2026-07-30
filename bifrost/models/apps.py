# bifrost/models/apps.py
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bson import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
import logging

log = logging.getLogger(__name__)
UTC = ZoneInfo("UTC")


class AppMixin:
    # ---------------------------------------------------------
    # CLIENT APP MANAGEMENT
    # ---------------------------------------------------------
    @staticmethod
    def owns_its_database(app_config):
        """Whether the tenant supplied the database Bifrost talks to.

        A BYODB tenant holds its own credentials and can reach that database
        without Bifrost at all, so gatekeeping raw SQL inside the console
        protects nothing. A managed tenant is a schema inside the platform's
        shared Postgres, running under the platform's credentials — there, raw
        SQL is the platform's blast radius, not the tenant's.
        """
        app_config = app_config or {}
        if app_config.get('db_mode') == 'managed':
            return False
        return bool(app_config.get('db_connection'))

    @staticmethod
    def is_internal_tenant(app_config):
        """Whether the platform team owns this tenant.

        Internal tenants are Bifrost's own products, so a platform admin sees
        everything. External tenants are somebody else's customers and somebody
        else's data; a platform admin gets the operational view and nothing more.

        Defaults to external when the field is missing — an unclassified tenant
        gets the *narrower* answer, so forgetting to set this can never widen
        access. The migration marks today's apps internal explicitly.
        """
        return (app_config or {}).get('tenant_type') == 'internal'

    @staticmethod
    def directory_scope(app_config):
        """Which account directory this app draws its users from.

        Defaults to the app's own client_id, so a tenant with one app behaves
        exactly as before. Point several apps at the same `tenant_id` and they
        share one user pool — which is the difference between SSO that spans
        your customer's products and SSO that spans a single app.
        """
        return app_config.get('tenant_id') or app_config['client_id']
    def register_application(self, app_name, callback_url, web_url=None, logo_url=None, allowed_methods=None,
                             api_url=None, tenant_id=None, tenant_type="external"):
        """Creates a new application document.

        `tenant_id` groups apps that share a user directory; leave it None for a
        standalone app and it defaults to the app's own client_id.

        `tenant_type` is "internal" (a platform-owned product) or "external"
        (a customer). It decides how much of this tenant a platform admin sees.
        """
        safe_name = app_name.lower().replace(' ', '_')
        client_id = f"{safe_name}_{secrets.token_hex(4)}"
        client_secret = secrets.token_urlsafe(32)
        webhook_secret = secrets.token_hex(24)

        app_doc = {
            "app_name": app_name,
            "client_id": client_id,
            "client_secret_hash": generate_password_hash(client_secret),
            "webhook_secret": webhook_secret,
            "app_logo_url": logo_url or "",
            "app_qr_url": "",
            "app_web_url": web_url,
            "app_callback_url": callback_url,
            "app_api_url": api_url,
            "tenant_id": tenant_id or client_id,
            # New tenants are external until someone says otherwise.
            "tenant_type": tenant_type,
            # OIDC relying-party registration. The callback URL is always an
            # implicit member of this set; add entries for extra environments.
            "oidc_redirect_uris": [callback_url] if callback_url else [],
            "oidc_post_logout_redirect_uris": [],
            "oidc_public_client": False,
            "allowed_auth_methods": allowed_methods or ["email"],
            "telegram_bot_token": None,
            "enabled_services": {
                "sso": True,
                "phone_otp": True,
                "email_otp": True,
                "secrets_vault": True,
                "payment_bot": True,
                "heimdall_monitor": True
            },
            "created_at": datetime.now(UTC)
        }
        self.db.applications.insert_one(app_doc)

        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "webhook_secret": webhook_secret
        }

    def update_app_details(self, app_id, data):
        """Updates non-sensitive app details."""
        allowed_fields = ['app_name', 'app_callback_url', 'app_web_url', 'app_api_url', 'app_logo_url', 'app_qr_url', 'telegram_bot_token', 'enabled_services', 'custom_domain', 'db_connection', 'db_mode', 'db_schema',
                          'notification_configs', 'platform_locked_tables', 'payment_methods',
                          'tenant_id', 'oidc_redirect_uris', 'oidc_post_logout_redirect_uris',
                          'oidc_public_client', 'tenant_type', 'role_permissions']
        updates = {k: v for k, v in data.items() if k in allowed_fields}

        if updates:
            self.db.applications.update_one(
                {"_id": ObjectId(app_id)},
                {"$set": updates}
            )
            self._trigger_event_for_app(app_id, "config_updated")
            return True
        return False

    def rotate_app_secret(self, app_id):
        """Regenerates the Client Secret for an App."""
        new_secret = secrets.token_urlsafe(32)
        self.db.applications.update_one(
            {"_id": ObjectId(app_id)},
            {"$set": {"client_secret_hash": generate_password_hash(new_secret)}}
        )
        self._trigger_event_for_app(app_id, "config_updated")
        return new_secret

    def add_app_api_key(self, app_id, key_name, key_value):
        """Adds or updates an API Key for an application using its own webhook_secret."""
        from ..utils.encryption import encrypt_value
        
        # Fetch app to get its webhook_secret
        app = self.db.applications.find_one({"_id": ObjectId(app_id)})
        if not app or "webhook_secret" not in app:
            return False
            
        secret = app["webhook_secret"]
        safe_key_name = key_name.strip().upper()
        encrypted_value = encrypt_value(key_value, secret)
        
        self.db.applications.update_one(
            {"_id": ObjectId(app_id)},
            {"$set": {f"api_keys.{safe_key_name}": encrypted_value}}
        )
        self._trigger_event_for_app(app_id, "config_updated")
        return True

    def remove_app_api_key(self, app_id, key_name):
        """Removes an API Key from an application."""
        self.db.applications.update_one(
            {"_id": ObjectId(app_id)},
            {"$unset": {f"api_keys.{key_name}": ""}}
        )
        self._trigger_event_for_app(app_id, "config_updated")
        return True

    def get_app_by_client_id(self, client_id):
        return self.db.applications.find_one({"client_id": client_id})

    def verify_client_secret(self, client_id, provided_secret):
        app = self.get_app_by_client_id(client_id)
        if not app:
            return False
        return check_password_hash(app["client_secret_hash"], provided_secret)

    def link_user_to_app(self, account_id, app_id, role=None, duration_str=None, suppress_webhook=False):
        """
        Links a user to an app.
        STRICT: Writes to app_specific_role only.
        """
        # --- OWNER LOGIC: Enforce Single Owner ---
        if role == 'owner':
            existing_owners = self.db.app_links.find({
                "app_id": ObjectId(app_id),
                "app_specific_role": "owner",
                "account_id": {"$ne": ObjectId(account_id)}
            })

            for owner_link in existing_owners:
                log.info(f"👑 Ownership Transfer: Demoting {owner_link['account_id']} to Super Admin.")
                # Downgrade previous owner to Super Admin
                self.link_user_to_app(
                    account_id=owner_link['account_id'],
                    app_id=app_id,
                    role="super_admin",
                    duration_str="lifetime",
                    suppress_webhook=False
                )
        # ------------------------------------------

        current_link = self.db.app_links.find_one({"account_id": ObjectId(account_id), "app_id": ObjectId(app_id)})
        old_role = current_link.get('app_specific_role') if current_link else None

        target_role = role
        if target_role is None:
            target_role = old_role if old_role else "user"

        update_doc = {
            "last_login": datetime.now(UTC),
            "app_specific_role": target_role,  # <--- STRICT WRITE
            "role": target_role                # Legacy support
        }

        if duration_str and duration_str != 'lifetime':
            now = datetime.now(UTC)
            expires_at = None
            if duration_str == '1m':
                expires_at = now + timedelta(days=30)
            elif duration_str == '3m':
                expires_at = now + timedelta(days=90)
            elif duration_str == '6m':
                expires_at = now + timedelta(days=180)
            elif duration_str == '1y':
                expires_at = now + timedelta(days=365)

            if expires_at:
                update_doc["expires_at"] = expires_at

        if duration_str == 'lifetime':
            update_doc["expires_at"] = None

        update_op = {
            "$set": update_doc,
            "$setOnInsert": {"linked_at": datetime.now(UTC)}
        }
        if target_role != "user":
            update_op["$unset"] = {"warning_sent": ""}  # Clear warning flag on upgrade

        self.db.app_links.update_one(
            {"account_id": ObjectId(account_id), "app_id": ObjectId(app_id)},
            update_op,
            upsert=True
        )

        if old_role != target_role and not suppress_webhook:
            self._trigger_event_for_user(account_id, "account_role_change", specific_app_id=app_id)

    def activate_free_trial(self, account_id, app_id, duration_str="14d"):
        """
        Activates a one-time free trial for the user on a specific app.
        """
        query = {"account_id": ObjectId(account_id), "app_id": ObjectId(app_id)}
        link = self.db.app_links.find_one(query)

        if link and link.get('trial_used'):
            return False, "Free trial has already been used."

        now = datetime.now(UTC)
        expires_at = now + timedelta(days=14)

        update_doc = {
            "app_specific_role": "premium_user",
            "role": "premium_user",
            "expires_at": expires_at,
            "trial_used": True,
            "last_login": now
        }

        self.db.app_links.update_one(
            query,
            {"$set": update_doc, "$setOnInsert": {"linked_at": now}},
            upsert=True
        )

        self._trigger_event_for_user(account_id, "account_role_change", specific_app_id=app_id)
        return True, "Free trial activated successfully."

    def remove_user_from_app(self, account_id, app_id, is_self_action=False):
        """
        Completely unlinks a user from an application.
        Verified users (user, premium_user, admin) cannot be removed by anyone except themselves.
        """
        query = {
            "account_id": ObjectId(account_id),
            "app_id": ObjectId(app_id)
        }

        link = self.db.app_links.find_one(query)
        if not link:
            return False, "Link not found."

        if not is_self_action:
            # Compliance check: Check app_specific_role
            current_role = link.get('app_specific_role', 'user')
            if current_role not in ['guest', 'banned']:
                return False, "COMPLIANCE ERROR: Verified users cannot be removed by Admins. They must delete their own account."

        result = self.db.app_links.delete_one(query)

        if result.deleted_count > 0:
            self._trigger_event_for_user(
                account_id=account_id,
                event_type="account_role_change",
                specific_app_id=app_id,
                extra_data={"new_role": "removed", "reason": "admin_removed" if not is_self_action else "self_deleted"}
            )
            return True, "User removed successfully."

        return False, "Database error during removal."

    def get_user_role_for_app(self, account_id, app_id):
        log.info(f"🔍 DEBUG: Checking role for Account {account_id} in App {app_id}")
        link = self.db.app_links.find_one({"account_id": ObjectId(account_id), "app_id": ObjectId(app_id)})

        if not link:
            log.info(f"❌ DEBUG: No App Link found for Account {account_id}. Defaulting to None.")
            return None

        # STRICT READ: We ONLY check app_specific_role.
        role = link.get('app_specific_role', 'user')
        expires_at = link.get('expires_at')

        log.info(f"📄 DEBUG: Found Link: Role='{role}', Expires={expires_at}")

        if expires_at:
            # Ensure timezone awareness for accurate comparison
            now = datetime.now(UTC)
            # Handle case where stored date might be naive (older data) or aware
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            else:
                expires_at = expires_at.astimezone(UTC)

            log.info(f"⏳ DEBUG: Expiration Check: Now={now} vs Expires={expires_at}")

            if expires_at < now:
                log.info("🚫 DEBUG: Subscription EXPIRED")
                return "expired"

        return role

    # ---------------------------------------------------------
    # BACKOFFICE & HEIMDALL HELPERS
    # ---------------------------------------------------------
    def is_heimdall(self, email):
        if not email: return False
        admin = self.db.admins.find_one({"email": email.lower()})
        return admin and admin.get('role') == 'heimdall'

    def get_managed_apps(self, account_id):
        """Returns apps this user may open the console for."""
        from ..backoffice import CONSOLE_ROLES
        # STRICT: check app_specific_role
        links = self.db.app_links.find({
            "account_id": ObjectId(account_id),
            "app_specific_role": {"$in": list(CONSOLE_ROLES)}
        })
        app_ids = [link['app_id'] for link in links]
        return list(self.db.applications.find({"_id": {"$in": app_ids}}))

    def get_all_apps(self):
        return list(self.db.applications.find({}))

    def get_app_users(self, app_id):
        links = list(self.db.app_links.find({"app_id": ObjectId(app_id)}))
        if not links:
            return []

        user_ids = [link['account_id'] for link in links]
        users_cursor = self.db.accounts.find({"_id": {"$in": user_ids}})
        users_map = {u['_id']: u for u in users_cursor}

        results = []
        for link in links:
            user = users_map.get(link['account_id'])
            if user:
                # MAPPING: We read 'app_specific_role' but return it as 'role' key
                # for API backward compatibility, assuming frontend expects 'role'.
                results.append({
                    "account_id": str(user['_id']),
                    "display_name": user.get('display_name'),
                    "email": user.get('email'),
                    "username": user.get('username'),
                    "telegram_id": user.get('telegram_id'),
                    "role": link.get('app_specific_role', 'user'),
                    "expires_at": link.get('expires_at'),
                    "linked_at": link.get('linked_at')
                })
        return results

    def get_app_owner(self, app_id):
        # STRICT: check app_specific_role
        link = self.db.app_links.find_one({
            "app_id": ObjectId(app_id),
            "app_specific_role": "owner"
        })
        if link:
            return self.db.accounts.find_one({"_id": link['account_id']})
        return None
    # ---------------------------------------------------------
    # TENANT INTAKE (request -> approval -> application)
    # ---------------------------------------------------------
    # A request is inert: it creates no application, no account and no
    # credentials until a platform admin approves it. That is the whole point of
    # having it — the intake form is public, so nothing it says can be trusted
    # until a human at Bifrost has looked at it.
    REQUEST_FIELDS = ('app_name', 'callback_url', 'web_url', 'api_url', 'logo_url',
                      'admin_email', 'payments_enabled', 'pay_payway', 'pay_manual',
                      'qr_url', 'notes')

    def create_tenant_request(self, form):
        """Stores an intake form verbatim (minus anything secret) as pending."""
        doc = {k: (form.get(k) or '').strip() for k in self.REQUEST_FIELDS}
        doc.update({
            "status": "pending",
            "created_at": datetime.now(UTC),
            "decided_at": None,
            "decided_by": None,
            "decision_reason": None,
            "client_id": None,
        })
        return str(self.db.tenant_requests.insert_one(doc).inserted_id)

    def list_tenant_requests(self, status=None):
        query = {"status": status} if status else {}
        return list(self.db.tenant_requests.find(query).sort("created_at", -1))

    def get_tenant_request(self, request_id):
        return self.db.tenant_requests.find_one({"_id": ObjectId(request_id)})

    def decide_tenant_request(self, request_id, status, actor, reason=None, client_id=None):
        """Records a decision. Only a pending request can be decided.

        The status guard is what makes approval idempotent: a double-submitted
        approve cannot provision the same tenant twice.
        """
        res = self.db.tenant_requests.update_one(
            {"_id": ObjectId(request_id), "status": "pending"},
            {"$set": {
                "status": status,
                "decided_at": datetime.now(UTC),
                "decided_by": actor,
                "decision_reason": reason,
                "client_id": client_id,
            }}
        )
        return res.modified_count == 1

import random
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo
from bson import ObjectId
from werkzeug.security import generate_password_hash
import logging

log = logging.getLogger(__name__)
UTC = ZoneInfo("UTC")

# Explicit opt-out of directory scoping, for platform paths that legitimately
# search every tenant (console sign-in, Heimdall). Passing it is a decision;
# omitting the scope entirely is a TypeError rather than a silent global search.
ANY_TENANT = "*"


def scoped(query, directory):
    """Constrain an account lookup to one directory.

    Accounts are partitioned per directory, so an unscoped lookup can return
    another tenant's user. Callers must say which directory they mean, or say
    ANY_TENANT out loud.
    """
    if directory == ANY_TENANT:
        return query
    if not directory:
        raise ValueError(
            "account lookup requires a directory scope "
            "(db.directory_scope(app_config), or ANY_TENANT to search all tenants)"
        )
    query["client_id"] = directory
    return query


class AuthMixin:
    # ---------------------------------------------------------
    # OTP UTILITIES
    # ---------------------------------------------------------
    def create_otp(self, identifier, channel="email", account_id=None):
        """
        Generates a 6-digit OTP.
        CRITICAL: Deletes any existing codes for this identifier/channel to prevent
        user confusion (entering an old valid code vs a new valid code).
        """
        identifier = str(identifier).lower()

        # 1. Invalidate previous codes for this specific flow to ensure only the LATEST works
        self.db.verification_codes.delete_many({
            "identifier": identifier,
            "channel": channel
        })

        code = str(random.randint(100000, 999999))
        doc = {
            "code": code,
            "identifier": identifier,
            "channel": channel,
            "created_at": datetime.now(UTC)
        }
        if account_id:
            doc["account_id"] = str(account_id)

        result = self.db.verification_codes.insert_one(doc)
        # Never the code: anyone with log access could spend a live OTP before it
        # expires. The insert id is what correlates a send with its verification.
        log.info(f"✅ OTP Created: Channel={channel}, ID={identifier}, VID={result.inserted_id}")
        return code, str(result.inserted_id)

    def create_login_code(self, telegram_id):
        code, _ = self.create_otp(telegram_id, channel="telegram")
        return code

    def create_deep_link_token(self, account_id):
        """Generates a secure, long-string token for Deep Linking."""
        token = secrets.token_urlsafe(16)
        doc = {
            "code": token,
            "identifier": "deep_link",
            "account_id": str(account_id),
            "channel": "deep_link",
            "created_at": datetime.now(UTC)
        }
        self.db.verification_codes.insert_one(doc)
        log.info(f"🔗 Deep Link Token Created for Account {account_id}")
        return token

    def verify_otp(self, identifier=None, code=None, verification_id=None):
        """
        Verifies and consumes an OTP.
        """
        # Aggressive cleaning: remove spaces, newlines, tabs
        safe_code = "".join(str(code).split()) if code else None

        query = {"code": safe_code}

        if verification_id:
            try:
                query["_id"] = ObjectId(verification_id)
            except:
                log.warning(f"❌ OTP Verification failed: Invalid ObjectId format '{verification_id}'")
                return False
        elif identifier:
            query["identifier"] = str(identifier).lower()

        if not identifier and not verification_id and not safe_code:
            return False

        # Atomic find and delete
        record = self.db.verification_codes.find_one_and_delete(query)

        if record:
            log.info(f"✅ OTP Verified and Consumed for {record.get('identifier')}")
            return record

        log.warning(f"❌ OTP Verification failed: No matching record found for code ending in ...{safe_code[-2:] if safe_code else 'None'}")
        return False

    def verify_and_consume_code(self, code):
        safe_code = "".join(str(code).split()) if code else None
        log.info(f"🔍 Attempting to verify Telegram code: '{safe_code}'")
        query = {"code": safe_code, "channel": "telegram"}
        record = self.db.verification_codes.find_one_and_delete(query)
        if record:
            return record['identifier']
        return None

    # ---------------------------------------------------------
    # ACCOUNT MANAGEMENT
    # ---------------------------------------------------------
    def create_account(self, data):
        account = {
            "client_id": data.get("client_id"),
            "display_name": data.get("display_name", "Unknown User"),
            "is_active": True,
            "created_at": datetime.now(UTC),
            "auth_providers": data.get("auth_providers", [])
        }

        if data.get("email"):
            account["email"] = data.get("email").lower()
        if data.get("username"):
            account["username"] = data.get("username").lower()
        if data.get("password"):
            account["password_hash"] = generate_password_hash(data["password"])
        if data.get("telegram_id"):
            account["telegram_id"] = str(data.get("telegram_id"))
        if data.get("google_id"):
            account["google_id"] = data.get("google_id")
        if data.get("phone_number"):
            account["phone_number"] = data.get("phone_number")

        return self.db.accounts.insert_one(account).inserted_id

    def find_account_by_email(self, email, directory):
        if not email: return None
        return self.db.accounts.find_one(scoped({"email": email.lower()}, directory))

    def find_account_by_username(self, username, directory):
        if not username: return None
        return self.db.accounts.find_one(scoped({"username": username.lower()}, directory))

    def find_account_by_id(self, account_id):
        # _id is already unique across directories, so this needs no scope.
        try:
            return self.db.accounts.find_one({"_id": ObjectId(account_id)})
        except Exception:
            return None

    def find_account_by_telegram(self, telegram_id, directory):
        return self.db.accounts.find_one(scoped({"telegram_id": str(telegram_id)}, directory))

    def find_account_by_phone(self, phone, directory):
        if not phone: return None
        return self.db.accounts.find_one(scoped({"phone_number": str(phone).strip()}, directory))

    def find_account_by_sso(self, provider, provider_id, directory):
        if not provider or not provider_id: return None
        query = {
            "$or": [
                {f"{provider}_id": str(provider_id)},
                {f"identities.{provider}.id": str(provider_id)}
            ]
        }
        return self.db.accounts.find_one(scoped(query, directory))

    def update_password(self, email, new_password, directory):
        user = self.find_account_by_email(email, directory)
        if not user:
            return

        self.db.accounts.update_one(
            {"_id": user['_id']},
            {"$set": {"password_hash": generate_password_hash(new_password)}}
        )
        self._trigger_event_for_user(user['_id'], "security_password_change")

    def directory_of(self, account_id):
        """The directory an existing account lives in.

        Uniqueness checks below take their scope from the account itself rather
        than from the caller: an identifier only has to be unique inside its own
        directory, and reading it off the account is one fewer argument to get
        wrong.
        """
        account = self.find_account_by_id(account_id)
        return (account or {}).get('client_id')

    def link_email_credentials(self, account_id, email, password, client_id=None):
        email = email.lower()
        query = scoped({"email": email, "_id": {"$ne": ObjectId(account_id)}},
                       self.directory_of(account_id))

        existing = self.db.accounts.find_one(query)
        if existing:
            return False, "Email is already associated with another account."

        result = self.db.accounts.update_one(
            {"_id": ObjectId(account_id)},
            {
                "$set": {"email": email, "password_hash": generate_password_hash(password)},
                "$addToSet": {"auth_providers": "email"}
            }
        )

        if result.modified_count > 0:
            # SEND UPDATED DATA IN WEBHOOK
            self._trigger_event_for_user(
                account_id,
                "account_update",
                extra_data={"email": email}
            )
            return True, "Account linked successfully."
        else:
            return False, "Account not found."

    def link_telegram(self, account_id, telegram_id, display_name, client_id):
        telegram_id = str(telegram_id)
        existing = self.db.accounts.find_one(scoped(
            {"telegram_id": telegram_id, "_id": {"$ne": ObjectId(account_id)}},
            self.directory_of(account_id)))
        if existing:
            return False, "Telegram account already linked to another user."

        updates = {"telegram_id": telegram_id}
        result = self.db.accounts.update_one(
            {"_id": ObjectId(account_id)},
            {
                "$set": updates,
                "$addToSet": {"auth_providers": "telegram"}
            }
        )

        if result.modified_count > 0:
            # SEND UPDATED DATA IN WEBHOOK
            self._trigger_event_for_user(
                account_id,
                "account_update",
                extra_data={"telegram_id": telegram_id}
            )
            return True, "Telegram linked."
        else:
            return False, "Account not found."

    def link_sso(self, account_id, provider, provider_id):
        provider_id = str(provider_id)
        existing = self.db.accounts.find_one(scoped({
            "$or": [
                {f"{provider}_id": provider_id},
                {f"identities.{provider}.id": provider_id}
            ],
            "_id": {"$ne": ObjectId(account_id)}
        }, self.directory_of(account_id)))
        if existing:
            return False, f"{provider.capitalize()} account already linked to another user."

        result = self.db.accounts.update_one(
            {"_id": ObjectId(account_id)},
            {
                "$set": {
                    f"identities.{provider}": {
                        "id": provider_id,
                        "linked_at": datetime.now(UTC)
                    },
                    f"{provider}_id": provider_id
                },
                "$addToSet": {"auth_providers": provider}
            }
        )

        if result.modified_count > 0:
            self._trigger_event_for_user(
                account_id,
                "account_update",
                extra_data={f"{provider}_id": provider_id}
            )
            return True, f"{provider.capitalize()} linked."
        else:
            return False, "Account not found."

    def update_account_profile(self, account_id, updates, client_id=None):
        directory = self.directory_of(account_id)

        if 'email' in updates:
            updates['email'] = updates['email'].lower()
            existing = self.db.accounts.find_one(scoped(
                {"email": updates['email'], "_id": {"$ne": ObjectId(account_id)}}, directory))
            if existing:
                return False, "Email is already in use by another account."

        if 'username' in updates:
            updates['username'] = updates['username'].lower()
            existing = self.db.accounts.find_one(scoped(
                {"username": updates['username'], "_id": {"$ne": ObjectId(account_id)}}, directory))
            if existing:
                return False, "Username is already taken."

        result = self.db.accounts.update_one({"_id": ObjectId(account_id)}, {"$set": updates})

        if result.matched_count > 0:
            # SEND UPDATED DATA IN WEBHOOK
            self._trigger_event_for_user(
                account_id,
                "account_update",
                extra_data=updates
            )
            return True, "Profile updated."
        else:
            return False, "Account not found."

    def delete_account(self, account_id):
        from bson import ObjectId
        result = self.db.accounts.delete_one({"_id": ObjectId(account_id)})
        # Also clean up any associated sessions or linked apps in Bifrost
        self.db.apps_users.delete_many({"user_id": ObjectId(account_id)})
        # A deleted account must not keep a live refresh token behind it.
        self.db.oidc_refresh_tokens.delete_many({"user_id": ObjectId(account_id)})
        self.db.auth_codes.delete_many({"user_id": ObjectId(account_id)})
        return result.deleted_count > 0
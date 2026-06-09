# bifrost/models/payments.py
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bson import ObjectId
import logging

log = logging.getLogger(__name__)
UTC = ZoneInfo("UTC")


class PaymentMixin:
    # ---------------------------------------------------------
    # TRANSACTION MANAGEMENT
    # ---------------------------------------------------------
    def create_transaction(self, account_id, app_id, amount, currency, description, target_role="premium_user",
                           duration="1m", client_ref_id=None, app_name=None):
        """Creates a pending transaction record."""
        tx_id = f"tx-{secrets.token_hex(8)}"

        # Handle account_id being None (for pre-login intents)
        acc_oid = ObjectId(account_id) if account_id else None

        tx_doc = {
            "transaction_id": tx_id,
            "account_id": acc_oid,
            "app_id": ObjectId(app_id),
            "app_name": app_name,
            "amount": amount,
            "currency": currency,
            "description": description,
            "status": "pending",
            "target_role": target_role,
            "duration": duration,
            "client_ref_id": client_ref_id,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "provider_ref": None
        }
        self.db.transactions.insert_one(tx_doc)
        return tx_id

    def get_transaction(self, transaction_id):
        return self.db.transactions.find_one({"transaction_id": transaction_id})

    def complete_transaction(self, transaction_id, provider_ref=None):
        """
        Marks a transaction as completed and grants the role.
        STRICT MODE: Writes ONLY to 'app_specific_role'.
        Legacy 'role' field is completely deprecated.
        """
        tx = self.db.transactions.find_one({"transaction_id": transaction_id})
        if not tx:
            return False, "Transaction not found"

        if tx['status'] == 'completed':
            return True, "Already completed"

        # 1. Update Transaction Status
        self.db.transactions.update_one(
            {"_id": tx['_id']},
            {
                "$set": {
                    "status": "completed",
                    "provider_ref": provider_ref,
                    "updated_at": datetime.now(UTC)
                }
            }
        )

        # 2. Calculate Expiration
        duration = tx.get('duration')
        expires_at = None
        if duration:
            now = datetime.now(UTC)
            if duration == '1m':
                expires_at = now + timedelta(days=30)
            elif duration == '3m':
                expires_at = now + timedelta(days=90)
            elif duration == '6m':
                expires_at = now + timedelta(days=180)
            elif duration == '1y':
                expires_at = now + timedelta(days=365)
            elif duration == 'lifetime':
                expires_at = None

        # 3. Grant Role (STRICT)
        # We exclusively use 'app_specific_role' for ALL apps.
        target_role = tx.get('target_role', 'premium_user')

        update_doc = {
            "app_specific_role": target_role,
            "role": target_role,  # Legacy support
            "last_login": datetime.now(UTC)
        }

        if expires_at:
            update_doc["expires_at"] = expires_at
        elif duration == 'lifetime':
            # Explicitly clear expiration for lifetime
            update_doc["expires_at"] = None

        # 4. Perform Update
        self.db.app_links.update_one(
            {
                "account_id": tx['account_id'],
                "app_id": tx['app_id']
            },
            {
                "$set": update_doc,
                "$setOnInsert": {"linked_at": datetime.now(UTC)},
                "$unset": {"warning_sent": ""}  # Clear warning flag on upgrade
            },
            upsert=True
        )

        log.info(f"✅ Transaction {transaction_id} completed. Granted '{target_role}' to {tx['account_id']} in app_specific_role.")

        # --- FIX: Trigger Webhook automatically on completion ---
        self._trigger_event_for_user(
            account_id=tx['account_id'],
            event_type="subscription_success",
            specific_app_id=tx['app_id'],
            extra_data={
                "transaction_id": transaction_id,
                "amount": tx.get('amount'),
                "currency": tx.get('currency', 'USD'),
                "role": target_role,
                "duration": duration,
                "expires_at": expires_at.isoformat() if expires_at else None
            }
        )

        # 5. Return Data for general processing
        return True, {
            "account_id": str(tx['account_id']),
            "app_id": str(tx['app_id']),
            "role": target_role,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "duration": duration
        }

    def save_pending_payment(self, trx_id, amount, currency, raw_text, payer_name):
        try:
            if self.db.payment_logs.find_one({"trx_id": trx_id}):
                return False

            self.db.payment_logs.insert_one({
                "trx_id": trx_id,
                "amount": float(amount),
                "currency": currency,
                "payer_name": payer_name,
                "raw_text": raw_text,
                "status": "unclaimed",
                "claimed_by_account_id": None,
                "created_at": datetime.now(UTC)
            })
            return True
        except Exception as e:
            log.error(f"Error saving payment log: {e}")
            return False

    def claim_payment(self, trx_input, app_id, user_identity):
        # 1. Resolve User
        user = None
        if 'account_id' in user_identity:
            user = self.find_account_by_id(user_identity['account_id'])
        elif 'telegram_id' in user_identity:
            user = self.find_account_by_telegram(user_identity['telegram_id'])
        elif 'email' in user_identity:
            user = self.find_account_by_email(user_identity['email'])

        if not user:
            return False, "User account not found."

        # 2. Fuzzy Match Payment
        safe_input = str(trx_input).strip()
        regex_pattern = f"{safe_input}$"

        payment = self.db.payment_logs.find_one({
            "status": "unclaimed",
            "trx_id": {"$regex": regex_pattern}
        })

        if not payment:
            return False, "Transaction ID not found or already claimed."

        # 3. Atomic Claim
        result = self.db.payment_logs.update_one(
            {"_id": payment['_id'], "status": "unclaimed"},
            {
                "$set": {
                    "status": "claimed",
                    "claimed_by_account_id": user['_id'],
                    "claimed_for_app_id": ObjectId(app_id),
                    "claimed_method": list(user_identity.keys())[0],
                    "claimed_at": datetime.now(UTC)
                }
            }
        )

        if result.modified_count == 0:
            return False, "Error: Payment claimed by someone else."

        # 4. Grant Premium Role (Claims currently default to 1 Month if not specified)
        self.link_user_to_app(user['_id'], app_id, role="premium_user", suppress_webhook=True)

        # 5. Send Success Webhook for Claims
        self._trigger_event_for_user(
            account_id=user['_id'],
            event_type="subscription_success",
            specific_app_id=app_id,
            extra_data={
                "transaction_id": payment['trx_id'],
                "amount": payment['amount'],
                "currency": payment['currency'],
                "role": "premium_user",
                "method": "claim"
            }
        )

        return True, f"Success! ${payment['amount']} claimed."
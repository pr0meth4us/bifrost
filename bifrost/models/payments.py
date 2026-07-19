# bifrost/models/payments.py
import re
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

    # ---------------------------------------------------------
    # MULTI-TENANT POSTGRESQL PROXIED MANUAL PAYMENTS
    # ---------------------------------------------------------
    def get_manual_payments(self, db_conn_str, status_filter=None):
        """Fetches manual payments from the tenant's PostgreSQL database."""
        from bifrost.utils.tenant_db import get_tenant_db
        from decimal import Decimal
        sql = """
        SELECT p.id, p.user_id, p.amount, p.txn_ref, p.receipt_url, p.status, p.reviewed_at, u.email
        FROM payments p
        LEFT JOIN users u ON p.user_id = u.id
        """
        params = []
        if status_filter:
            sql += " WHERE p.status = %s"
            params.append(status_filter)
        sql += " ORDER BY p.id DESC"
        
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                columns = [desc[0] for desc in cur.description]
                results = []
                for row in cur.fetchall():
                    d = dict(zip(columns, row))
                    for k, v in d.items():
                        if isinstance(v, Decimal):
                            d[k] = float(v)
                    results.append(d)
                return results

    def get_manual_payment_by_id(self, db_conn_str, payment_id):
        """Fetches a single manual payment from the tenant's PostgreSQL database."""
        from bifrost.utils.tenant_db import get_tenant_db
        from decimal import Decimal
        sql = """
        SELECT p.id, p.user_id, p.amount, p.txn_ref, p.receipt_url, p.status, p.reviewed_at, u.email
        FROM payments p
        LEFT JOIN users u ON p.user_id = u.id
        WHERE p.id = %s
        """
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, [payment_id])
                row = cur.fetchone()
                if row:
                    columns = [desc[0] for desc in cur.description]
                    d = dict(zip(columns, row))
                    for k, v in d.items():
                        if isinstance(v, Decimal):
                            d[k] = float(v)
                    return d
                return None

    def approve_manual_payment(self, db_conn_str, payment_id, track_id, reviewer_id):
        """Atomically approves payment and activates tenant premium entitlement."""
        from bifrost.utils.tenant_db import get_tenant_db
        payment = self.get_manual_payment_by_id(db_conn_str, payment_id)
        if not payment:
            return False, "Payment record not found."
        
        user_id = payment["user_id"]
        
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE payments SET status = 'approved', reviewed_by = %s, reviewed_at = NOW() WHERE id = %s",
                    [reviewer_id, payment_id]
                )
                cur.execute(
                    """
                    INSERT INTO entitlements (user_id, exam_track_id, status, activated_at)
                    VALUES (%s, %s, 'premium', NOW())
                    ON CONFLICT (user_id, exam_track_id)
                    DO UPDATE SET status = 'premium', activated_at = NOW();
                    """,
                    [user_id, track_id]
                )
                conn.commit()
        return True, payment

    def reject_manual_payment(self, db_conn_str, payment_id, reviewer_id, reason):
        """Rejects manual payment with reason logged defensively."""
        from bifrost.utils.tenant_db import get_tenant_db
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='payments'")
                columns = [r[0] for r in cur.fetchall()]
                
                if 'reject_reason' in columns:
                    cur.execute(
                        "UPDATE payments SET status = 'rejected', reviewed_by = %s, reviewed_at = NOW(), reject_reason = %s WHERE id = %s",
                        [reviewer_id, reason, payment_id]
                    )
                elif 'notes' in columns:
                    cur.execute(
                        "UPDATE payments SET status = 'rejected', reviewed_by = %s, reviewed_at = NOW(), notes = %s WHERE id = %s",
                        [reviewer_id, reason, payment_id]
                    )
                else:
                    cur.execute(
                        "UPDATE payments SET status = 'rejected', reviewed_by = %s, reviewed_at = NOW() WHERE id = %s",
                        [reviewer_id, payment_id]
                    )
                conn.commit()
        return True

    def refund_manual_payment(self, db_conn_str, payment_id, track_id, reviewer_id, reason):
        """Atomically marks payment as refunded and revokes premium entitlement."""
        from bifrost.utils.tenant_db import get_tenant_db
        payment = self.get_manual_payment_by_id(db_conn_str, payment_id)
        if not payment:
            return False, "Payment record not found."
        
        user_id = payment["user_id"]
        
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='payments'")
                columns = [r[0] for r in cur.fetchall()]
                
                if 'refund_reason' in columns:
                    cur.execute(
                        "UPDATE payments SET status = 'refunded', reviewed_by = %s, reviewed_at = NOW(), refund_reason = %s WHERE id = %s",
                        [reviewer_id, reason, payment_id]
                    )
                elif 'notes' in columns:
                    cur.execute(
                        "UPDATE payments SET status = 'refunded', reviewed_by = %s, reviewed_at = NOW(), notes = %s WHERE id = %s",
                        [reviewer_id, reason, payment_id]
                    )
                else:
                    cur.execute(
                        "UPDATE payments SET status = 'refunded', reviewed_by = %s, reviewed_at = NOW() WHERE id = %s",
                        [reviewer_id, payment_id]
                    )
                
                cur.execute(
                    "UPDATE entitlements SET status = 'revoked' WHERE user_id = %s AND exam_track_id = %s",
                    [user_id, track_id]
                )
                conn.commit()
        return True, payment

    def suspend_tenant_user(self, db_conn_str, user_id, reason):
        """Suspends user in tenant DB and revokes entitlements."""
        from bifrost.utils.tenant_db import get_tenant_db
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users'")
                cols = [r[0] for r in cur.fetchall()]
                
                if 'status' in cols:
                    cur.execute("UPDATE users SET status = 'suspended' WHERE id = %s", [user_id])
                if 'suspended_at' in cols:
                    cur.execute("UPDATE users SET suspended_at = NOW() WHERE id = %s", [user_id])
                
                cur.execute("UPDATE entitlements SET status = 'rejected' WHERE user_id = %s", [user_id])
                conn.commit()
        return True

    def reinstate_tenant_user(self, db_conn_str, user_id):
        """Reinstates a user in the tenant DB."""
        from bifrost.utils.tenant_db import get_tenant_db
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users'")
                cols = [r[0] for r in cur.fetchall()]
                
                if 'status' in cols:
                    cur.execute("UPDATE users SET status = 'active' WHERE id = %s", [user_id])
                conn.commit()
        return True

    def get_tenant_tables(self, db_conn_str):
        """Fetches the list of tables in the tenant's PostgreSQL database public schema."""
        from bifrost.utils.tenant_db import get_tenant_db
        sql = """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_type='BASE TABLE'
            ORDER BY table_name
        """
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                return [row[0] for row in cur.fetchall()]

    def get_tenant_table_schema(self, db_conn_str, table_name):
        """Returns column metadata: name, data_type, nullable, char_max_length, fk info."""
        from bifrost.utils.tenant_db import get_tenant_db
        sql = """
            SELECT
                c.column_name,
                c.data_type,
                c.udt_name,
                c.is_nullable,
                c.character_maximum_length,
                c.numeric_precision,
                fk.foreign_table,
                fk.foreign_column
            FROM information_schema.columns c
            LEFT JOIN (
                SELECT
                    kcu.column_name,
                    ccu.table_name AS foreign_table,
                    ccu.column_name AS foreign_column
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu 
                    ON tc.constraint_name = kcu.constraint_name
                JOIN information_schema.constraint_column_usage ccu 
                    ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_name = %s
            ) fk ON c.column_name = fk.column_name
            WHERE c.table_schema = 'public' AND c.table_name = %s
            ORDER BY c.ordinal_position
        """
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, [table_name, table_name])
                keys = [d[0] for d in cur.description]
                return [dict(zip(keys, r)) for r in cur.fetchall()]

    def get_tenant_table_data(self, db_conn_str, table_name, limit=50, offset=0, sort_by='id', sort_dir='desc', search_query=None):
        """Fetches rows for a target table with pagination, sorting, and optional search."""
        from bifrost.utils.tenant_db import get_tenant_db
        from decimal import Decimal
        # Defensive check on table_name and sort_dir to avoid SQL injection
        assert re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name), "Invalid table name"
        sort_dir = 'ASC' if sort_dir.lower() == 'asc' else 'DESC'

        # Also validate sort_by against valid columns (fetch schema first)
        schema = self.get_tenant_table_schema(db_conn_str, table_name)
        valid_columns = [col['column_name'] for col in schema]
        if sort_by not in valid_columns:
            sort_by = valid_columns[0] if valid_columns else 'id'

        params = []
        where_clause = ""
        
        # If searching, we cast all text-like columns to text and check ILIKE
        if search_query:
            text_columns = [col['column_name'] for col in schema if col['data_type'] in ('character varying', 'text', 'character')]
            if text_columns:
                search_conditions = []
                for col in text_columns:
                    search_conditions.append(f'"{col}" ILIKE %s')
                    params.append(f'%{search_query}%')
                where_clause = "WHERE " + " OR ".join(search_conditions)

        sql = f'SELECT * FROM "{table_name}" {where_clause} ORDER BY "{sort_by}" {sort_dir} LIMIT %s OFFSET %s'
        params.extend([limit, offset])

        # We also want to get the total count for pagination
        count_sql = f'SELECT COUNT(*) FROM "{table_name}" {where_clause}'
        count_params = params[:-2]

        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(count_sql, count_params)
                total_count = cur.fetchone()[0]

                cur.execute(sql, params)
                columns = [desc[0] for desc in cur.description]
                results = []
                import json
                for row in cur.fetchall():
                    d = dict(zip(columns, row))
                    for k, v in d.items():
                        if isinstance(v, Decimal):
                            d[k] = float(v)
                        elif isinstance(v, datetime):
                            d[k] = v.isoformat()
                        elif isinstance(v, (dict, list)):
                            d[k] = json.dumps(v)
                    results.append(d)
                return columns, results, total_count

    def get_distinct_column_values(self, db_conn_str, table_name, column_name):
        """Returns distinct values for a column — used to build enum selects."""
        from bifrost.utils.tenant_db import get_tenant_db
        assert re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name)
        assert re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', column_name)
        sql = f'SELECT DISTINCT "{column_name}" FROM "{table_name}" WHERE "{column_name}" IS NOT NULL LIMIT 50'
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                return [row[0] for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # CMS CONFIG — per-app schema annotations stored in MongoDB
    # ------------------------------------------------------------------

    def get_cms_config(self, app_id):
        """Returns the CMS config for an app. Creates default if missing."""
        doc = self.db.applications.find_one(
            {"_id": ObjectId(app_id)},
            {"cms_config": 1}
        )
        return (doc or {}).get("cms_config", {})

    def save_cms_config(self, app_id, config):
        """Persists CMS config dict to MongoDB applications document."""
        self.db.applications.update_one(
            {"_id": ObjectId(app_id)},
            {"$set": {"cms_config": config}}
        )
        return True

    def log_cms_mutation(self, app_id, table_name, action, row_id, acting_user, before=None, after=None):
        """Mandatory server-side logging for CMS edits (PRD Sec 8.5)."""
        from datetime import datetime, timezone
        self.db.cms_audit_log.insert_one({
            "app_id": str(app_id),
            "table": table_name,
            "action": action,
            "row_id": row_id,
            "acting_user": acting_user,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "before": before,
            "after": after
        })

    def save_tenant_table_row(self, db_conn_str, table_name, row_id, data, app_id=None, acting_user=None):
        """Updates a row in the tenant database public schema."""
        from bifrost.utils.tenant_db import get_tenant_db
        assert re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name)

        schema = self.get_tenant_table_schema(db_conn_str, table_name)
        valid_columns = {col['column_name'] for col in schema}

        fields = []
        params = []
        for k, v in data.items():
            if k == 'id' or k not in valid_columns:
                continue
            fields.append(f'"{k}" = %s')
            params.append(v if v != '' else None)

        params.append(row_id)
        sql = f'UPDATE "{table_name}" SET {", ".join(fields)} WHERE id = %s RETURNING *'

        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                # Fetch BEFORE state for logging
                cur.execute(f'SELECT * FROM "{table_name}" WHERE id = %s', [row_id])
                before_row = cur.fetchone()
                before_dict = dict(zip([desc[0] for desc in cur.description], before_row)) if before_row else None

                cur.execute(sql, params)
                after_row = cur.fetchone()
                after_dict = dict(zip([desc[0] for desc in cur.description], after_row)) if after_row else None
                conn.commit()

        # Decimal and datetime are not JSON serializable out of the box for MongoDB
        def sanitize_dict(d):
            if not d: return d
            from decimal import Decimal
            out = {}
            for k,v in d.items():
                if isinstance(v, Decimal): out[k] = float(v)
                elif isinstance(v, datetime): out[k] = v.isoformat()
                else: out[k] = v
            return out

        if app_id and acting_user:
            self.log_cms_mutation(app_id, table_name, "UPDATE", row_id, acting_user, sanitize_dict(before_dict), sanitize_dict(after_dict))
        return True

    def insert_tenant_table_row(self, db_conn_str, table_name, data, app_id=None, acting_user=None):
        """Inserts a new row in the tenant database public schema."""
        from bifrost.utils.tenant_db import get_tenant_db
        assert re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name)

        schema = self.get_tenant_table_schema(db_conn_str, table_name)
        valid_columns = {col['column_name'] for col in schema}

        cols, placeholders, params = [], [], []
        for k, v in data.items():
            if k == 'id' or k not in valid_columns:
                continue
            cols.append(f'"{k}"')
            placeholders.append('%s')
            params.append(v if v != '' else None)

        sql = f'INSERT INTO "{table_name}" ({", ".join(cols)}) VALUES ({", ".join(placeholders)}) RETURNING *'

        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                after_row = cur.fetchone()
                after_dict = dict(zip([desc[0] for desc in cur.description], after_row)) if after_row else None
                conn.commit()

        def sanitize_dict(d):
            if not d: return d
            from decimal import Decimal
            out = {}
            for k,v in d.items():
                if isinstance(v, Decimal): out[k] = float(v)
                elif isinstance(v, datetime): out[k] = v.isoformat()
                else: out[k] = v
            return out

        if app_id and acting_user:
            self.log_cms_mutation(app_id, table_name, "CREATE", after_dict.get('id') if after_dict else None, acting_user, None, sanitize_dict(after_dict))
        return True

    def delete_tenant_table_row(self, db_conn_str, table_name, row_id, app_id=None, acting_user=None):
        """Deletes a row from the tenant database public schema."""
        from bifrost.utils.tenant_db import get_tenant_db
        assert re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name)

        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(f'SELECT * FROM "{table_name}" WHERE id = %s', [row_id])
                before_row = cur.fetchone()
                before_dict = dict(zip([desc[0] for desc in cur.description], before_row)) if before_row else None
                
                sql = f'DELETE FROM "{table_name}" WHERE id = %s'
                cur.execute(sql, [row_id])
                conn.commit()

        def sanitize_dict(d):
            if not d: return d
            from decimal import Decimal
            out = {}
            for k,v in d.items():
                if isinstance(v, Decimal): out[k] = float(v)
                elif isinstance(v, datetime): out[k] = v.isoformat()
                else: out[k] = v
            return out

        if app_id and acting_user:
            self.log_cms_mutation(app_id, table_name, "DELETE", row_id, acting_user, sanitize_dict(before_dict), None)
        return True
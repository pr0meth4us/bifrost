import base64
import hashlib
import hmac
import json
import logging
import requests
from flask import current_app
from datetime import datetime

from ..utils.urls import public_url

log = logging.getLogger(__name__)


class PayWayNotConfigured(Exception):
    """The tenant has no PayWay merchant of its own in the vault."""


class PayWayService:
    def __init__(self, app_doc):
        """Binds to one tenant's ABA merchant.

        Credentials come from that app's vault only — there is deliberately no
        platform-wide fallback. A missing key must fail loudly: falling back to a
        shared merchant would silently route this tenant's money into someone
        else's ABA account, which is worse than a 400.
        """
        from ..utils.encryption import app_secret
        self.merchant_id = app_secret(app_doc, 'PAYWAY_MERCHANT_ID')
        self.api_key = app_secret(app_doc, 'PAYWAY_API_KEY')
        if not self.merchant_id or not self.api_key:
            raise PayWayNotConfigured(
                f"App '{(app_doc or {}).get('client_id')}' has no PAYWAY_MERCHANT_ID / "
                f"PAYWAY_API_KEY in its Bifrost vault."
            )
        # Endpoint is not a credential: sandbox vs production, platform default is fine.
        self.api_url = app_secret(app_doc, 'PAYWAY_API_URL', current_app.config['PAYWAY_API_URL'])
        self.public_url = public_url()

    def _generate_hash(self, data_string):
        """Generates the HMAC-SHA512 hash (Base64 of Binary)."""
        if not self.api_key:
            log.error("Missing PAYWAY_API_KEY")
            return None

        # 1. Generate Raw Binary Digest
        signature = hmac.new(
            self.api_key.encode('utf-8'),
            data_string.encode('utf-8'),
            hashlib.sha512
        ).digest()

        # 2. Base64 Encode
        return base64.b64encode(signature).decode('utf-8')

    def create_transaction(self, transaction_id, amount, items, firstname, lastname, email, phone):
        """
        Calls ABA API to generate a KHQR code.
        """
        # 1. Formatting Data
        formatted_amount = "{:.2f}".format(float(amount))

        # Items: Compact JSON -> Base64
        items_json = json.dumps(items, separators=(',', ':'))
        items_base64 = base64.b64encode(items_json.encode('utf-8')).decode('utf-8')

        req_time = datetime.now().strftime('%Y%m%d%H%M%S')

        # FIX: Base64 Encode the return_url
        # This prevents URL characters from breaking the hash concatenation on the server
        raw_return_url = f"{self.public_url}/internal/payments/callback"
        return_url_b64 = base64.b64encode(raw_return_url.encode('utf-8')).decode('utf-8')

        # Standard Fields
        payment_option = "abapay_khqr"
        purchase_type = "purchase"  # Value for the 'type' field
        currency = "USD"

        # Optional/Empty Fields (Required for Hash Order)
        gdt = ""
        shipping = ""
        ctid = ""
        pwt = ""
        cancel_url = ""
        continue_success_url = ""
        return_deeplink = ""
        topup_channel = ""
        custom_fields = ""
        return_params = ""

        # 2. GENERATE HASH - STRICT ORDER
        # Using return_url_b64 here
        hash_str = (
            f"{req_time}"
            f"{self.merchant_id}"
            f"{transaction_id}"
            f"{formatted_amount}"
            f"{items_base64}"
            f"{gdt}"
            f"{shipping}"
            f"{ctid}"
            f"{pwt}"
            f"{firstname}"
            f"{lastname}"
            f"{email}"
            f"{phone}"
            f"{purchase_type}"
            f"{payment_option}"
            f"{return_url_b64}"  # Using Base64 version
            f"{cancel_url}"
            f"{continue_success_url}"
            f"{return_deeplink}"
            f"{topup_channel}"
            f"{currency}"
            f"{custom_fields}"
            f"{return_params}"
        )

        signature = self._generate_hash(hash_str)

        if not signature:
            log.error("Failed to generate hash signature")
            return None

        # 3. Build JSON Payload
        # FIX: Changed key 'purchase_type' -> 'type'
        # FIX: Sending 'return_url' as the Base64 string
        payload = {
            "req_time": req_time,
            "merchant_id": self.merchant_id,
            "tran_id": transaction_id,
            "first_name": firstname,
            "last_name": lastname,
            "email": email,
            "phone": phone,
            "amount": formatted_amount,
            "type": purchase_type,  # Correct key name is 'type'
            "payment_option": payment_option,
            "items": items_base64,
            "currency": currency,
            "return_url": return_url_b64,  # Send Base64 encoded URL
            "hash": signature,
            "lifetime": 60,
            "qr_image_template": "template3_color"
        }

        # 4. Execute Request
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

        try:
            log.info(f"Sending QR Request to ABA: {self.api_url}")
            response = requests.post(
                self.api_url,
                json=payload,
                headers=headers,
                timeout=30
            )

            try:
                data = response.json()
            except json.JSONDecodeError:
                log.error(f"ABA returned non-JSON: {response.text[:200]}")
                return None

            status_code = str(data.get('status', {}).get('code'))

            if status_code == '0':
                return {
                    "qr_string": data.get('qrString'),
                    "deeplink": data.get('abapay_deeplink'),
                    "description": data.get('description', 'Success')
                }
            else:
                log.error(f"ABA API Error: {data.get('status', {}).get('message')} (Code: {status_code})")
                log.debug(f"Sent Hash String: {hash_str}")
                return None

        except Exception as e:
            log.error(f"ABA Request Failed: {e}")
            return None

    def verify_webhook(self, request_data):
        """
        Verifies the hash received from ABA callback.
        """
        received_hash = request_data.get('hash')
        tran_id = request_data.get('tran_id')
        status = request_data.get('status')
        apv = request_data.get('apv', '')
        req_time = request_data.get('req_time') or request_data.get('request_time') or ""

        if not received_hash or not tran_id:
            return False

        # Hash Construction for Callback:
        local_hash_str = f"{req_time}{self.merchant_id}{tran_id}{status}{apv}"

        expected_hash = self._generate_hash(local_hash_str)

        if hmac.compare_digest(expected_hash, received_hash):
            return True

        log.warning(f"Hash mismatch! Expected: {expected_hash} | Got: {received_hash}")
        return False
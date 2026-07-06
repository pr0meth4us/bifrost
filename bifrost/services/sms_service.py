import logging
import requests
from requests.auth import HTTPBasicAuth
from flask import current_app

log = logging.getLogger(__name__)

def send_otp_sms(to_phone: str, otp: str, app_name: str = "Bifrost Identity") -> bool:
    """
    Sends a Phone OTP SMS code using the Twilio HTTP API.
    If Twilio configuration is absent, it logs the code to stdout (Mock fall-back for local testing).
    """
    account_sid = current_app.config.get('TWILIO_ACCOUNT_SID')
    auth_token = current_app.config.get('TWILIO_AUTH_TOKEN')
    from_number = current_app.config.get('TWILIO_PHONE_NUMBER')

    message_body = f"Your {app_name} verification code is: {otp}. It is valid for 10 minutes."

    if not account_sid or not auth_token or not from_number:
        # Development / Sandbox mock log
        log.warning(f"⚠️ Twilio SMS not configured. MOCK DISPATCH to {to_phone}: '{message_body}'")
        print(f"\n[MOCK SMS SENDER] To: {to_phone} | Body: {message_body}\n")
        return True

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    data = {
        "To": to_phone.strip(),
        "From": from_number.strip(),
        "Body": message_body
    }

    try:
        response = requests.post(
            url,
            data=data,
            auth=HTTPBasicAuth(account_sid, auth_token),
            timeout=10
        )
        if response.status_code in [200, 201]:
            log.info(f"✅ SMS sent successfully to {to_phone} via Twilio.")
            return True
        else:
            log.error(f"❌ Twilio API Error: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        log.error(f"❌ Twilio SMS Dispatch Exception: {e}")
        return False

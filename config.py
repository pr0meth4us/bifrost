import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    MONGO_URI = os.environ.get('MONGO_URI')
    DB_NAME = os.environ.get('DB_NAME', 'bifrost_db')
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY')

    # --- EMAIL SETTINGS ---
    EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
    SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
    SENDER_EMAIL = 'bifrostbyhelm@gmail.com'

    # --- PUBLIC URL ---
    BIFROST_PUBLIC_URL = os.environ.get('BIFROST_PUBLIC_URL', 'http://localhost:5000')

    # --- ABA PAYWAY ---
    PAYWAY_API_URL = os.environ.get('PAYWAY_API_URL', 'https://checkout-sandbox.payway.com.kh/api/payment-gateway/v1/payments/purchase')
    PAYWAY_MERCHANT_ID = os.environ.get('PAYWAY_MERCHANT_ID', 'ec462892')
    PAYWAY_API_KEY = os.environ.get('PAYWAY_API_KEY', '8f43f99f4b8bfb7b050f55f0c2b79858cc237dcb')

    # --- ABA RECURRING PAYMENTS ---
    # Placeholder key/token for recurring auto-renewal integration (Sandbox/Preview)
    ABA_RECURRING_API_TOKEN = os.environ.get('ABA_RECURRING_API_TOKEN')

    # --- GUMROAD (International) ---
    # NO DEFAULT. Must be passed by client or set explicitly in ENV.
    GUMROAD_PRODUCT_PERMALINK = os.environ.get('GUMROAD_PRODUCT_PERMALINK')
    GUMROAD_BASE_URL = "https://gumroad.com/l"

    # --- SSO OAUTH PROVIDERS ---
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
    GITHUB_CLIENT_ID = os.environ.get('GITHUB_CLIENT_ID')
    GITHUB_CLIENT_SECRET = os.environ.get('GITHUB_CLIENT_SECRET')
    MICROSOFT_CLIENT_ID = os.environ.get('MICROSOFT_CLIENT_ID')
    MICROSOFT_CLIENT_SECRET = os.environ.get('MICROSOFT_CLIENT_SECRET')
    APPLE_CLIENT_ID = os.environ.get('APPLE_CLIENT_ID')
    APPLE_CLIENT_SECRET = os.environ.get('APPLE_CLIENT_SECRET')
    FACEBOOK_CLIENT_ID = os.environ.get('FACEBOOK_CLIENT_ID')
    FACEBOOK_CLIENT_SECRET = os.environ.get('FACEBOOK_CLIENT_SECRET')

    # --- TWILIO SMS ---
    TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
    TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
    TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER')

    # --- ADMIN SESSION POLICY (SOW 4.6) ---
    # Console sessions are deliberately shorter-lived than end-user sessions.
    # Idle timeout and max length are enforced in bifrost/backoffice/__init__.py.
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'true').lower() != 'false'

    # --- PLATFORM DATA LOCKS (CMS) ---
    # Platform-level floor only. A tenant may lock MORE tables via
    # `platform_locked_tables` on its app document; see locked_tables_for() in
    # backoffice/tenant_routes.py. The two are unioned, never overridden, so nothing
    # here can be unlocked by config.
    PLATFORM_LOCKED_TABLES = {
        'finance-bot': ['transactions', 'user_balances', 'ledger', 'bank_accounts'],
        'savvify': ['transactions', 'user_balances', 'ledger', 'bank_accounts']
    }

    if not SECRET_KEY or not MONGO_URI or not JWT_SECRET_KEY or not EMAIL_PASSWORD:
        raise RuntimeError("CRITICAL: Missing .env keys (EMAIL_PASSWORD, SECRET_KEY, etc.)")
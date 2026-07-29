import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    MONGO_URI = os.environ.get('MONGO_URI')
    DB_NAME = os.environ.get('DB_NAME', 'bifrost_db')
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY')

    # --- MANAGED BIFROST POSTGRES ---
    MANAGED_POSTGRES_URL = os.environ.get('MANAGED_POSTGRES_URL') or os.environ.get('DATABASE_URL')

    # --- EMAIL SETTINGS ---
    EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
    SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
    SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'bifrostbyhelm@gmail.com')

    # --- PUBLIC URL ---
    BIFROST_PUBLIC_URL = os.environ.get('BIFROST_PUBLIC_URL', 'http://localhost:5000')

    # --- ABA PAYWAY ---
    # Endpoint only. Merchant ID and API key are per-tenant and live in each app's
    # vault (PAYWAY_MERCHANT_ID / PAYWAY_API_KEY); see services/payway.py. A shared
    # platform merchant would pay every tenant's revenue into one ABA account.
    PAYWAY_API_URL = os.environ.get('PAYWAY_API_URL', 'https://checkout-sandbox.payway.com.kh/api/payment-gateway/v1/payments/purchase')

    # --- ABA RECURRING PAYMENTS ---
    # Placeholder key/token for recurring auto-renewal integration (Sandbox/Preview)
    ABA_RECURRING_API_TOKEN = os.environ.get('ABA_RECURRING_API_TOKEN')

    # --- GUMROAD (International) ---
    # Product permalink is per-tenant (vault key GUMROAD_PRODUCT_PERMALINK).
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
    # LEGACY SEED ONLY — do not add tenants here. Locks now live on the app document
    # as `platform_locked_tables`, editable by a platform super-admin in the console,
    # so onboarding a tenant no longer needs a deploy. These two entries stay so the
    # existing apps keep their locks whether or not anyone re-saves them; the two
    # sources are unioned, never overridden, in locked_tables_for().
    PLATFORM_LOCKED_TABLES = {
        'finance-bot': ['transactions', 'user_balances', 'ledger', 'bank_accounts'],
        'savvify': ['transactions', 'user_balances', 'ledger', 'bank_accounts']
    }

    if not SECRET_KEY or not MONGO_URI or not JWT_SECRET_KEY or not EMAIL_PASSWORD:
        raise RuntimeError("CRITICAL: Missing .env keys (EMAIL_PASSWORD, SECRET_KEY, etc.)")
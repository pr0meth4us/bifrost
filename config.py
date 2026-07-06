import os
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

    if not SECRET_KEY or not MONGO_URI or not JWT_SECRET_KEY or not EMAIL_PASSWORD:
        raise RuntimeError("CRITICAL: Missing .env keys (EMAIL_PASSWORD, SECRET_KEY, etc.)")
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Setup minimal logging for config phase
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bifrost-config")

# Robustly find .env file by walking up directories
env_path = Path(__file__).resolve().parent
found_env = False
for _ in range(4):
    potential_env = env_path / '.env'
    if potential_env.exists():
        logger.info(f"✅ Loading environment from: {potential_env}")
        load_dotenv(dotenv_path=potential_env)
        found_env = True
        break
    env_path = env_path.parent

if not found_env:
    logger.warning("⚠️ No .env file found. Relying on System Environment Variables.")

class Config:
    # --- CORE DATABASE & SECURITY ---
    SECRET_KEY = os.environ.get('SECRET_KEY')
    MONGO_URI = os.environ.get('MONGO_URI')
    DB_NAME = os.environ.get('DB_NAME', 'bifrost_db')
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY')

    # --- EMAIL SERVICE ---
    EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
    SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
    SENDER_EMAIL = 'bifrostbyhelm@gmail.com'

    # --- PUBLIC URLS ---
    BIFROST_PUBLIC_URL = os.environ.get('BIFROST_PUBLIC_URL', 'http://localhost:5000')
    BIFROST_BOT_TOKEN = os.environ.get('BIFROST_BOT_TOKEN')
    BIFROST_BOT_SECRET = os.getenv("BIFROST_BOT_SECRET")
    BIFROST_BOT_USERNAME = 'bifrost_byhelm_bot'
    PAYMENT_GROUP_ID = os.environ.get('PAYMENT_GROUP_ID')

    # --- INTERNAL SERVICE AUTH (Bot talking to API) ---
    BIFROST_API_URL = os.environ.get('BIFROST_API_URL', 'http://localhost:8000')
    BIFROST_ROOT_CLIENT_ID = os.environ.get('BIFROST_CLIENT_ID')
    BIFROST_ROOT_CLIENT_SECRET = os.environ.get('BIFROST_CLIENT_SECRET')

    @staticmethod
    def check_critical():
        """Ensures vital keys are present."""
        if not Config.SECRET_KEY or not Config.MONGO_URI:
            raise RuntimeError("CRITICAL: Missing SECRET_KEY or MONGO_URI in environment.")
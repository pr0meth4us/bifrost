import os
import sys
import json
from pymongo import MongoClient
from dotenv import load_dotenv

import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

def get_fernet(secret: str) -> Fernet:
    secret_bytes = secret.encode()
    salt = b"bifrost_api_keys_salt"
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret_bytes))
    return Fernet(key)

def encrypt_value(value: str, secret: str) -> str:
    if not value:
        return value
    f = get_fernet(secret)
    return f.encrypt(value.encode()).decode()

def main():
    load_dotenv("/Users/nicksng/code/finance-bot/bifrost/.env")
    mongo_uri = os.getenv("MONGO_URI")
    if not mongo_uri:
        print("No MONGO_URI found")
        sys.exit(1)

    client = MongoClient(mongo_uri)
    db = client["bifrost_db"]

    app = db.applications.find_one({"client_id": "bifrost_payment_bot_d17a5e6f"})
    if not app:
        print("App bifrost_payment_bot_d17a5e6f not found")
        sys.exit(1)

    webhook_secret = app["webhook_secret"]

    sa_path = "/Users/nicksng/code/random/credentials.json"
    if not os.path.exists(sa_path):
        print(f"File not found: {sa_path}")
        sys.exit(1)

    with open(sa_path, "r") as f:
        sa_content = f.read()

    encrypted_value = encrypt_value(sa_content, webhook_secret)

    db.applications.update_one(
        {"_id": app["_id"]},
        {"$set": {"api_keys.GOOGLE_APPLICATION_CREDENTIALS_JSON": encrypted_value}}
    )
    print("✅ Successfully uploaded Service Account JSON to Bifrost for Auto Texter Trainer.")

if __name__ == "__main__":
    main()

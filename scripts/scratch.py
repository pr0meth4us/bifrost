import sys
import os
from pymongo import MongoClient

# Add bifrost to path to import models
sys.path.append(os.path.dirname(os.path.abspath(".")))

from bifrost.models import BifrostDB
from dotenv import load_dotenv

# Try to load from finance-bot
load_dotenv("/Users/nicksng/code/finance-bot/.env")
mongo_uri = os.environ.get("MONGODB_URI")
db_name = os.environ.get("DB_NAME", "bifrost")

print(f"Connecting to {mongo_uri} / {db_name}")

client = MongoClient(mongo_uri)
db = BifrostDB(client, db_name)

# Create the application
creds = db.register_application("404", "http://localhost:3000/api/auth/callback")
print(f"CLIENT_ID={creds['client_id']}")
print(f"CLIENT_SECRET={creds['client_secret']}")
print(f"WEBHOOK_SECRET={creds['webhook_secret']}")

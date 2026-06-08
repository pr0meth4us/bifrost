import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
from zoneinfo import ZoneInfo
UTC = ZoneInfo("UTC")

mongo_uri = os.environ.get("MONGO_URI", "your_mongo_uri_here")
client = MongoClient(mongo_uri)
db = client['bifrost_db']

account_id = "6917670e409d7063eea82b88"

link = db.app_links.find_one({"account_id": ObjectId(account_id)})
if link:
    print("Link found!")
    role = link.get('app_specific_role', 'user')
    expires_at = link.get('expires_at')
    print(f"Role: {role}, Expires: {expires_at}, Type: {type(expires_at)}")
    
    if expires_at:
        now = datetime.now(UTC)
        try:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            else:
                expires_at = expires_at.astimezone(UTC)
            print("Timezone conversion successful!")
        except Exception as e:
            print(f"Timezone error: {type(e).__name__} - {e}")
else:
    print("Link not found!")

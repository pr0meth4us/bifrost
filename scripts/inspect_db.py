import sys
import os
from pymongo import MongoClient

import os
mongo_uri = os.environ.get("MONGO_URI", "your_mongo_uri_here")
client = MongoClient(mongo_uri)
db = client['bifrost_db']

# Let's find the user's account_id = 6917670e409d7063eea82b88
account_id = "6917670e409d7063eea82b88"

from bson import ObjectId
user = db.accounts.find_one({"_id": ObjectId(account_id)})
print(f"User: {user}")

links = db.app_links.find({"account_id": ObjectId(account_id)})
print("\nApp Links:")
for link in links:
    print(link)

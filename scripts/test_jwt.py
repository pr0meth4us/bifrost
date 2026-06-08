import jwt
from datetime import datetime
from zoneinfo import ZoneInfo
import traceback
from pymongo import MongoClient
from bson import ObjectId

import os
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "your_jwt_secret_here")
client_id = "bifrost_payment_bot_d17a5e6f"
account_id = "6917670e409d7063eea82b88"

# 1. Simulate Token Creation
token_payload = {
    "sub": account_id,
    "iss": "bifrost",
    "aud": client_id,
    "iat": datetime.now(UTC_TZ),
    "exp": datetime.now(UTC_TZ).timestamp() + 3600 * 24 * 7
}
encoded_jwt = jwt.encode(token_payload, JWT_SECRET_KEY, algorithm="HS256")
print(f"Generated JWT: {encoded_jwt}")

# 2. Simulate validate_token
try:
    payload = jwt.decode(
        encoded_jwt,
        JWT_SECRET_KEY,
        algorithms=["HS256"],
        audience=client_id
    )
    print("JWT decodes successfully")
    
    # Simulate DB fetch
    mongo_uri = os.environ.get("MONGO_URI", "your_mongo_uri_here")
    client = MongoClient(mongo_uri)
    db = client['bifrost_db']
    
    app_doc = db.applications.find_one({"client_id": client_id})
    if not app_doc:
        print("App not found!")
    else:
        print("App found")
        
        user = db.accounts.find_one({"_id": ObjectId(payload['sub'])})
        if not user:
            print("User not found")
        else:
            print("User found")
            
            link = db.app_links.find_one({"account_id": ObjectId(payload['sub']), "app_id": app_doc['_id']})
            if link:
                role = link.get('app_specific_role', 'user')
                print(f"Role: {role}")
            else:
                print("Link not found")
except Exception as e:
    print("Exception occurred:")
    traceback.print_exc()

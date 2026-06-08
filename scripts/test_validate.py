import os
import sys
import jwt
from datetime import datetime
from zoneinfo import ZoneInfo
import traceback
from pymongo import MongoClient
from bson import ObjectId

UTC_TZ = ZoneInfo("UTC")
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "your_jwt_secret_here")
# Need to use Savvify's client_id!
# I don't know it off-hand, so let's find the app document that has ID 6917346e5e57559f6ee43bfd
mongo_uri = os.environ.get("MONGO_URI", "your_mongo_uri_here")
client = MongoClient(mongo_uri)
db = client['bifrost_db']

app_doc = db.applications.find_one({"_id": ObjectId("6917346e5e57559f6ee43bfd")})
client_id = app_doc['client_id']
print(f"Testing for Client ID: {client_id}")

account_id = "6917670e409d7063eea82b88"
token_payload = {
    "sub": account_id,
    "iss": "bifrost",
    "aud": client_id,
    "iat": datetime.now(UTC_TZ),
    "exp": datetime.now(UTC_TZ).timestamp() + 3600 * 24 * 7
}
encoded_jwt = jwt.encode(token_payload, JWT_SECRET_KEY, algorithm="HS256")
print(f"Generated JWT: {encoded_jwt}")

# Now exactly run validate_token code


try:
    payload = jwt.decode(
        encoded_jwt,
        JWT_SECRET_KEY,
        algorithms=["HS256"],
        audience=client_id
    )

    acc_id = payload['sub']
    
    # db is already connected
    app_doc2 = db.applications.find_one({"client_id": client_id})
    user = db.accounts.find_one({"_id": ObjectId(acc_id)})
    
    # db.get_user_role_for_app
    link = db.app_links.find_one({"account_id": ObjectId(acc_id), "app_id": app_doc2['_id']})
    role = link.get('app_specific_role', 'user')
    expires_at = link.get('expires_at')
    if expires_at:
        now = datetime.now(UTC_TZ)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC_TZ)
        else:
            expires_at = expires_at.astimezone(UTC_TZ)
    
    final_role = role if role else "user"
    
    response = {
        "is_valid": True,
        "account_id": acc_id,
        "app_specific_role": final_role,
        "role": final_role,
        "email": user.get('email'),
        "username": user.get('username'),
        "display_name": user.get('display_name'),
        "telegram_id": user.get('telegram_id')
    }
    print(f"Validation successful: {response}")
except Exception as e:
    print("Exception occurred:")
    traceback.print_exc()

import os
from pymongo import MongoClient

def main():
    mongo_uri = "mongodb+srv://bifrostbyhelm_db_user:YG3fFeHVGMOCw1yX@bifrost.emyp9nm.mongodb.net/bifrost_db?retryWrites=true&w=majority"
    client = MongoClient(mongo_uri, tlsAllowInvalidCertificates=True)
    db = client['bifrost_db']

    print("Purging GCP/AI credentials from Bifrost Payment Bot...")
    res1 = db.applications.update_one(
        {"app_name": "Bifrost Payment Bot"},
        {"$unset": {"api_keys.GOOGLE_APPLICATION_CREDENTIALS_JSON": ""}}
    )
    print(f"Bifrost Payment Bot updated: Matched: {res1.matched_count}, Modified: {res1.modified_count}")

    print("Purging GCP/AI credentials from Random Project...")
    res2 = db.applications.update_one(
        {"app_name": "Random Project"},
        {"$unset": {
            "api_keys.GOOGLE_APPLICATION_CREDENTIALS_JSON": "",
            "api_keys.VERTEX_AI_PROJECT": "",
            "api_keys.VERTEX_AI_LOCATION": "",
            "api_keys.GEMINI_API_KEY": "",
            "api_keys.GOOGLE_APPLICATION_CREDENTIALS": ""
        }}
    )
    print(f"Random Project updated: Matched: {res2.matched_count}, Modified: {res2.modified_count}")

if __name__ == "__main__":
    main()

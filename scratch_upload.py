import sys
import os
from dotenv import dotenv_values
from run import app

def upload_keys():
    env_dict = dotenv_values('/Users/nicksng/code/finance-bot/.env')
    client_id = env_dict.get("BIFROST_CLIENT_ID")
    webhook_secret = env_dict.get("BIFROST_WEBHOOK_SECRET")

    upload_payload = {}
    for k, v in env_dict.items():
        if not k.startswith("BIFROST_") and v:
            upload_payload[k] = v

    print(f"Uploading {len(upload_payload)} keys...")

    client = app.test_client()
    response = client.post(
        '/api/v1/config/bulk-upload',
        headers={
            "X-Client-ID": client_id,
            "X-Webhook-Secret": webhook_secret,
            "Content-Type": "application/json"
        },
        json={"keys": upload_payload}
    )
    print("Status:", response.status_code)
    try:
        print("Data:", response.json)
    except:
        print("Data:", response.data)

if __name__ == '__main__':
    upload_keys()

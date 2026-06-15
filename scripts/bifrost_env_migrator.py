#!/usr/bin/env python3
"""
bifrost_env_migrator.py
=======================
A CLI tool to parse your local .env file and bulk upload all secrets to Bifrost.

Prerequisites:
  pip install requests python-dotenv
"""
import os
import sys
import requests
from dotenv import dotenv_values

def main():
    print("🚀 Bifrost Environment Migrator")
    print("===============================\n")
    
    env_path = input("Path to your .env file (default: .env): ").strip()
    if not env_path:
        env_path = ".env"
        
    if not os.path.exists(env_path):
        print(f"❌ Error: File '{env_path}' not found.")
        sys.exit(1)
        
    print(f"Reading {env_path}...")
    env_dict = dotenv_values(env_path)
    
    if not env_dict:
        print("❌ Error: No valid key-value pairs found in the file.")
        sys.exit(1)
        
    print(f"Found {len(env_dict)} keys.")
    
    # Extract Bifrost credentials if present, or ask for them
    client_id = env_dict.get("BIFROST_CLIENT_ID") or input("Enter BIFROST_CLIENT_ID: ").strip()
    webhook_secret = env_dict.get("BIFROST_WEBHOOK_SECRET") or input("Enter BIFROST_WEBHOOK_SECRET: ").strip()
    bifrost_url = env_dict.get("BIFROST_URL") or input("Enter BIFROST_URL (e.g. https://bifrost.example.com): ").strip()
    
    if not all([client_id, webhook_secret, bifrost_url]):
        print("❌ Error: Missing required Bifrost credentials.")
        sys.exit(1)
        
    bifrost_url = bifrost_url.rstrip('/')
    
    # Filter out Bifrost-specific keys so we don't upload them to the vault
    upload_payload = {}
    for k, v in env_dict.items():
        if not k.startswith("BIFROST_") and v:
            upload_payload[k] = v
            
    if not upload_payload:
        print("⚠️ No standard keys to upload (only Bifrost credentials found).")
        sys.exit(0)
        
    print(f"\nPreparing to securely upload {len(upload_payload)} keys to Bifrost...")
    
    confirm = input("Continue? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Aborted.")
        sys.exit(0)
        
    endpoint = f"{bifrost_url}/api/v1/config/bulk-upload"
    headers = {
        "X-Client-ID": client_id,
        "X-Webhook-Secret": webhook_secret,
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(
            endpoint,
            headers=headers,
            json={"keys": upload_payload},
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        print("\n✅ Success!")
        print(data.get("message", "Keys uploaded securely."))
        print("\n⚠️ IMPORTANT: You should now delete the uploaded keys from your local .env file.")
        print("Leave ONLY the BIFROST_CLIENT_ID, BIFROST_WEBHOOK_SECRET, and BIFROST_URL in your .env.")
        
    except requests.exceptions.RequestException as e:
        print(f"\n❌ Error uploading to Bifrost: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(e.response.text)
            
if __name__ == "__main__":
    main()

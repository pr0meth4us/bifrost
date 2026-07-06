import os
import requests
import json
import time
from dotenv import load_dotenv

# Load local .env first to get local fallbacks and BIFROST_ credentials
load_dotenv()

bifrost_url = os.getenv("BIFROST_URL")
client_id = os.getenv("BIFROST_CLIENT_ID")
webhook_secret = os.getenv("BIFROST_WEBHOOK_SECRET")

# Use a user-home relative cache path to allow sharing across script execution environments
cache_file = os.path.expanduser("~/.bifrost_cache.json")

def load_cached_keys():
    if os.path.exists(cache_file):
        try:
            mtime = os.path.getmtime(cache_file)
            # Cache is valid for 1 hour
            if time.time() - mtime < 3600:
                with open(cache_file, "r") as f:
                    return json.load(f)
        except Exception:
            pass
    return None

def save_cached_keys(data):
    try:
        with open(cache_file, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

# Bootstrapping credentials injection
if bifrost_url and client_id and webhook_secret:
    data = load_cached_keys()
    from_cache = True
    
    if data is None:
        endpoint = f"{bifrost_url.rstrip('/')}/api/v1/config"
        headers = {
            "X-Client-ID": client_id,
            "X-Webhook-Secret": webhook_secret
        }
        try:
            response = requests.get(endpoint, headers=headers, timeout=5)
            response.raise_for_status()
            data = response.json()
            save_cached_keys(data)
            from_cache = False
        except Exception as e:
            # Silence logging on headless environments if fallback exists
            pass
            
    if data:
        for key, value in data.get("data", {}).get("api_keys", {}).items():
            if value:
                os.environ[key] = str(value)

def get_config(key_name: str, default: str = "") -> str:
    """
    Fetches configuration value from environment (injected dynamically via Bifrost).
    """
    safe_key_name = key_name.strip()
    val = os.getenv(safe_key_name)
    if val is not None:
        return val
    return default

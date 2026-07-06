"""
Bifrost Client SDK
==================
A reusable, parameterized configuration and secret injection utility for Bifrost.
Bifrost is a unified Identity and Secrets Vault configuration manager.

Basic Usage:
------------
Simply import and fetch configured variables:

    from bifrost_client import get_config
    db_uri = get_config("MONGODB_URI")

Advanced Reusable / Parameterized Usage:
----------------------------------------
To configure custom credentials, custom cache parameters, or bypass environment injection:

    from bifrost_client import BifrostClient

    client = BifrostClient(
        client_id="my_app_client_id",
        webhook_secret="my_webhook_secret",
        bifrost_url="https://bifrost.example.com",
        cache_path="/tmp/my_app_secrets.cache",
        cache_ttl=1800,  # 30 minutes
        inject_env=True  # Automatically write keys to os.environ
    )

    # Fetch secrets directly from the client object
    gemini_key = client.get("GEMINI_API_KEY")
"""

import os
import sys
import time
import json
import requests
from typing import Optional, Dict, Any
from dotenv import load_dotenv

# Ensure local environment variables are parsed first
load_dotenv()

class BifrostClient:
    """
    Main client class for connecting to the Bifrost Configuration Service,
    fetching secrets, and managing local client-side caches.
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        webhook_secret: Optional[str] = None,
        bifrost_url: Optional[str] = None,
        cache_path: Optional[str] = None,
        cache_ttl: int = 3600,
        inject_env: bool = True,
        timeout: int = 10
    ):
        """
        Initialize the Bifrost Client.

        :param client_id: The Client ID registered in Bifrost. Defaults to BIFROST_CLIENT_ID env var.
        :param webhook_secret: The webhook secret configured for client validation. Defaults to BIFROST_WEBHOOK_SECRET env var.
        :param bifrost_url: The URL of the live Bifrost API server. Defaults to BIFROST_URL env var.
        :param cache_path: Absolute file path where config cache is stored. Defaults to user-home relative cache.
        :param cache_ttl: Lifetime of the local cache in seconds. Defaults to 1 hour (3600s).
        :param inject_env: If True, automatically populates loaded keys into os.environ.
        :param timeout: Connection timeout in seconds when querying Bifrost.
        """
        self.client_id = client_id or os.getenv("BIFROST_CLIENT_ID")
        self.webhook_secret = webhook_secret or os.getenv("BIFROST_WEBHOOK_SECRET")
        self.bifrost_url = bifrost_url or os.getenv("BIFROST_URL")
        
        # Determine fallback cache path: Home directory for local dev, fallback to /tmp or script dir
        if cache_path:
            self.cache_path = os.path.abspath(cache_path)
        else:
            home = os.path.expanduser("~")
            if os.access(home, os.W_OK):
                self.cache_path = os.path.join(home, ".bifrost_cache.json")
            else:
                self.cache_path = "/tmp/.bifrost_cache.json" if os.name != 'nt' else os.path.join(os.path.dirname(__file__), ".bifrost_cache.json")

        self.cache_ttl = cache_ttl
        self.inject_env = inject_env
        self.timeout = timeout
        
        self.api_keys: Dict[str, Any] = {}
        self.bootstrap()

    def _load_cache(self) -> Optional[Dict[str, Any]]:
        """Loads configuration from local file cache if it is fresh."""
        if os.path.exists(self.cache_path):
            try:
                mtime = os.path.getmtime(self.cache_path)
                # Verify if cache is within Time-To-Live limits
                if time.time() - mtime < self.cache_ttl:
                    with open(self.cache_path, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception:
                pass
        return None

    def _save_cache(self, data: Dict[str, Any]) -> None:
        """Stores the configuration data into local cache."""
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    def bootstrap(self) -> None:
        """
        Pulls secrets from the live Bifrost config endpoints, decodes,
        caches, and optionally injects them into process memory.
        """
        if not self.bifrost_url or not self.client_id or not self.webhook_secret:
            # Silence booting warning in headless containers, rely on local fallback env
            return

        data = self._load_cache()
        if data is None:
            endpoint = f"{self.bifrost_url.rstrip('/')}/api/v1/config"
            headers = {
                "X-Client-ID": self.client_id,
                "X-Webhook-Secret": self.webhook_secret
            }
            try:
                response = requests.get(endpoint, headers=headers, timeout=self.timeout)
                response.raise_for_status()
                data = response.json()
                self._save_cache(data)
            except Exception:
                # Silence exception so application boot fallback doesn't crash
                data = None

        if data:
            self.api_keys = data.get("data", {}).get("api_keys", {})
            if self.inject_env:
                for key, value in self.api_keys.items():
                    if value is not None:
                        os.environ[key] = str(value)

    def get(self, key_name: str, default: str = "") -> str:
        """
        Retrieves a configuration key. Prioritizes Bifrost-loaded keys,
        falling back to local environment variables, then the default.
        """
        safe_key = key_name.strip()
        
        # Check loaded Bifrost vault keys first
        if safe_key in self.api_keys and self.api_keys[safe_key] is not None:
            return str(self.api_keys[safe_key])
            
        # Fall back to standard environment variables
        val = os.getenv(safe_key)
        if val is not None:
            return val
            
        return default


# --- Singleton / Backwards-Compatible Bootstrapper ---

# Pre-load configuration on import using standard environment variables
default_client = BifrostClient(inject_env=True)

def get_config(key_name: str, default: str = "") -> str:
    """
    Retrieves a config value from the environment.
    Delegates to the default singleton Bifrost Client.
    """
    return default_client.get(key_name, default)

import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

def get_fernet(secret: str) -> Fernet:
    """Derives a Fernet instance using a specific string secret."""
    secret_bytes = secret.encode()
    salt = b"bifrost_api_keys_salt"  # Deterministic salt
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret_bytes))
    return Fernet(key)

def encrypt_value(value: str, secret: str) -> str:
    """Encrypts a string value using the provided secret."""
    if not value:
        return value
    f = get_fernet(secret)
    return f.encrypt(value.encode()).decode()

def decrypt_value(encrypted_value: str, secret: str) -> str:
    """Decrypts a string value using the provided secret."""
    if not encrypted_value:
        return encrypted_value
    try:
        f = get_fernet(secret)
        return f.decrypt(encrypted_value.encode()).decode()
    except Exception:
        # Fallback if decryption fails
        return encrypted_value


def app_secret(app_doc, key_name, default=None):
    """Reads one decrypted key out of a tenant's own vault.

    Every app document carries its own webhook_secret, so a key encrypted for one
    tenant is unreadable with another's. Callers that used to reach into
    current_app.config for a platform-wide credential go through here instead.
    """
    raw = ((app_doc or {}).get('api_keys') or {}).get(key_name)
    if not raw:
        return default
    return decrypt_value(raw, (app_doc or {}).get('webhook_secret', '')) or default

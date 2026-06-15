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

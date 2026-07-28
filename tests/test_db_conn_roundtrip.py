"""Blank connection-string field must mean "keep stored", never "wipe it".

The form stopped echoing the stored value (it was ciphertext). That only stays
safe if a blank submit leaves the existing connection alone — otherwise every
operator who edits the app name silently drops the tenant's database.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bifrost.utils.encryption import encrypt_value, decrypt_value

SECRET = "a" * 48
URL = "postgresql://user:pw@db.example.com:5432/app"


def build(form_value, stored):
    """Mirrors the db_connection branch of update_app_settings()."""
    raw = (form_value or "").strip()
    data = {"app_name": "X"}
    if raw:
        data["db_connection"] = encrypt_value(raw, SECRET)
    # update_app_details() only $sets keys present in data
    return {**stored, **data}


def test_roundtrip():
    stored = {"db_connection": encrypt_value(URL, SECRET)}

    # Blank submit: the stored ciphertext survives untouched.
    assert build("", stored)["db_connection"] == stored["db_connection"]
    assert build(None, stored)["db_connection"] == stored["db_connection"]
    assert build("   ", stored)["db_connection"] == stored["db_connection"]

    # A typed URL replaces it, encrypted exactly once — decrypting must yield
    # the plaintext, not another layer of ciphertext.
    new = "postgresql://u2:pw2@other.example.com:5432/db2"
    assert decrypt_value(build(new, stored)["db_connection"], SECRET) == new

    # First-time set on an app with nothing stored.
    assert decrypt_value(build(URL, {})["db_connection"], SECRET) == URL


if __name__ == "__main__":
    test_roundtrip()
    print("ok: blank keeps stored connection, typed value encrypts once")

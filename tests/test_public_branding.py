"""Public branding endpoint: serves the QR, leaks nothing else.

Run: .venv/bin/python tests/test_public_branding.py
"""
import sys, types

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])
from bifrost import config_api

APP = {
    "app_name": "Ministry Exam Prep",
    "client_id": "prolong",
    "app_logo_url": "https://cdn.example.com/logo.png",
    "app_qr_url": "https://cdn.example.com/aba-khqr.png",
    "client_secret_hash": "pbkdf2:sha256:...",
    "webhook_secret": "whsec_example",
    "telegram_bot_token": "123:ABC",
    "api_keys": {"GEMINI_API_KEY": "gAAAAA..."},
    "db_connection": "postgresql://u:p@h:5432/db",
}

SECRETS = ("whsec_example", "123:ABC", "gAAAAA", "postgresql://", "pbkdf2")


def test():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(config_api.config_api_bp)
    config_api.get_db = lambda: types.SimpleNamespace(
        get_app_by_client_id=lambda cid: APP if cid == "prolong" else None
    )
    c = app.test_client()

    r = c.get("/api/v1/public/branding/prolong")
    assert r.status_code == 200
    assert r.get_json()["data"]["payment_qr_url"] == APP["app_qr_url"]
    body = r.get_data(as_text=True)
    for s in SECRETS:
        assert s not in body, f"public endpoint leaked {s!r}"

    assert c.get("/api/v1/public/branding/nope").status_code == 404
    print("ok")


if __name__ == "__main__":
    test()

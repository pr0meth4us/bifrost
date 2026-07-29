"""Onboarding's payment step writes the right things, and only for the right app.

The credential path is the one that matters: PayWay keys must land encrypted in
the new app's own vault, readable back only with that app's webhook_secret.

Run: .venv/bin/python tests/test_onboarding_payments.py
"""
import sys, types

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])

from bson import ObjectId
from werkzeug.datastructures import MultiDict

from bifrost.backoffice import app_routes
from bifrost.utils.encryption import encrypt_value, app_secret

APP_ID = ObjectId()
APP = {"_id": APP_ID, "client_id": "newco", "webhook_secret": "whsec_new"}


class FakeDB:
    """Just enough of BifrostDB: records updates, encrypts keys like the real one."""

    def __init__(self):
        self.updates = {}
        self.keys = {}

    def update_app_details(self, app_id, data):
        assert app_id == APP_ID
        self.updates.update(data)
        return True

    def add_app_api_key(self, app_id, name, value):
        assert app_id == APP_ID
        self.keys[name] = encrypt_value(value, APP["webhook_secret"])
        return True


def run(form):
    db = FakeDB()
    methods = app_routes._save_payment_setup(db, APP, MultiDict(form))
    return db, methods


def test_no_payments():
    db, methods = run({"app_name": "x"})
    assert methods == []
    assert db.updates == {"payment_methods": []}
    assert db.keys == {}, "an app that takes no payments must store no merchant keys"


def test_manual_only():
    db, methods = run({"payments_enabled": "on", "pay_manual": "on",
                       "qr_url": " https://cdn.example.com/khqr.png "})
    assert methods == ["manual"]
    assert db.updates["app_qr_url"] == "https://cdn.example.com/khqr.png"
    assert db.keys == {}, "manual queue needs no bank credentials"


def test_payway_only():
    db, methods = run({"payments_enabled": "on", "pay_payway": "on",
                       "payway_merchant_id": "merchant_new", "payway_api_key": "key_new"})
    assert methods == ["payway"]
    vault = {**APP, "api_keys": db.keys}
    assert app_secret(vault, "PAYWAY_MERCHANT_ID") == "merchant_new"
    assert app_secret(vault, "PAYWAY_API_KEY") == "key_new"
    assert "key_new" not in str(db.keys), "credentials must be stored encrypted"
    # Another tenant's secret must not open this vault.
    assert app_secret({**vault, "webhook_secret": "whsec_other"}, "PAYWAY_API_KEY") != "key_new"


def test_both_methods():
    db, methods = run({"payments_enabled": "on", "pay_payway": "on", "pay_manual": "on",
                       "payway_merchant_id": "m", "payway_api_key": "k",
                       "qr_url": "https://cdn.example.com/khqr.png"})
    assert methods == ["payway", "manual"], "both must be able to run side by side"
    assert db.updates["app_qr_url"] and len(db.keys) == 2


def test_blank_credential_on_edit_means_unchanged():
    # Settings tab never renders a stored key back, so a blank input must keep
    # the existing one rather than wiping it.
    db = FakeDB()
    existing = {**APP, "api_keys": {"PAYWAY_MERCHANT_ID": "cipher", "PAYWAY_API_KEY": "cipher"}}
    app_routes._save_payment_setup(db, existing, MultiDict(
        {"payments_enabled": "on", "pay_payway": "on"}))
    assert db.keys == {}, "blank inputs must not overwrite a stored credential"


def test_methods_ignored_when_payments_off():
    # Checkboxes left set while the master toggle is off must not enable anything.
    db, methods = run({"pay_payway": "on", "payway_api_key": "k"})
    assert methods == [] and db.keys == {}


if __name__ == "__main__":
    app_routes.flash = lambda *a, **k: None  # no request context in a unit test
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn()
            print(f"  {name} ok")
    print("ok")

"""Trusted-device cookie: right account only, real signature only, expires.

Run: .venv/bin/python tests/test_trusted_device.py
"""
import sys, time, types
from itsdangerous import URLSafeTimedSerializer

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])
from bifrost.backoffice import auth_routes as ar

SALT = "backoffice-trusted-device"


def check(cookie, user_id, secret="s3cret", days=30):
    ar._device_serializer = lambda: URLSafeTimedSerializer(secret, salt=SALT)
    ar.request = types.SimpleNamespace(cookies={ar.TRUSTED_DEVICE_COOKIE: cookie} if cookie else {})
    ar.TRUSTED_DEVICE_DAYS = days
    return ar._device_trusted_for(user_id)


def test():
    good = URLSafeTimedSerializer("s3cret", salt=SALT).dumps("user-1")

    assert check(good, "user-1")
    assert not check(good, "user-2"), "cookie must not authorize another account"
    assert not check(good, "user-1", secret="other"), "forged/rotated key must fail"
    assert not check(good[:-3] + "xxx", "user-1"), "tampered token must fail"
    assert not check(None, "user-1")

    time.sleep(1.1)
    assert not check(good, "user-1", days=0), "max_age must actually be applied"
    print("ok")


if __name__ == "__main__":
    test()

"""Nothing that routes money or data is shared between tenants.

Three boundaries, each of which used to be one global value:
  - PayWay merchant credentials  (revenue misrouted to another tenant's ABA account)
  - Gumroad product permalink    (same, for the international flow)
  - managed Postgres schema      (tenant A reading tenant B's rows)

Run: .venv/bin/python tests/test_tenant_isolation.py
"""
import sys, types
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])

from flask import Flask

from bifrost.utils.encryption import encrypt_value, app_secret
from bifrost.services.payway import PayWayService, PayWayNotConfigured
from bifrost.services.gumroad import GumroadService
from bifrost.backoffice.tenant_routes import managed_schema_for, _with_schema


def make_app_doc(client_id, secret, keys):
    return {
        "client_id": client_id,
        "webhook_secret": secret,
        "api_keys": {k: encrypt_value(v, secret) for k, v in keys.items()},
    }


A = make_app_doc("alpha", "whsec_a", {
    "PAYWAY_MERCHANT_ID": "merchant_A", "PAYWAY_API_KEY": "key_A",
    "GUMROAD_PRODUCT_PERMALINK": "alpha-premium",
})
B = make_app_doc("beta_co", "whsec_b", {
    "PAYWAY_MERCHANT_ID": "merchant_B", "PAYWAY_API_KEY": "key_B",
    "GUMROAD_PRODUCT_PERMALINK": "beta-premium",
})
UNCONFIGURED = make_app_doc("gamma", "whsec_g", {})


def flask_ctx():
    app = Flask(__name__)
    app.config.update(
        PAYWAY_API_URL="https://sandbox.example/purchase",
        BIFROST_PUBLIC_URL="https://bifrost.example",
        GUMROAD_BASE_URL="https://gumroad.com/l",
    )
    return app.app_context()


def test_vault_is_per_tenant():
    assert app_secret(A, "PAYWAY_API_KEY") == "key_A"
    # B's secret cannot open A's vault: decrypt_value falls back to the ciphertext.
    stolen = dict(A, webhook_secret=B["webhook_secret"])
    assert app_secret(stolen, "PAYWAY_API_KEY") != "key_A"
    assert app_secret(A, "NOT_SET", "fallback") == "fallback"


def test_payway_binds_to_its_own_merchant():
    with flask_ctx():
        assert PayWayService(A).merchant_id == "merchant_A"
        assert PayWayService(B).merchant_id == "merchant_B"

        # Same payload signed by two tenants must not produce the same HMAC.
        assert PayWayService(A)._generate_hash("x") != PayWayService(B)._generate_hash("x")

        # No platform fallback: an unconfigured tenant must fail, not inherit.
        try:
            PayWayService(UNCONFIGURED)
        except PayWayNotConfigured:
            pass
        else:
            raise AssertionError("unconfigured tenant silently got a merchant account")


def test_gumroad_product_is_per_tenant():
    with flask_ctx():
        assert "alpha-premium" in GumroadService(A).generate_checkout_url("tx1", "u@e.com")
        assert "beta-premium" in GumroadService(B).generate_checkout_url("tx1", "u@e.com")
        assert GumroadService(UNCONFIGURED).generate_checkout_url("tx1", "u@e.com") is None


def test_managed_schema_is_per_tenant():
    assert managed_schema_for(A) != managed_schema_for(B)
    assert managed_schema_for({"client_id": "a-b.c"}) == "tenant_a_b_c", "must be a safe identifier"
    assert managed_schema_for({"client_id": "x", "db_schema": "chosen"}) == "chosen"

    base = "postgresql://u:p@h:5432/db"
    dsn_a, dsn_b = _with_schema(base, managed_schema_for(A)), _with_schema(base, managed_schema_for(B))
    assert dsn_a != dsn_b, "pool is keyed on the DSN, so these must differ"
    opts = parse_qs(urlsplit(dsn_a).query)["options"][0]
    assert opts == "-c search_path=tenant_alpha"
    assert "public" not in opts, "public must not stay on the search path"

    # An existing options= value must be replaced, never duplicated.
    once = _with_schema(dsn_a, "tenant_beta_co")
    assert parse_qs(urlsplit(once).query)["options"] == ["-c search_path=tenant_beta_co"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn()
            print(f"  {name} ok")
    print("ok")

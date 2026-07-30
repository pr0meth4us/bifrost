import datetime
from zoneinfo import ZoneInfo
import jwt
from flask import current_app

UTC_TZ = ZoneInfo("UTC")

# Explicit Client App Role-Permissions Map
CLIENT_ROLE_PERMISSIONS = {
    "owner": [
        "read:profile", "write:profile",
        "read:app", "write:app",
        "manage:users", "billing:manage",
        "premium:access"
    ],
    "super_admin": [
        "read:profile", "write:profile",
        "read:app", "write:app",
        "manage:users", "premium:access"
    ],
    "admin": [
        "read:profile", "write:profile",
        "read:app", "manage:users",
        "premium:access"
    ],
    "premium_user": [
        "read:profile", "write:profile",
        "read:app", "premium:access"
    ],
    "user": [
        "read:profile", "write:profile",
        "read:app"
    ],
    "guest": [
        "read:app"
    ]
}

DEFAULT_TTL_SECONDS = 7 * 86400


def create_client_jwt(user, client_id, db, app_config, ttl_seconds=DEFAULT_TTL_SECONDS,
                      scopes=None):
    """
    Generates a cryptographically signed JWT token for downstream client applications.
    Embeds the resolved tenant role and associated list of granular permissions.

    OIDC passes a short `ttl_seconds` and the granted `scopes`; the scope claim is
    what /oidc/userinfo filters on, so a token issued without `openid` cannot be
    used to read profile data.
    """
    user_id = user.get('_id') or user.get('id')
    role = db.get_user_role_for_app(user_id, app_config['_id']) or "user"
    permissions = CLIENT_ROLE_PERMISSIONS.get(role, ["read:app"])

    now = datetime.datetime.now(UTC_TZ)
    token_payload = {
        "sub": str(user_id),
        "iss": "bifrost",
        "aud": client_id,
        "iat": now,
        "exp": now + datetime.timedelta(seconds=ttl_seconds),
        "email": user.get('email', ''),
        "name": user.get('display_name', ''),
        "role": role,
        "permissions": permissions
    }
    if scopes:
        token_payload["scope"] = " ".join(scopes)

    return jwt.encode(
        token_payload,
        current_app.config['JWT_SECRET_KEY'],
        algorithm="HS256"
    )

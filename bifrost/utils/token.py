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

def create_client_jwt(user, client_id, db, app_config):
    """
    Generates a cryptographically signed JWT token for downstream client applications.
    Embeds the resolved tenant role and associated list of granular permissions.
    """
    user_id = user.get('_id') or user.get('id')
    role = db.get_user_role_for_app(user_id, app_config['_id']) or "user"
    permissions = CLIENT_ROLE_PERMISSIONS.get(role, ["read:app"])

    token_payload = {
        "sub": str(user_id),
        "iss": "bifrost",
        "aud": client_id,
        "iat": datetime.datetime.now(UTC_TZ),
        "exp": datetime.datetime.now(UTC_TZ) + datetime.timedelta(days=7),
        "email": user.get('email', ''),
        "name": user.get('display_name', ''),
        "role": role,
        "permissions": permissions
    }

    return jwt.encode(
        token_payload,
        current_app.config['JWT_SECRET_KEY'],
        algorithm="HS256"
    )

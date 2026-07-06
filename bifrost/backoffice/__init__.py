# bifrost/backoffice/__init__.py
from flask import Blueprint, session, redirect, url_for, flash, current_app
from functools import wraps

backoffice_bp = Blueprint('backoffice', __name__, url_prefix='/backoffice')

def get_db():
    from ..models import BifrostDB
    from .. import mongo
    return BifrostDB(mongo.cx, current_app.config['DB_NAME'])


# --- PERMISSION HELPERS ---

def get_current_role_in_app(app_id):
    """Returns: owner, super_admin, admin, or None/heimdall/pr0meth4us"""
    if session.get('is_heimdall'):
        return 'heimdall'
    if session.get('is_pr0meth4us'):
        return 'pr0meth4us'

    db = get_db()
    user_id = session.get('backoffice_user')
    if not user_id: return None

    return db.get_user_role_for_app(user_id, app_id)


ROLE_PERMISSIONS = {
    "owner": {
        "read:config", "write:config", "manage:users", "view:secrets", "manage:secrets", "transfer:ownership", "view:metrics"
    },
    "super_admin": {
        "read:config", "write:config", "manage:users", "view:secrets", "manage:secrets", "view:metrics"
    },
    "admin": {
        "read:config", "manage:users", "view:metrics"
    },
    "member": {
        "read:config", "view:metrics"
    },
    "user": {
        "read:config", "view:metrics"
    },
    "viewer": {
        "read:config"
    }
}

def check_permission(app_id, permission_or_level):
    """
    Role-Based Access Control (RBAC) Checker.
    Supports both explicit permission strings (professional) and legacy levels (fallback).
    """
    role = get_current_role_in_app(app_id)
    if role in ('heimdall', 'pr0meth4us'):
        return True

    if not role:
        return False

    # Legacy numeric fallback compatibility
    if isinstance(permission_or_level, int):
        level = permission_or_level
        if role == 'owner': return True
        if level <= 2 and role == 'super_admin': return True
        if level <= 1 and role == 'admin': return True
        return False

    # Explicit string permission check
    allowed_permissions = ROLE_PERMISSIONS.get(role, set())
    return permission_or_level in allowed_permissions


# --- AUTH DECORATORS ---

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('backoffice_user'):
            return redirect(url_for('backoffice.login'))
        return f(*args, **kwargs)
    return decorated_function


def heimdall_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_heimdall'):
            flash("Heimdall Access Required.", "danger")
            return redirect(url_for('backoffice.dashboard'))
        return f(*args, **kwargs)
    return decorated_function
from . import auth_routes, app_routes, heimdall_routes, user_routes

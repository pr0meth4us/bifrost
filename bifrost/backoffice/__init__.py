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


def check_permission(app_id, min_level):
    """
    Levels:
    3 = Owner/Heimdall (Secrets, Transfer Ownership)
    2 = Super Admin (Config, Manage Admins)
    1 = Admin (Manage Users only)
    """
    role = get_current_role_in_app(app_id)
    if role == 'heimdall': return True
    if role == 'pr0meth4us': return True
    if role == 'owner': return True  # Level 3

    if min_level <= 2 and role == 'super_admin': return True
    if min_level <= 1 and role == 'admin': return True

    return False


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

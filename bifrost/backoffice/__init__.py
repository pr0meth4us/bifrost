# bifrost/backoffice/__init__.py
from flask import Blueprint, session, redirect, url_for, flash, current_app, request, abort
from functools import wraps
from datetime import datetime, timedelta, timezone

backoffice_bp = Blueprint('backoffice', __name__, url_prefix='/backoffice')

def get_db():
    from ..models import BifrostDB
    from .. import mongo
    return BifrostDB(mongo.cx, current_app.config['DB_NAME'])


# --- PERMISSION HELPERS ---

def resolve_app_doc(db, app_id_or_slug=None):
    """Resolves application document by ObjectId, client_id slug, or active session context."""
    from bson import ObjectId
    if app_id_or_slug:
        if isinstance(app_id_or_slug, ObjectId):
            app = db.db.applications.find_one({"_id": app_id_or_slug})
            if app:
                session['active_app_id'] = str(app['_id'])
                return app
        if isinstance(app_id_or_slug, str) and ObjectId.is_valid(app_id_or_slug):
            app = db.db.applications.find_one({"_id": ObjectId(app_id_or_slug)})
            if app:
                session['active_app_id'] = str(app['_id'])
                return app
        # Try slug lookup (e.g. ministry_exam_prep)
        app = db.db.applications.find_one({"client_id": str(app_id_or_slug).strip()})
        if app:
            session['active_app_id'] = str(app['_id'])
            return app

    # Fallback to active app in session
    active_id = session.get('active_app_id')
    if active_id and ObjectId.is_valid(active_id):
        app = db.db.applications.find_one({"_id": ObjectId(active_id)})
        if app:
            return app

    # Fallback to user's first managed app
    user_id = session.get('backoffice_user')
    if user_id:
        apps = db.get_managed_apps(user_id)
        if apps:
            app = apps[0]
            session['active_app_id'] = str(app['_id'])
            return app
    return None

def get_current_role_in_app(app_id_or_slug):
    """Returns: owner, super_admin, admin, developer, or None/heimdall/pr0meth4us

    Memoised per request. check_permission() calls this on every single check and
    the sidebar now asks about half a dozen permissions per render, so without the
    cache one page load would cost a Mongo round-trip per menu item.
    """
    if session.get('is_heimdall'):
        return 'heimdall'
    if session.get('is_pr0meth4us'):
        return 'pr0meth4us'

    user_id = session.get('backoffice_user')
    if not user_id: return None

    from flask import g
    cache = g.setdefault('_role_cache', {})
    key = str(app_id_or_slug)
    if key in cache:
        return cache[key]

    db = get_db()
    app = resolve_app_doc(db, app_id_or_slug)
    role = db.get_user_role_for_app(user_id, str(app['_id'])) if app else None
    cache[key] = role
    return role



# Console roles (SOW 3.8). Three that matter:
#   admin/owner       — everything
#   content_manager   — content + glossary + read-only analytics; NO payments, NO publish
#   operations        — payment queue and support actions; NO content, NO config
# Enforcement is server-side via check_permission/requires. Hiding a button is not
# access control, so every route gates on a permission string, not on a role name.
_CONTENT = {"content:read", "content:write"}
_PAYMENTS = {"payments:view", "payments:approve"}
_SUPPORT = {"users:view", "users:suspend", "entitlements:override"}

ROLE_PERMISSIONS = {
    "owner": {
        "read:config", "write:config", "manage:users", "view:secrets", "manage:secrets",
        "transfer:ownership", "view:metrics", "audit:view", "content:publish",
    } | _CONTENT | _PAYMENTS | _SUPPORT,
    "super_admin": {
        "read:config", "write:config", "manage:users", "view:secrets", "manage:secrets",
        "view:metrics", "audit:view", "content:publish",
    } | _CONTENT | _PAYMENTS | _SUPPORT,
    "admin": {
        "read:config", "write:config", "manage:users", "view:metrics", "audit:view",
        "content:publish",
    } | _CONTENT | _PAYMENTS | _SUPPORT,
    # Content Manager: may move draft -> review, may NOT publish, may NOT see money.
    "content_manager": {
        "read:config", "view:metrics", "audit:view",
    } | _CONTENT,
    # Operations / Billing: the money path and support actions only.
    "operations": {
        "read:config", "audit:view",
    } | _PAYMENTS | _SUPPORT,
    "billing_agent": {
        "read:config", "audit:view",
    } | _PAYMENTS | _SUPPORT,
    # Developer: raw SQL against the tenant database, and nothing else. Deliberately
    # NOT a superset of admin — a developer running migrations has no business
    # approving payments or reading secrets, and an owner should be able to hand
    # this out to a contractor without also handing over the money path.
    # db:execute is the most dangerous permission in the system (DROP TABLE is one
    # keystroke), so it is never bundled into another role — it must be assigned.
    "developer": {
        "read:config", "audit:view", "db:execute",
    } | _CONTENT,
    "member": {"read:config"},
    "user": {"read:config"},
    "viewer": {"read:config"},
}

# Roles that may sign in to the console at all.
CONSOLE_ROLES = ("owner", "super_admin", "admin", "content_manager", "operations",
                 "billing_agent", "developer")

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


def requires(permission):
    """Server-side permission gate. One mechanism, used by every route.

    Mutations and API calls get a hard 403 — a Content Manager's session hitting a
    payments endpoint with curl must be rejected by the server, not redirected to a
    page that happens to hide the button.
    """
    def decorator(f):
        @wraps(f)
        @login_required
        def wrapper(*args, **kwargs):
            app_id = kwargs.get('app_id')
            if not check_permission(app_id, permission):
                if request.method != 'GET' or request.path.startswith('/backoffice/api/'):
                    abort(403, description=f"Missing permission: {permission}")
                flash("You do not have access to that.", "danger")
                return redirect(url_for('backoffice.view_app', app_id=app_id) if app_id
                                else url_for('backoffice.dashboard'))
            return f(*args, **kwargs)
        return wrapper
    return decorator


# --- AUTH DECORATORS ---

# Admin sessions expire faster than end-user sessions (SOW 4.6).
ADMIN_IDLE_TIMEOUT_MINUTES = 30
ADMIN_MAX_SESSION_HOURS = 8


def _session_expired():
    now = datetime.now(timezone.utc)
    started = session.get('session_started_at')
    seen = session.get('last_seen_at')
    try:
        if started and now - datetime.fromisoformat(started) > timedelta(hours=ADMIN_MAX_SESSION_HOURS):
            return True
        if seen and now - datetime.fromisoformat(seen) > timedelta(minutes=ADMIN_IDLE_TIMEOUT_MINUTES):
            return True
    except ValueError:
        return True
    session['last_seen_at'] = now.isoformat()
    return False


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('backoffice_user'):
            return redirect(url_for('backoffice.login'))
        if _session_expired():
            session.clear()
            flash("Session expired. Please sign in again.", "warning")
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
@backoffice_bp.app_context_processor
def _inject_rbac():
    """Gives the shared sidebar the real permission matrix instead of a duplicate.

    `can()` is check_permission() — so a menu that drifts from what the routes
    actually allow is impossible. Still cosmetic: the routes do the enforcing.
    """
    def can(app_id, permission):
        try:
            return check_permission(app_id, permission)
        except Exception:
            return False

    return {"can": can}


from . import (auth_routes, app_routes, heimdall_routes, user_routes, tenant_routes,
               devtools_routes)

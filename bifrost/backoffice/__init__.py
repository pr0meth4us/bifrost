# bifrost/backoffice/__init__.py
from flask import (Blueprint, session, redirect, url_for, flash, current_app, request,
                   abort, jsonify)
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

# Platform-staff roles. They run Bifrost; they are not automatically staff of
# every tenant on it.
PLATFORM_ROLES = ('heimdall', 'pr0meth4us')

# What a platform admin may do inside an EXTERNAL tenant — someone else's
# customers, someone else's data. Enough to keep the platform running and answer
# "is their integration healthy", and nothing that reads or changes the tenant's
# business: no secrets, no content, no end-user records, no payment approvals, no
# SQL. Internal tenants are unrestricted.
#
# The escape hatch is deliberate and consented: a tenant owner can grant a
# platform engineer a normal role in their app through user management, which is
# visible to them and revocable by them. That is a better break-glass than an
# implicit superuser nobody can see.
PLATFORM_EXTERNAL_PERMISSIONS = {
    "read:config",   # see how the app is wired, to support the integration
    "view:metrics",  # aggregate health and usage
    "audit:view",    # who did what, since the platform is accountable for it
}


# Permissions a tenant owner may not hand out by editing their own role table.
# Raw SQL is the only one, and only when the database is the platform's rather
# than the tenant's — see AppMixin.owns_its_database. Everything else in the
# matrix concerns the tenant's own content, users, payments and secrets, which
# are theirs to delegate.
PLATFORM_GRANTED_ONLY = {"db:execute"}


def effective_role_permissions(app, role):
    """The permission set for `role` in `app`.

    `applications.role_permissions` overrides the platform defaults per role, so
    a tenant that wants its content_manager to publish is a console edit rather
    than a release. A role absent from the override falls back to the default,
    which keeps the table meaningful when only one role has been customised.
    """
    overrides = (app or {}).get('role_permissions') or {}
    if role not in overrides:
        return ROLE_PERMISSIONS.get(role, set())

    from ..models.apps import AppMixin

    granted = set(overrides[role] or ())
    if not AppMixin.owns_its_database(app):
        granted -= PLATFORM_GRANTED_ONLY
    return granted


def platform_admin_may(app_id, permission):
    """Whether a platform admin may exercise `permission` inside this tenant."""
    if not app_id:
        # Platform-level page with no tenant in scope (dashboard, intake queue).
        return True
    db = get_db()
    app = resolve_app_doc(db, app_id)
    if db.is_internal_tenant(app):
        return True
    return permission in PLATFORM_EXTERNAL_PERMISSIONS


def check_permission(app_id, permission_or_level):
    """
    Role-Based Access Control (RBAC) Checker.
    Supports both explicit permission strings (professional) and legacy levels (fallback).
    """
    role = get_current_role_in_app(app_id)
    if role in PLATFORM_ROLES:
        # Numeric legacy levels are only ever used for tenant-config screens; treat
        # them as the write-config permission rather than a blanket yes.
        permission = ("write:config" if isinstance(permission_or_level, int)
                      else permission_or_level)
        return platform_admin_may(app_id, permission)

    if not role:
        return False

    # Legacy numeric fallback compatibility
    if isinstance(permission_or_level, int):
        level = permission_or_level
        if role == 'owner': return True
        if level <= 2 and role == 'super_admin': return True
        if level <= 1 and role == 'admin': return True
        return False

    # Explicit string permission check, against this app's effective matrix —
    # the platform defaults unless the tenant has overridden that role.
    app = resolve_app_doc(get_db(), app_id) if app_id else None
    return permission_or_level in effective_role_permissions(app, role)


def cms_full_access(app_id, roles=("owner", "super_admin")):
    """Unrestricted read/write over the tenant's CMS tables.

    Replaces five hand-copied role tuples that each listed heimdall alongside the
    tenant's own owners. A platform admin keeps that blanket access on internal
    tenants only — on an external one, the tenant's CMS is the tenant's data and
    the platform sees it through the same per-role config as everyone else.
    """
    role = get_current_role_in_app(app_id)
    if role in PLATFORM_ROLES:
        return platform_admin_may(app_id, "content:write")
    return role in roles


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

# Session length is a poor proxy for risk. A 30-minute idle timeout protected
# the console against a laptop abandoned for 31 minutes and not one abandoned for
# 20, while interrupting everyone who reads a log or waits for a deploy — and it
# left DROP TABLE available for the whole eight hours on the strength of one
# morning sign-in.
#
# So: a working day's session, and a re-authentication window in front of the
# actions that actually deserve one. This is GitHub's sudo mode, and the trade is
# deliberate — ordinary work stops being interrupted, and the destructive surface
# gets more protection than it had, not less.
ADMIN_IDLE_TIMEOUT_MINUTES = 8 * 60
ADMIN_MAX_SESSION_HOURS = 8

# How long a re-authentication counts for. Short enough that a walked-away
# session cannot be used to drain the vault, long enough to run a migration.
SUDO_WINDOW_MINUTES = 30

# Actions worth re-authenticating for: raw SQL, credentials, and moving money or
# ownership. Everything else — reviewing content, reading the queue — is not.
SUDO_PERMISSIONS = frozenset({'db:execute', 'view:secrets', 'manage:secrets',
                              'transfer:ownership'})


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


def has_sudo():
    """True when the operator re-authenticated recently enough."""
    stamped = session.get('sudo_at')
    if not stamped:
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(stamped)
    except ValueError:
        return False
    return age <= timedelta(minutes=SUDO_WINDOW_MINUTES)


def grant_sudo():
    session['sudo_at'] = datetime.now(timezone.utc).isoformat()


def requires_sudo(f):
    """Re-authenticate before a destructive action, GitHub-style.

    Applied to the GET that opens a dangerous screen AND to the POST that acts,
    because a gate on the page alone is decoration — the POST is the action.
    A POST arriving without sudo loses its form data on the way to the prompt;
    that is the right trade for a handful of rare, destructive operations.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if has_sudo():
            return f(*args, **kwargs)
        target = request.full_path if request.method == 'GET' else (request.referrer or '')
        confirm = url_for('backoffice.confirm_access', next=target)
        # A fetch() cannot follow a redirect to a login form usefully — it would
        # render the HTML into the results pane. Say so in the caller's language.
        if request.path.startswith('/backoffice/api/') or request.is_json:
            return jsonify(error="Confirm your identity to run this.",
                           confirm_url=confirm), 403
        return redirect(confirm)
    return decorated


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

    # Pending-intake count for the sidebar badge. Platform admins only — nobody
    # else has the menu item, so nobody else pays for the query.
    pending_requests = 0
    if session.get('is_heimdall'):
        try:
            pending_requests = get_db().db.tenant_requests.count_documents({"status": "pending"})
        except Exception:
            pending_requests = 0

    return {"can": can, "pending_requests": pending_requests}


def acting_identity():
    """Who to record as having performed an action, for humans reading it later.

    The email, because attestation columns and audit rows are read from the
    tenant's side, where a Bifrost ObjectId resolves to nothing. Falls back to
    the id for sessions issued before the email was carried, and only then to
    'unknown' — an audit row with no actor is worse than an opaque one.
    """
    return (session.get('backoffice_email')
            or (str(session['backoffice_user']) if session.get('backoffice_user') else None)
            or 'unknown')


from . import (auth_routes, app_routes, heimdall_routes, user_routes, tenant_routes,
               devtools_routes, review_routes)

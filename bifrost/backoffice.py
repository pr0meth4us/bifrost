# bifrost/backoffice.py
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from bson import ObjectId
from . import mongo
from .models import BifrostDB
from .services.email_service import send_invite_email, send_reset_email

backoffice_bp = Blueprint('backoffice', __name__, url_prefix='/backoffice')


def get_db():
    return BifrostDB(mongo.cx, current_app.config['DB_NAME'])


# --- PERMISSION HELPERS ---

def get_current_role_in_app(app_id):
    """Returns: owner, super_admin, admin, or None/heimdall"""
    if session.get('is_heimdall'):
        return 'heimdall'

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
    if role == 'owner': return True  # Level 3

    if min_level <= 2 and role == 'super_admin': return True
    if min_level <= 1 and role == 'admin': return True

    return False


# --- AUTH DECORATORS ---

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('backoffice_user'):
            return redirect(url_for('backoffice.login'))
        return f(*args, **kwargs)

    return decorated_function


def heimdall_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_heimdall'):
            flash("Heimdall Access Required.", "danger")
            return redirect(url_for('backoffice.dashboard'))
        return f(*args, **kwargs)

    return decorated_function


# --- ROUTES ---

@backoffice_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        identifier = request.form.get('email').strip()
        password = request.form.get('password')
        db = get_db()

        # 1. Heimdall Check
        admin_doc = db.db.admins.find_one({"email": identifier.lower()})
        if admin_doc and check_password_hash(admin_doc['password_hash'], password):
            if admin_doc.get('role') == 'heimdall':
                session['backoffice_user'] = str(admin_doc['_id'])
                session['is_heimdall'] = True
                session['role'] = 'Heimdall'
                return redirect(url_for('backoffice.dashboard'))
            else:
                flash("Role deprecated. Update to 'heimdall'.", "warning")

        # 2. App Tenant Check
        user = db.find_account_by_email(identifier)
        if not user: user = db.find_account_by_username(identifier)

        if user and user.get('password_hash') and check_password_hash(user['password_hash'], password):
            managed_apps = db.get_managed_apps(user['_id'])
            if managed_apps:
                session['backoffice_user'] = str(user['_id'])
                session['is_heimdall'] = False
                session['role'] = 'Tenant'  # General label
                return redirect(url_for('backoffice.dashboard'))
            else:
                flash("Access Denied: You do not manage any apps.", "danger")
        else:
            flash("Invalid credentials.", "danger")

    return render_template('backoffice/login.html')


@backoffice_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('backoffice.login'))


@backoffice_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email').strip().lower()
        db = get_db()
        is_heimdall = False
        user = db.db.admins.find_one({"email": email})
        if user:
            is_heimdall = True
        else:
            user = db.find_account_by_email(email)

        if user:
            otp, vid = db.create_otp(email, channel="email")
            if send_reset_email(email, otp):
                session['reset_email'] = email
                session['reset_is_heimdall'] = is_heimdall
                flash(f"Reset code sent to {email}", "success")
                return redirect(url_for('backoffice.reset_password'))
            else:
                flash("Error sending email.", "danger")
        else:
            flash("Email not found.", "danger")
    return render_template('backoffice/forgot_password.html')


@backoffice_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    email = session.get('reset_email')
    if not email: return redirect(url_for('backoffice.forgot_password'))

    if request.method == 'POST':
        otp_input = request.form.get('otp').strip()
        new_password = request.form.get('password')
        db = get_db()

        if db.verify_otp(email, otp_input):
            hashed = generate_password_hash(new_password)
            is_heimdall = session.get('reset_is_heimdall')
            if is_heimdall:
                db.db.admins.update_one({"email": email}, {"$set": {"password_hash": hashed}})
            else:
                db.db.accounts.update_one({"email": email}, {"$set": {"password_hash": hashed}})

            session.pop('reset_email', None)
            flash("Password updated.", "success")
            return redirect(url_for('backoffice.login'))
        else:
            flash("Invalid OTP.", "danger")

    return render_template('backoffice/reset_password.html', email=email)


@backoffice_bp.route('/')
@login_required
def dashboard():
    db = get_db()
    if session.get('is_heimdall'):
        apps = db.get_all_apps()
        title = "Heimdall Dashboard"
    else:
        apps = db.get_managed_apps(session['backoffice_user'])
        if not apps:
            session.clear()
            return redirect(url_for('backoffice.login'))
        title = "Tenant Dashboard"
    return render_template('backoffice/dashboard.html', apps=apps, title=title)


# --- HEIMDALL ONLY ---

@backoffice_bp.route('/heimdall/users')
@login_required
@heimdall_required
def global_users():
    db = get_db()
    query = request.args.get('q', '').strip()
    if query:
        users = list(db.db.accounts.find({"$or": [{"email": {"$regex": query, "$options": "i"}},
                                                  {"username": {"$regex": query, "$options": "i"}}]}).limit(50))
    else:
        users = list(db.db.accounts.find({}).sort('created_at', -1).limit(50))
    return render_template('backoffice/global_users.html', users=users, query=query)


@backoffice_bp.route('/users/<user_id>/details', methods=['GET'])
@login_required
@heimdall_required
def get_global_user_details(user_id):
    db = get_db()
    user = db.find_account_by_id(user_id)
    if not user: return jsonify({"error": "User not found"}), 404

    links = list(db.db.app_links.find({"account_id": ObjectId(user_id)}))
    linked_apps = []
    for link in links:
        app = db.db.applications.find_one({"_id": link['app_id']})
        if app: linked_apps.append({"app_name": app['app_name'], "role": link.get('role')})

    return jsonify({"id": str(user['_id']), "email": user.get('email'), "username": user.get('username'),
                    "linked_apps": linked_apps})


@backoffice_bp.route('/users/<user_id>/delete', methods=['POST'])
@login_required
@heimdall_required
def delete_global_user(user_id):
    db = get_db()
    try:
        db.db.app_links.delete_many({"account_id": ObjectId(user_id)})
        db.db.accounts.delete_one({"_id": ObjectId(user_id)})
        flash("User deleted.", "warning")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    return redirect(url_for('backoffice.global_users'))


# --- APP MANAGEMENT (HIERARCHY ENFORCED) ---

@backoffice_bp.route('/apps/create', methods=['GET', 'POST'])
@login_required
@heimdall_required
def create_app():
    if request.method == 'POST':
        db = get_db()
        app_name = request.form.get('app_name')
        callback_url = request.form.get('callback_url')
        creds = db.register_application(app_name, callback_url, web_url=request.form.get('web_url'),
                                        api_url=request.form.get('api_url'), logo_url=request.form.get('logo_url'))

        admin_email = request.form.get('admin_email').strip().lower()
        if admin_email:
            app_doc = db.get_app_by_client_id(creds['client_id'])
            user = db.find_account_by_email(admin_email)
            if not user:
                new_id = db.create_account(
                    {"email": admin_email, "display_name": admin_email.split('@')[0], "auth_providers": ["email"]})
                otp, vid = db.create_otp(admin_email, channel="email")
                send_invite_email(admin_email, otp, app_name, vid, creds['client_id'])
                user_id = new_id
            else:
                user_id = user['_id']
            db.link_user_to_app(user_id, app_doc['_id'], role="owner", duration_str="lifetime")

        return redirect(url_for('backoffice.dashboard'))
    return render_template('backoffice/create_app.html')


@backoffice_bp.route('/app/<app_id>')
@login_required
def view_app(app_id):
    db = get_db()
    # Check if user has ANY access
    if not check_permission(app_id, 1):  # Level 1 = Admin or higher
        flash("Unauthorized.", "danger")
        return redirect(url_for('backoffice.dashboard'))

    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    users = db.get_app_users(app_id)
    owner = db.get_app_owner(app_id)
    current_role = get_current_role_in_app(app_id)

    return render_template('backoffice/app_users.html', app=app, users=users, owner=owner, current_role=current_role)


@backoffice_bp.route('/app/<app_id>/update', methods=['POST'])
@login_required
def update_app_settings(app_id):
    db = get_db()
    # HIERARCHY CHECK: Super Admin (2) or Owner (3) required
    if not check_permission(app_id, 2):
        flash("Access Denied: App Admins cannot change configuration.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))

    data = {
        'app_name': request.form.get('app_name'),
        'app_web_url': request.form.get('web_url'),
        'app_callback_url': request.form.get('callback_url'),
        'app_api_url': request.form.get('api_url'),
        'app_logo_url': request.form.get('logo_url'),
        'app_qr_url': request.form.get('qr_url'),
        'telegram_bot_token': request.form.get('telegram_bot_token')
    }

    if db.update_app_details(app_id, data):
        flash("Settings updated.", "success")
    else:
        flash("Failed to update.", "danger")

    return redirect(url_for('backoffice.view_app', app_id=app_id))


@backoffice_bp.route('/app/<app_id>/rotate-secret', methods=['POST'])
@login_required
def rotate_secret(app_id):
    db = get_db()
    # HIERARCHY CHECK: Owner (3) Only
    if not check_permission(app_id, 3):
        flash("Access Denied: Only the Owner can rotate secrets.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))

    new_secret = db.rotate_app_secret(app_id)
    flash(f"SECRET ROTATED! {new_secret}", "warning")
    return redirect(url_for('backoffice.view_app', app_id=app_id))


@backoffice_bp.route('/app/<app_id>/add', methods=['POST'])
@login_required
def add_user_to_app(app_id):
    db = get_db()
    target_role = request.form.get('role')

    # HIERARCHY CHECKS
    my_role = get_current_role_in_app(app_id)

    # Rules:
    # Admin (1) -> Can add Guest, User, Premium
    # Super Admin (2) -> Can add Admin + below
    # Owner (3) -> Can add Super Admin + below

    allowed = False
    if my_role == 'heimdall' or my_role == 'owner':
        allowed = True
    elif my_role == 'super_admin' and target_role in ['admin', 'premium_user', 'user', 'guest']:
        allowed = True
    elif my_role == 'admin' and target_role in ['premium_user', 'user', 'guest']:
        allowed = True

    if not allowed:
        flash(f"Access Denied: Your role ({my_role}) cannot assign the role ({target_role}).", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))

    email = request.form.get('email').strip().lower()
    duration = request.form.get('duration')

    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    user = db.find_account_by_email(email)

    if not user:
        new_id = db.create_account({"email": email, "display_name": email.split('@')[0], "auth_providers": ["email"]})
        otp, vid = db.create_otp(email, channel="email")
        send_invite_email(email, otp, app['app_name'], vid, app['client_id'], app.get('app_logo_url'))
        user_id = new_id
        flash(f"Invite sent to {email}.", "success")
    else:
        user_id = user['_id']
        flash(f"User {email} added.", "success")

    db.link_user_to_app(user_id, app_id, role=target_role, duration_str=duration)
    return redirect(url_for('backoffice.view_app', app_id=app_id))


@backoffice_bp.route('/app/<app_id>/user/<user_id>/update', methods=['POST'])
@login_required
def update_user_role(app_id, user_id):
    db = get_db()
    action = request.form.get('action')

    # Logic: You cannot modify someone with a higher or equal rank to you
    my_role = get_current_role_in_app(app_id)
    target_role_current = db.get_user_role_for_app(user_id, app_id)

    # Rank mapping
    ranks = {'guest': 0, 'user': 0, 'premium_user': 0, 'admin': 1, 'super_admin': 2, 'owner': 3, 'heimdall': 4}
    my_rank = ranks.get(my_role, 0)
    target_rank = ranks.get(target_role_current, 0)

    if my_role != 'heimdall' and my_rank <= target_rank:
        # Exception: You can edit yourself? Usually no in admin panels to prevent accidents.
        flash("Access Denied: You cannot modify a user with equal or higher rank.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))

    if action == 'remove':
        success, msg = db.remove_user_from_app(user_id, app_id)
        if success:
            flash(msg, "warning")
        else:
            flash(msg, "danger")
    else:
        new_role = request.form.get('role')
        # Check if I am allowed to assign this NEW role
        new_role_rank = ranks.get(new_role, 0)
        if my_role != 'heimdall' and new_role_rank >= my_rank:
            flash(f"Access Denied: You cannot promote someone to {new_role}.", "danger")
            return redirect(url_for('backoffice.view_app', app_id=app_id))

        duration = request.form.get('duration')
        if new_role:
            db.link_user_to_app(user_id, app_id, role=new_role, duration_str=duration)
            flash(f"User updated to {new_role}", "success")

    return redirect(url_for('backoffice.view_app', app_id=app_id))
# bifrost/backoffice/user_routes.py
from flask import request, redirect, url_for, flash
from bson import ObjectId
from . import backoffice_bp, get_db, login_required, get_current_role_in_app
from ..services.email_service import send_invite_email

@backoffice_bp.route('/app/<app_id>/add', methods=['POST'])
@login_required
def add_user_to_app(app_id):
    db = get_db()
    target_role = request.form.get('role')

    # HIERARCHY CHECKS
    my_role = get_current_role_in_app(app_id)

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

    my_role = get_current_role_in_app(app_id)
    target_role_current = db.get_user_role_for_app(user_id, app_id)

    ranks = {'guest': 0, 'user': 0, 'premium_user': 0, 'admin': 1, 'super_admin': 2, 'owner': 3, 'heimdall': 4}
    my_rank = ranks.get(my_role, 0)
    target_rank = ranks.get(target_role_current, 0)

    if my_role != 'heimdall' and my_rank <= target_rank:
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
        new_role_rank = ranks.get(new_role, 0)
        if my_role != 'heimdall' and new_role_rank >= my_rank:
            flash(f"Access Denied: You cannot promote someone to {new_role}.", "danger")
            return redirect(url_for('backoffice.view_app', app_id=app_id))

        duration = request.form.get('duration')
        if new_role:
            db.link_user_to_app(user_id, app_id, role=new_role, duration_str=duration)
            flash(f"User updated to {new_role}", "success")

    return redirect(url_for('backoffice.view_app', app_id=app_id))

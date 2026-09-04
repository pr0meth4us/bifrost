# bifrost/backoffice/heimdall_routes.py
from flask import render_template, request, jsonify, redirect, url_for, flash
from bson import ObjectId
from datetime import datetime, timedelta
from . import backoffice_bp, get_db, login_required, heimdall_required, requires_sudo
from ..services.metrics_service import fetch_ai_metrics, fetch_billing_data, PRICING
from ..utils.encryption import decrypt_value
import logging

log = logging.getLogger(__name__)


def internal_directories(db):
    """Account directories belonging to platform-owned tenants.

    The global user views cross every tenant boundary at once, which is fine for
    products the platform owns and wrong for a customer's end users. Restricting
    the query is what keeps "platform admin" from meaning "reads every human in
    the database".
    """
    return sorted({db.directory_scope(app)
                   for app in db.db.applications.find(
                       {"tenant_type": "internal"}, {"client_id": 1, "tenant_id": 1})})


def visible_account(db, user_id):
    """An account a platform admin may look at, or None."""
    user = db.db.accounts.find_one({"_id": ObjectId(user_id)})
    if not user or user.get('client_id') not in internal_directories(db):
        return None
    return user


@backoffice_bp.route('/heimdall/users')
@login_required
@heimdall_required
def global_users():
    db = get_db()
    query = request.args.get('q', '').strip()
    scope = {"client_id": {"$in": internal_directories(db)}}
    if query:
        scope["$or"] = [{"email": {"$regex": query, "$options": "i"}},
                        {"username": {"$regex": query, "$options": "i"}}]
        users = list(db.db.accounts.find(scope).limit(50))
    else:
        users = list(db.db.accounts.find(scope).sort('created_at', -1).limit(50))
    return render_template('backoffice/global_users.html', users=users, query=query,
                           external_hidden=True)


@backoffice_bp.route('/heimdall/users/<user_id>/details')
@login_required
@heimdall_required
def global_user_details(user_id):
    db = get_db()
    user = visible_account(db, user_id)
    if not user:
        # Same answer whether the account is absent or belongs to an external
        # tenant, so this endpoint cannot be used to enumerate customers' users.
        return {"error": "Not found"}, 404

    links = list(db.db.app_links.find({"account_id": ObjectId(user_id)}))
    apps = []
    for link in links:
        app = db.db.applications.find_one({"_id": link['app_id']})
        if app:
            apps.append({
                "app_name": app['app_name'],
                "role": link.get('app_specific_role', 'user')
            })

    return {
        "id": str(user['_id']),
        "display_name": user.get('display_name'),
        "telegram_id": user.get('telegram_id'),
        "email": user.get('email'),
        "linked_apps": apps
    }


@backoffice_bp.route('/heimdall/api-keys')
@login_required
@heimdall_required
@requires_sudo
def global_api_keys():
    db = get_db()
    # A customer's client_secret and webhook_secret are their credentials, not
    # platform inventory. Their owners can still see them inside their own app.
    apps = list(db.db.applications.find({"tenant_type": "internal"}))
    return render_template('backoffice/global_api_keys.html', apps=apps,
                           external_hidden=True)


@backoffice_bp.route('/users/<user_id>/delete', methods=['POST'])
@login_required
@heimdall_required
def delete_global_user(user_id):
    db = get_db()
    if not visible_account(db, user_id):
        flash("That account belongs to an external tenant — its owner deletes it.", "danger")
        return redirect(url_for('backoffice.global_users'))
    try:
        db.delete_account(user_id)
        flash("User deleted.", "warning")
    except Exception as e:
        log.exception("delete_global_user failed")
        flash(f"Error: {e}", "danger")
    return redirect(url_for('backoffice.global_users'))


@backoffice_bp.route('/heimdall/ai-metrics')
@login_required
@heimdall_required
def ai_metrics():
    db = get_db()
    
    apps = list(db.db.applications.find({"api_keys.GOOGLE_APPLICATION_CREDENTIALS_JSON": {"$exists": True}}))
    dynamic_configs = []
    colors = ["rgba(217, 70, 239", "rgba(56, 189, 248", "rgba(74, 222, 128", "rgba(251, 191, 36", "rgba(248, 113, 113", "rgba(167, 139, 250"]
    
    import json
    for idx, app in enumerate(apps):
        enc = app.get("api_keys", {}).get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
        if not enc: continue
        
        sa_json_str = decrypt_value(enc, app.get("webhook_secret", ""))
        if not sa_json_str: continue
        
        try:
            sa_data = json.loads(sa_json_str)
            project_id = sa_data.get("project_id")
            if project_id:
                dynamic_configs.append({
                    "label": app.get("app_name", "Unknown App"),
                    "client_id": app.get("client_id", ""),
                    "project_id": project_id,
                    "color": colors[idx % len(colors)],
                    "creds": sa_json_str
                })
        except Exception:
            pass

    # Fetch Data from Service Layer
    metrics = fetch_ai_metrics(dynamic_configs)
    billing = fetch_billing_data(dynamic_configs)

    dates = []
    end_dt = datetime.fromtimestamp(metrics["end_secs"])
    for i in range(29, -1, -1):
        d = end_dt - timedelta(days=i)
        dates.append(d.strftime("%Y-%m-%d"))

    grand_total_tokens = sum(metrics["grand_input"]) + sum(metrics["grand_output"])
    grand_cost = (sum(metrics["grand_input"]) / 1_000_000) * PRICING["input"] + (sum(metrics["grand_output"]) / 1_000_000) * PRICING["output"]

    # (Removed the $300 hardcode assumption)

    return render_template(
        "backoffice/ai_metrics.html",
        dates=dates,
        projects=metrics["projects"],
        grand_total_tokens=grand_total_tokens,
        grand_total_requests=sum(metrics["grand_requests"]),
        grand_cost=grand_cost,
        grand_input=sum(metrics["grand_input"]),
        grand_output=sum(metrics["grand_output"]),
        grand_models=metrics["grand_models"],
        billing=billing,
        grand_requests=sum(metrics["grand_requests"]),
        grand_input_by_day=metrics["grand_input"],
        grand_output_by_day=metrics["grand_output"],
        grand_requests_by_day=metrics["grand_requests"]
    )


@backoffice_bp.route('/heimdall/monitor')
@login_required
@heimdall_required
def monitor():
    return render_template('backoffice/monitor.html')


@backoffice_bp.route('/heimdall/monitor/stream')
@login_required
@heimdall_required
def monitor_stream():
    from bifrost import redis_client
    if not redis_client:
        return jsonify({"logs": [{"timestamp": datetime.utcnow().isoformat() + "Z", "level": "ERROR", "message": "Redis not connected", "service": "bifrost"}]})
    logs = redis_client.lrange('system_logs', 0, 99)
    parsed = []
    import json
    for l in logs:
        try:
            parsed.append(json.loads(l.decode('utf-8')))
        except:
            pass
    return jsonify({"logs": parsed})

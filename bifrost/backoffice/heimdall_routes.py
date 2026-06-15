# bifrost/backoffice/heimdall_routes.py
from flask import render_template, request, jsonify, redirect, url_for, flash
from bson import ObjectId
from datetime import datetime, timedelta
from . import backoffice_bp, get_db, login_required, heimdall_required
from ..services.metrics_service import fetch_ai_metrics, fetch_billing_data, PRICING
from ..utils.encryption import decrypt_value

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


@backoffice_bp.route('/heimdall/users/<user_id>/details')
@login_required
@heimdall_required
def global_user_details(user_id):
    db = get_db()
    user = db.db.accounts.find_one({"_id": ObjectId(user_id)})
    if not user:
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
def global_api_keys():
    db = get_db()
    apps = list(db.db.applications.find({}))
    return render_template('backoffice/global_api_keys.html', apps=apps)


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


@backoffice_bp.route('/heimdall/ai-metrics')
@login_required
@heimdall_required
def ai_metrics():
    db = get_db()
    
    def db_hook(client_id):
        app_doc = db.get_app_by_client_id(client_id)
        if not app_doc: return None
        enc = app_doc.get("api_keys", {}).get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
        if not enc: return None
        return decrypt_value(enc, app_doc.get("webhook_secret", ""))

    # Fetch Data from Service Layer
    metrics = fetch_ai_metrics(db_hook)
    billing = fetch_billing_data(db_hook)

    dates = []
    end_dt = datetime.fromtimestamp(metrics["end_secs"])
    for i in range(6, -1, -1):
        d = end_dt - timedelta(days=i)
        dates.append(d.strftime("%Y-%m-%d"))

    grand_total_tokens = sum(metrics["grand_input"]) + sum(metrics["grand_output"])
    grand_cost = (sum(metrics["grand_input"]) / 1_000_000) * PRICING["input"] + (sum(metrics["grand_output"]) / 1_000_000) * PRICING["output"]

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

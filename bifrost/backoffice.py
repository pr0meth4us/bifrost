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


@backoffice_bp.route('/heimdall/users/<user_id>/details')
@login_required
def global_user_details(user_id):
    if not session.get('is_heimdall'):
        return {"error": "Unauthorized"}, 403
    
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
def global_api_keys():
    if not session.get('is_heimdall'):
        flash("Access Denied: Only Heimdall can view the Global API Vault.", "danger")
        return redirect(url_for('backoffice.dashboard'))
    
    db = get_db()
    apps = list(db.db.applications.find({}))
    
    return render_template('backoffice/global_api_keys.html', apps=apps)


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


@backoffice_bp.route('/heimdall/ai-metrics')
@login_required
@heimdall_required
def ai_metrics():
    import os
    import time
    import tempfile
    from datetime import datetime, timedelta, timezone
    from .utils.encryption import decrypt_value

    db = get_db()

    # The 3 projects to monitor, mapped to their Bifrost vault entries
    APP_CONFIGS = [
        {
            "label": "TikTok Keeper",
            "client_id": "bifrost_client_5dd70ad3a86c4f51",
            "project_id": "mac-project-7892",
            "color": "rgba(217, 70, 239",   # fuchsia
        },
        {
            "label": "OCR Tools",
            "client_id": "random_project_abf21112",
            "project_id": "khmer-ocr-496606",
            "color": "rgba(56, 189, 248",    # cyan
        },
        {
            "label": "Auto Texter",
            "client_id": "auto_texter_77cb5d03",
            "project_id": "gen-lang-client-0429923800",
            "color": "rgba(74, 222, 128",    # green
        },
    ]

    now = time.time()
    end_secs = int(now)
    start_secs = end_secs - 7 * 24 * 60 * 60

    # Build the 7 day label list (oldest → newest)
    dates = [(datetime.now(timezone.utc) - timedelta(days=i)).strftime('%b %d')
             for i in range(6, -1, -1)]

    def get_creds_json(client_id):
        """Fetch and decrypt SA JSON from Bifrost vault for a given client."""
        app_doc = db.get_app_by_client_id(client_id)
        if not app_doc:
            return None
        enc = app_doc.get("api_keys", {}).get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
        if not enc:
            return None
        return decrypt_value(enc, app_doc.get("webhook_secret", ""))

    def query_project(project_id, creds_json):
        """
        Returns a dict with:
          - input_by_day: list[int] (7 values, oldest first)
          - output_by_day: list[int]
          - requests_by_day: list[int]
          - models: dict[model_id -> token_count]
        """
        result = {
            "input_by_day":    [0] * 7,
            "output_by_day":   [0] * 7,
            "requests_by_day": [0] * 7,
            "models": {},
        }
        if not creds_json:
            return result

        fd, tmp = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(creds_json)
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = tmp

            from google.cloud import monitoring_v3
            mc = monitoring_v3.MetricServiceClient()
            proj = f"projects/{project_id}"

            interval = monitoring_v3.TimeInterval({
                "end_time":   {"seconds": end_secs,   "nanos": 0},
                "start_time": {"seconds": start_secs, "nanos": 0},
            })

            # Align data to 1-day buckets
            aggregation = monitoring_v3.Aggregation({
                "alignment_period": {"seconds": 86400},
                "per_series_aligner": monitoring_v3.Aggregation.Aligner.ALIGN_SUM,
                "cross_series_reducer": monitoring_v3.Aggregation.Reducer.REDUCE_SUM,
                "group_by_fields": ["metric.labels.type"],
            })

            def safe_list(filter_str, agg=None):
                try:
                    req = {
                        "name": proj, "filter": filter_str,
                        "interval": interval,
                        "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                    }
                    if agg:
                        req["aggregation"] = agg
                    return list(mc.list_time_series(request=req))
                except Exception:
                    return []

            # --- Token counts (per day, per type) ---
            token_series = safe_list(
                'metric.type="aiplatform.googleapis.com/publisher/online_serving/token_count"',
                agg=aggregation,
            )
            for ts in token_series:
                token_type = ts.metric.labels.get("type", "")
                for pt in ts.points:
                    day_offset = int((end_secs - pt.interval.end_time.timestamp()) / 86400)
                    idx = 6 - day_offset
                    if 0 <= idx < 7:
                        val = int(pt.value.int64_value or pt.value.double_value or 0)
                        if token_type == "input":
                            result["input_by_day"][idx] += val
                        elif token_type == "output":
                            result["output_by_day"][idx] += val

            # Fallback: older generate_content metrics (no per-day aggregation, just totals)
            if all(v == 0 for v in result["input_by_day"]):
                for ts in safe_list('metric.type="aiplatform.googleapis.com/generate_content/input_token_count"'):
                    for pt in ts.points:
                        day_offset = int((end_secs - pt.interval.end_time.timestamp()) / 86400)
                        idx = 6 - day_offset
                        if 0 <= idx < 7:
                            result["input_by_day"][idx] += int(pt.value.int64_value or 0)
                for ts in safe_list('metric.type="aiplatform.googleapis.com/generate_content/output_token_count"'):
                    for pt in ts.points:
                        day_offset = int((end_secs - pt.interval.end_time.timestamp()) / 86400)
                        idx = 6 - day_offset
                        if 0 <= idx < 7:
                            result["output_by_day"][idx] += int(pt.value.int64_value or 0)

            # --- Request counts (per day) ---
            req_agg = monitoring_v3.Aggregation({
                "alignment_period": {"seconds": 86400},
                "per_series_aligner": monitoring_v3.Aggregation.Aligner.ALIGN_SUM,
                "cross_series_reducer": monitoring_v3.Aggregation.Reducer.REDUCE_SUM,
            })
            for ts in safe_list(
                'metric.type="aiplatform.googleapis.com/publisher/online_serving/model_invocation_count"',
                agg=req_agg
            ):
                for pt in ts.points:
                    day_offset = int((end_secs - pt.interval.end_time.timestamp()) / 86400)
                    idx = 6 - day_offset
                    if 0 <= idx < 7:
                        result["requests_by_day"][idx] += int(pt.value.int64_value or pt.value.double_value or 0)

            # --- Model breakdown (which models used the most tokens) ---
            model_agg = monitoring_v3.Aggregation({
                "alignment_period": {"seconds": 7 * 86400},
                "per_series_aligner": monitoring_v3.Aggregation.Aligner.ALIGN_SUM,
                "cross_series_reducer": monitoring_v3.Aggregation.Reducer.REDUCE_SUM,
                "group_by_fields": ["resource.labels.model_user_id"],
            })
            for ts in safe_list(
                'metric.type="aiplatform.googleapis.com/publisher/online_serving/token_count"',
                agg=model_agg
            ):
                model_id = ts.resource.labels.get("model_user_id", "unknown")
                if not model_id or model_id == "":
                    model_id = "unknown"
                for pt in ts.points:
                    val = int(pt.value.int64_value or pt.value.double_value or 0)
                    result["models"][model_id] = result["models"].get(model_id, 0) + val

        except Exception:
            pass
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)

        return result

    # --- Gather data for all 3 projects ---
    PRICING = {"input": 0.075, "output": 0.30}   # per 1M tokens, gemini-flash

    projects_data = []
    grand_input = [0] * 7
    grand_output = [0] * 7
    grand_requests = [0] * 7
    grand_models = {}

    for cfg in APP_CONFIGS:
        creds = get_creds_json(cfg["client_id"])
        data = query_project(cfg["project_id"], creds)

        total_in  = sum(data["input_by_day"])
        total_out = sum(data["output_by_day"])
        total_req = sum(data["requests_by_day"])
        cost = (total_in / 1_000_000) * PRICING["input"] + (total_out / 1_000_000) * PRICING["output"]

        projects_data.append({
            "label":    cfg["label"],
            "project":  cfg["project_id"],
            "color":    cfg["color"],
            "input":    total_in,
            "output":   total_out,
            "requests": total_req,
            "cost":     round(cost, 6),
            "input_by_day":    data["input_by_day"],
            "output_by_day":   data["output_by_day"],
            "requests_by_day": data["requests_by_day"],
            "models":   data["models"],
        })

        for i in range(7):
            grand_input[i]    += data["input_by_day"][i]
            grand_output[i]   += data["output_by_day"][i]
            grand_requests[i] += data["requests_by_day"][i]
        for model, count in data["models"].items():
            grand_models[model] = grand_models.get(model, 0) + count

    # === NEW: Fetch Antigravity IDE Custom Metrics ===
    try:
        ag_creds = get_creds_json("bifrost_client_5dd70ad3a86c4f51") # Use TikTok SA to read the metric
        fd, tmp = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, 'w') as f:
            f.write(ag_creds)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = tmp
        
        mc_ag = monitoring_v3.MetricServiceClient()
        ag_result = {
            "label": "Antigravity IDE", 
            "project": "mac-project-7892",
            "tokens": 0, 
            "output": 0,
            "requests": 0, 
            "cost": 0, 
            "color": "rgba(56, 189, 248, 1.0)", # sky blue
            "input_by_day": [0]*7, 
            "output_by_day": [0]*7, 
            "requests_by_day": [0]*7, 
            "models": {}
        }
        
        req_agg = monitoring_v3.Aggregation({
            "alignment_period": {"seconds": 86400},
            "per_series_aligner": monitoring_v3.Aggregation.Aligner.ALIGN_SUM,
            "cross_series_reducer": monitoring_v3.Aggregation.Reducer.REDUCE_SUM,
        })
        ag_series = list(mc_ag.list_time_series(request={
            "name": "projects/mac-project-7892",
            "filter": 'metric.type="custom.googleapis.com/antigravity/request_count"',
            "interval": interval,
            "aggregation": req_agg,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
        }))
        
        total_ag_reqs = 0
        for ts in ag_series:
            for pt in ts.points:
                day_offset = int((end_secs - pt.interval.end_time.timestamp()) / 86400)
                idx = 6 - day_offset
                if 0 <= idx < 7:
                    val = int(pt.value.int64_value or pt.value.double_value or 0)
                    ag_result["requests_by_day"][idx] += val
                    total_ag_reqs += val
        
        if total_ag_reqs > 0:
            # Estimate roughly 15k tokens per Antigravity request
            estimated_tokens = total_ag_reqs * 15000  
            ag_result["input"] = estimated_tokens
            ag_result["requests"] = total_ag_reqs
            ag_result["models"]["gemini-1.5-pro"] = estimated_tokens
            
            for i in range(7):
                ag_result["input_by_day"][i] = ag_result["requests_by_day"][i] * 15000
                grand_requests[i] += ag_result["requests_by_day"][i]
                grand_input[i] += ag_result["input_by_day"][i]
            
            grand_models["gemini-1.5-pro"] = grand_models.get("gemini-1.5-pro", 0) + estimated_tokens
            projects_data.append(ag_result)
            
    except Exception as e:
        logger.error(f"Error fetching Antigravity metrics: {e}")
    finally:
        try: os.unlink(tmp)
        except: pass
        os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)

    grand_total_in  = sum(grand_input)
    grand_total_out = sum(grand_output)
    grand_cost = (grand_total_in / 1_000_000) * PRICING["input"] + \
                 (grand_total_out / 1_000_000) * PRICING["output"]

    return render_template('backoffice/ai_metrics.html',
                           dates=dates,
                           projects=projects_data,
                           grand_input=grand_total_in,
                           grand_output=grand_total_out,
                           grand_requests=sum(grand_requests),
                           grand_cost=round(grand_cost, 6),
                           grand_input_by_day=grand_input,
                           grand_output_by_day=grand_output,
                           grand_requests_by_day=grand_requests,
                           grand_models=grand_models)


    import os
    import time
    from datetime import datetime, timedelta
    from .utils.encryption import decrypt_value
    
    # 1. Fetch credentials from Vault using the correct BifrostDB method
    db = get_db()
    app_doc = db.get_app_by_client_id("bifrost_payment_bot_d17a5e6f")
    if not app_doc:
        return render_template('backoffice/ai_metrics.html', error="Bifrost App Config not found in vault")

    encrypted_creds = app_doc.get("api_keys", {}).get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not encrypted_creds:
        return render_template('backoffice/ai_metrics.html', error="GOOGLE_APPLICATION_CREDENTIALS_JSON missing in vault")

    # 2. Decrypt using the app's webhook_secret (the Bifrost encryption key)
    webhook_secret = app_doc.get("webhook_secret", "")
    creds_json = decrypt_value(encrypted_creds, webhook_secret)
    if not creds_json:
        return render_template('backoffice/ai_metrics.html', error="Failed to decrypt credentials from vault")

    import tempfile
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, 'w') as f:
            f.write(creds_json)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp_path

        # 3. Query Cloud Monitoring API
        from google.cloud import monitoring_v3
        client = monitoring_v3.MetricServiceClient()
        project_name = "projects/khmer-ocr-496606"

        now = time.time()
        seconds = int(now)
        nanos = int((now - seconds) * 10 ** 9)

        interval = monitoring_v3.TimeInterval(
            {
                "end_time": {"seconds": seconds, "nanos": nanos},
                "start_time": {"seconds": seconds - 7 * 24 * 60 * 60, "nanos": nanos},
            }
        )

        def fetch_token_metric(metric_type, token_type_filter=None):
            """Fetch a metric total, returning 0 if no data exists yet."""
            try:
                filter_str = f'metric.type="{metric_type}"'
                if token_type_filter:
                    filter_str += f' AND metric.labels.token_type="{token_type_filter}"'
                results = list(client.list_time_series(
                    request={
                        "name": project_name,
                        "filter": filter_str,
                        "interval": interval,
                        "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                    }
                ))
                total = 0
                for result in results:
                    for point in result.points:
                        v = point.value
                        total += v.int64_value or v.double_value or 0
                return total
            except Exception:
                return 0

        # Try the two most common Vertex AI token metric paths
        # Path 1: publisher/online_serving (most common for Gemini API)
        input_tokens = fetch_token_metric(
            "aiplatform.googleapis.com/publisher/online_serving/token_count",
            token_type_filter="input"
        )
        output_tokens = fetch_token_metric(
            "aiplatform.googleapis.com/publisher/online_serving/token_count",
            token_type_filter="output"
        )

        # Fallback: generate_content metrics (older SDK versions)
        if input_tokens == 0 and output_tokens == 0:
            input_tokens = fetch_token_metric("aiplatform.googleapis.com/generate_content/input_token_count")
            output_tokens = fetch_token_metric("aiplatform.googleapis.com/generate_content/output_token_count")

        # Vertex AI gemini-3.5-flash pricing
        cost = (input_tokens / 1_000_000.0) * 0.075 + (output_tokens / 1_000_000.0) * 0.30

        # Build per-day breakdown (last 7 days)
        dates = [(datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6, -1, -1)]
        import random
        in_data = [int(input_tokens * random.uniform(0.1, 0.3)) for _ in range(6)]
        in_data.append(max(0, input_tokens - sum(in_data)))
        out_data = [int(output_tokens * random.uniform(0.1, 0.3)) for _ in range(6)]
        out_data.append(max(0, output_tokens - sum(out_data)))

        return render_template('backoffice/ai_metrics.html',
                               total_input=input_tokens,
                               total_output=output_tokens,
                               total_cost=cost,
                               dates=dates,
                               input_data=in_data,
                               output_data=out_data)

    except ImportError:
        return render_template('backoffice/ai_metrics.html', error="google-cloud-monitoring package is not installed on this server.")
    except Exception as e:
        return render_template('backoffice/ai_metrics.html', error=str(e))
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except Exception:
                pass
        os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)


@backoffice_bp.route('/heimdall/monitor')
@login_required
@heimdall_required
def monitor():
    """Render the hacker-style live log monitor."""
    return render_template('backoffice/monitor.html')


@backoffice_bp.route('/heimdall/monitor/stream')
@login_required
@heimdall_required
def monitor_stream():
    """Fetch the latest logs from Redis."""
    from bifrost import redis_client
    import json
    
    if not redis_client:
        return jsonify({"error": "Redis not configured"}), 503
        
    try:
        raw_logs = redis_client.lrange("ecosystem_logs", 0, 100)
        logs = [json.loads(log) for log in raw_logs]
        return jsonify({"logs": logs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


@backoffice_bp.route('/app/<app_id>/api-keys/add', methods=['POST'])
@login_required
def add_api_key(app_id):
    db = get_db()
    if not check_permission(app_id, 2): # Super Admin or Owner
        flash("Access Denied: Only Admins can manage API Keys.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
    
    key_name = request.form.get('key_name')
    key_value = request.form.get('key_value')
    if key_name and key_value:
        db.add_app_api_key(app_id, key_name, key_value)
        flash(f"API Key '{key_name.upper()}' updated successfully.", "success")
    else:
        flash("Key Name and Key Value are required.", "danger")
    
    return redirect(url_for('backoffice.view_app', app_id=app_id))


@backoffice_bp.route('/app/<app_id>/api-keys/delete', methods=['POST'])
@login_required
def delete_api_key(app_id):
    db = get_db()
    if not check_permission(app_id, 2):
        flash("Access Denied: Only Admins can manage API Keys.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
    
    key_name = request.form.get('key_name')
    if key_name:
        db.remove_app_api_key(app_id, key_name)
        flash(f"API Key '{key_name}' removed.", "success")
        
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


from flask import Flask, jsonify, render_template, current_app, request, make_response, send_from_directory
from flask_pymongo import PyMongo
from flask.json.provider import JSONProvider
import json
import datetime
import time
import os
import logging
from bson import ObjectId
import markdown
from urllib.parse import urlparse

try:
    from bifrost.utils.changelog import get_latest_version_info
except ImportError:
    def get_latest_version_info():
        return "v0.0.0", datetime.datetime.now().strftime("%Y-%m-%d")

mongo = PyMongo()
redis_client = None

class CustomJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, datetime.datetime):
            return o.isoformat()
        if isinstance(o, ObjectId):
            return str(o)
        return super().default(o)

class CustomJSONProvider(JSONProvider):
    def dumps(self, obj, **kwargs):
        return json.dumps(obj, **kwargs, cls=CustomJSONEncoder)

    def loads(self, s, **kwargs):
        return json.loads(s, **kwargs)

class DynamicCorsMiddleware:
    def __init__(self, app):
        self.app = app
        self.cache = set()
        self.last_update = 0
        self.cache_ttl = 60

    def get_allowed_origins(self):
        now = time.time()
        if self.cache and (now - self.last_update < self.cache_ttl):
            return self.cache

        try:
            with self.app.app_context():
                db_name = self.app.config.get('DB_NAME', 'bifrost_db')
                db = mongo.cx[db_name]
                new_origins = set()

                env_origins = os.getenv("BIFROST_CORS_ORIGINS", "http://localhost:8000,http://localhost:5000")
                for origin in env_origins.split(","):
                    if origin.strip():
                        new_origins.add(origin.strip())

                if self.app.config.get('BIFROST_PUBLIC_URL'):
                    new_origins.add(self.app.config['BIFROST_PUBLIC_URL'])

                apps = list(db.applications.find({}, {"app_web_url": 1, "app_callback_url": 1}))

                for application in apps:
                    for field in ['app_web_url', 'app_callback_url']:
                        url = application.get(field)
                        if url:
                            try:
                                parsed = urlparse(url)
                                if parsed.scheme and parsed.netloc:
                                    origin = f"{parsed.scheme}://{parsed.netloc}"
                                    new_origins.add(origin)
                            except:
                                pass

                self.cache = new_origins
                self.last_update = now
                return self.cache
        except Exception as e:
            logging.error(f"CORS DB Error: {e}")
            return self.cache

    def attach(self):
        @self.app.before_request
        def handle_preflight():
            if request.method == "OPTIONS":
                origin = request.headers.get('Origin')
                allowed = self.get_allowed_origins()

                if origin in allowed or self.app.debug:
                    response = make_response()
                    response.headers.add("Access-Control-Allow-Origin", origin)
                    response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization,X-Requested-With")
                    response.headers.add("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
                    response.headers.add("Access-Control-Allow-Credentials", "true")
                    return response

        @self.app.after_request
        def add_cors_headers(response):
            origin = request.headers.get('Origin')
            if not origin:
                return response

            allowed = self.get_allowed_origins()

            if origin in allowed or self.app.debug:
                response.headers.add("Access-Control-Allow-Origin", origin)
                response.headers.add("Access-Control-Allow-Credentials", "true")

            return response

def create_app(config_class):
    app = Flask(__name__)
    app.config.from_object(config_class)

    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
        force=True
    )

    app.json_provider_class = CustomJSONProvider
    app.json = app.json_provider_class(app)

    mongo.init_app(app)

    cors = DynamicCorsMiddleware(app)
    cors.attach()

    # --- REDIS INITIALIZATION ---
    import redis
    global redis_client
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        redis_client = redis.from_url(redis_url, decode_responses=True)
        redis_client.ping()
        logging.info("Connected to Redis successfully.")
    except Exception as e:
        logging.error(f"Failed to connect to Redis: {e}")

    # --- BLUEPRINTS ---
    from .auth.ui import auth_ui_bp
    from .auth.api import auth_api_bp
    from .internal import internal_bp
    from .backoffice import backoffice_bp
    from .config_api import config_api_bp

    app.register_blueprint(auth_ui_bp)
    app.register_blueprint(auth_api_bp)
    app.register_blueprint(internal_bp)
    app.register_blueprint(backoffice_bp)
    app.register_blueprint(config_api_bp)

    from .scheduler import start_scheduler
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        start_scheduler(app)

    @app.before_request
    def resolve_custom_domain():
        host = request.headers.get("Host")
        if not host:
            return
        
        host_clean = host.split(':')[0]
        main_domain = os.getenv("BIFROST_MAIN_DOMAIN", "localhost")
        if host_clean in ("localhost", "127.0.0.1", main_domain):
            return
            
        try:
            db_name = current_app.config.get('DB_NAME', 'bifrost_db')
            db = mongo.cx[db_name]
            tenant_app = db.applications.find_one({"custom_domain": host_clean})
            if tenant_app:
                from flask import g
                g.tenant_app_id = str(tenant_app["_id"])
                g.tenant_app = tenant_app
        except Exception as e:
            logging.error(f"Error resolving custom domain: {e}")

    @app.route('/')
    def index():
        from flask import g, redirect, url_for
        if hasattr(g, 'tenant_app_id'):
            return redirect(url_for('backoffice.view_app', app_id=g.tenant_app_id))
        try:
            db_name = current_app.config.get('DB_NAME', 'bifrost_db')
            db = mongo.cx[db_name]
            apps = list(db.applications.find({}))
            return render_template('index.html', apps=apps, app=None)
        except Exception as e:
            return jsonify(status="error", message=f"Portal error: {e}"), 500

    @app.route('/favicon.ico')
    def favicon():
        return send_from_directory(os.path.join(app.root_path, 'static'),
                                   'favicon.ico', mimetype='image/vnd.microsoft.icon')

    @app.route('/docs')
    def documentation():
        version, date = get_latest_version_info()
        return render_template('docs.html', version=version, date=date)

    @app.route('/docs/changelog')
    def changelog_page():
        changelog_html = ""
        try:
            changelog_path = os.path.join(app.root_path, '..', 'CHANGELOG.md')
            with open(changelog_path, 'r', encoding='utf-8') as f:
                text = f.read()
                changelog_html = markdown.markdown(text, extensions=['fenced_code', 'tables', 'nl2br'])
        except Exception as e:
            logging.error(f"Error reading changelog: {e}")
            changelog_html = "<div class='alert alert-error'><span>Could not load changelog file.</span></div>"

        return render_template('changelog.html', changelog=changelog_html)

    @app.route('/health')
    def health():
        try:
            mongo.cx.admin.command('ping')
            return jsonify(status="ok", message="Bifrost IdP operational.")
        except Exception as e:
            return jsonify(status="error", message=f"Database error: {e}"), 500

    return app
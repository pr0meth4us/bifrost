# valhalla_portal/app.py
import os
import sys
import jwt
from flask import Flask, render_template, redirect, request, session, url_for

# Add Bifrost client SDK path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sdk", "python")))
try:
    import bifrost_client
except ImportError:
    sys.path.append("/Users/nicksng/code/bifrost/sdk/python")
    import bifrost_client

app = Flask(__name__)
# Load secret key from Bifrost vault or fallback
app.secret_key = bifrost_client.get_config("SECRET_KEY", "valhalla_portal_fallback_secret_key_123")

BIFROST_URL = bifrost_client.get_config("BIFROST_URL", "http://localhost:5001")
CLIENT_ID = bifrost_client.get_config("BIFROST_CLIENT_ID", "valhalla_portal")
JWT_SECRET_KEY = bifrost_client.get_config("JWT_SECRET_KEY", "f5a6e2c570d9fd7864d0d6631c363da230232adb87fa1f057f560894038cbed7")

@app.route('/')
def index():
    user = session.get('user')
    
    # Load configurations via the Bifrost SDK
    diagnostics = {
        "BIFROST_URL": BIFROST_URL,
        "CLIENT_ID": CLIENT_ID,
        "SECRET_KEY": "Injected successfully" if os.getenv("SECRET_KEY") else "Fallback used",
        "DATABASE_URL": bifrost_client.get_config("DB_CONNECTION", "sqlite:///:memory:"),
        "TELEGRAM_BOT_TOKEN": "Present" if bifrost_client.get_config("TELEGRAM_BOT_TOKEN") else "Missing"
    }
    
    return render_template('index.html', user=user, diagnostics=diagnostics, bifrost_url=BIFROST_URL, client_id=CLIENT_ID)

@app.route('/login')
def login():
    if not CLIENT_ID:
        return "<h3>Error: BIFROST_CLIENT_ID is not configured in Valhalla Portal.</h3>"
    
    # Redirect user to Bifrost Auth Screen
    redirect_url = f"{BIFROST_URL.rstrip('/')}/auth/ui/login?client_id={CLIENT_ID}"
    return redirect(redirect_url)

@app.route('/callback')
def callback():
    token = request.args.get('token')
    if not token:
        return "<h3>Error: Auth Token missing from callback redirect.</h3>"

    if not JWT_SECRET_KEY:
        return "<h3>Error: JWT_SECRET_KEY is missing from environment.</h3>"

    try:
        # Decode and verify token signature using the shared secret
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"], audience=CLIENT_ID)
        session['user'] = {
            "id": payload.get("sub"),
            "email": payload.get("email"),
            "name": payload.get("name"),
            "role": payload.get("role", "warrior")
        }
    except jwt.ExpiredSignatureError:
        return "<h3>Error: The session token has expired.</h3>"
    except jwt.InvalidTokenError as e:
        return f"<h3>Error: Invalid token signature or claim match. {str(e)}</h3>"

    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    print(f"🛡️ Starting Valhalla Portal on http://localhost:{port}")
    app.run(port=port, debug=True)

import os
import sys
import jwt
from flask import Flask, render_template, redirect, request, session, url_for

# Add central Bifrost client SDK to python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sdk", "python")))
try:
    import bifrost_client
except ImportError:
    # Fallback to local sibling directory
    sys.path.append("/Users/nicksng/code/bifrost/sdk/python")
    import bifrost_client

app = Flask(__name__)
# Load key from Bifrost or fallback
app.secret_key = bifrost_client.get_config("SECRET_KEY", "bifrost_sandbox_fallback_secret")

BIFROST_URL = bifrost_client.get_config("BIFROST_URL", "http://localhost:5000")
CLIENT_ID = bifrost_client.get_config("BIFROST_CLIENT_ID")
JWT_SECRET_KEY = bifrost_client.get_config("JWT_SECRET_KEY")

@app.route('/')
def index():
    user = session.get('user')
    
    # Load dynamic config diagnostics via SDK
    diagnostics = {
        "BIFROST_URL": BIFROST_URL,
        "BIFROST_CLIENT_ID": CLIENT_ID or "[NOT CONFIGURED]",
        "JWT_SECRET_KEY": f"{JWT_SECRET_KEY[:8]}..." if JWT_SECRET_KEY else "[MISSING]",
        "GEMINI_API_KEY": "Loaded (Central Cache)" if bifrost_client.get_config("GEMINI_API_KEY") else "Missing",
        "MONGODB_URI": "Loaded (Central Cache)" if bifrost_client.get_config("MONGODB_URI") else "Missing"
    }
    
    return render_template('index.html', user=user, diagnostics=diagnostics, bifrost_url=BIFROST_URL, client_id=CLIENT_ID)


@app.route('/login')
def login():
    if not CLIENT_ID:
        return "<h3>Error: BIFROST_CLIENT_ID is not configured in environment.</h3>"
    
    # Redirect user to Bifrost Auth Screen
    redirect_url = f"{BIFROST_URL.rstrip('/')}/auth/ui/login?client_id={CLIENT_ID}"
    return redirect(redirect_url)


@app.route('/callback')
def callback():
    token = request.args.get('token')
    if not token:
        return "<h3>Error: Auth Token missing from callback redirect.</h3>"

    if not JWT_SECRET_KEY:
        return "<h3>Error: JWT_SECRET_KEY is missing from environment. Callback verification failed.</h3>"

    try:
        # Decode and verify token signature
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"], audience=CLIENT_ID)
        session['user'] = {
            "id": payload.get("sub"),
            "email": payload.get("email"),
            "name": payload.get("name"),
            "role": payload.get("role", "user")
        }
    except jwt.ExpiredSignatureError:
        return "<h3>Error: The session token has expired. Please try signing in again.</h3>"
    except jwt.InvalidTokenError as e:
        return f"<h3>Error: Invalid token signature or claim match. {str(e)}</h3>"

    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


if __name__ == '__main__':
    print(f"🚀 Starting Bifrost Sandbox App on http://localhost:5001")
    app.run(port=5001, debug=True)

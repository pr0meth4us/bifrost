# Bifrost

Bifrost is a unified Identity, Authentication, and Payment Gateway system designed to bridge web applications and Telegram bots seamlessly. It acts as a central hub (like the mythical Bifrost bridge) that handles user accounts, subscriptions, webhooks, and secure payment processing.

## Features

- **Unified Identity Management:** Links users across multiple channels (Email, Passwords, and Telegram).
- **Authentication Gateway:** Provides endpoints for OTP-based email login, traditional credentials, and Telegram-based login/linking.
- **Payment Processing Hub:** Securely handles subscription payments with integrations for **Gumroad** (International) and **ABA PayWay** (Local/Cambodia).
- **Service Authentication:** Built-in middleware for securing internal API routes via Client ID & Secret for inter-service communication (e.g., between the web service and the Telegram bot).
- **Dynamic CORS Middleware:** Securely limits cross-origin requests by dynamically caching and verifying registered client application URLs.
- **Backoffice & Admin Dashboards:** Dedicated UI for managing users, applications, secrets, and manual role overrides.

## Documentation

Full documentation index: **[docs/README.md](docs/README.md)**. The API reference
and integration guide are served live at `/docs`; the landing page is `/`.

| | |
|---|---|
| Architecture | [docs/reference/WHITE_PAPER.md](docs/reference/WHITE_PAPER.md) |
| Integrating an app | [docs/guides/client_adoption.md](docs/guides/client_adoption.md) |
| Running the console | [docs/guides/console-onboarding.md](docs/guides/console-onboarding.md) |
| Working on Bifrost | [docs/guides/dev_guide.md](docs/guides/dev_guide.md) |
| Terms, privacy, DPA | [docs/legal/](docs/legal/README.md) |
| Release history | [CHANGELOG.md](CHANGELOG.md) |

## Project Structure

- **`bifrost/`**: The core Flask application containing APIs for Authentication (`auth/`), Internal Services (`internal/`), Backoffice (`backoffice/`), and Models/Services.
- **`bifrost/bot/`**: A fully-featured Telegram Bot built with `python-telegram-bot` that interacts with the Bifrost internal API.
- **`web_service/`**: A frontend/proxy service interacting with Bifrost.
- **`config.py`**: Centralized configuration management using environment variables.

## Tech Stack

- **Backend Framework:** Python 3 with Flask & Flask-REST extensions
- **Database:** MongoDB (via Flask-PyMongo)
- **Security:** PyJWT for token generation/validation, Werkzeug for password hashing
- **Containerization:** Docker & Docker Compose
- **Bot Framework:** `python-telegram-bot`

## Setup & Installation

### Option 1: Docker Compose (Recommended)

1. Clone the repository.
2. Create a `.env` file in the root directory based on the configuration keys in `config.py` (see *Environment Variables* below).
3. Build and start the services:
   ```bash
   docker-compose up -d --build
   ```
   This will spin up the `bifrost` core API (port 5000), the `finance-bot-web` frontend (port 8000), and the `bifrost_bot` container.

### Option 2: Local Virtual Environment

1. Create a Python virtual environment and activate it:
   ```bash
   python -m venv venv
   source venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set up your `.env` file.
4. Run the Flask application:
   ```bash
   export FLASK_APP=run.py
   flask run --host=0.0.0.0 --port=5000
   ```

## Environment Variables

Your `.env` file must include the following critical configurations:

```env
# Security
SECRET_KEY=your_flask_secret_key
JWT_SECRET_KEY=your_jwt_secret

# Database
MONGO_URI=mongodb+srv://...
DB_NAME=bifrost_db

# Email Services (For OTP and password resets)
SENDER_EMAIL=bifrostbyhelm@gmail.com
EMAIL_PASSWORD=your_app_password
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587

# Bifrost Public Configuration
BIFROST_PUBLIC_URL=http://localhost:5000

# Telegram Bot configuration
BIFROST_BOT_TOKEN=your_telegram_bot_token
ADMIN_CHAT_ID=your_admin_chat_id
BIFROST_ROOT_CLIENT_ID=client_id_for_bot_auth
BIFROST_ROOT_CLIENT_SECRET=secret_for_bot_auth

# ABA PayWay (Optional/Local)
PAYWAY_API_URL=https://checkout-sandbox.payway.com.kh/api/payment-gateway/v1/payments/purchase
PAYWAY_MERCHANT_ID=your_merchant_id
PAYWAY_API_KEY=your_api_key

# Gumroad (Optional/International)
GUMROAD_PRODUCT_PERMALINK=your_permalink
```

## Contributing

See [CHANGELOG.md](CHANGELOG.md) for recent updates and bug fixes.

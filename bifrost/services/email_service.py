# bifrost/services/email_service.py
import smtplib
import os
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import current_app

from ..utils.urls import public_url

log = logging.getLogger(__name__)


def get_default_logo_url():
    base_url = public_url()
    return f"{base_url}/static/logo.png" if base_url else ""


def load_email_template(filename='verification_email.html'):
    template_path = os.path.join(current_app.root_path, 'templates', filename)
    try:
        with open(template_path, 'r', encoding='utf-8') as file:
            return file.read()
    except FileNotFoundError:
        return "<html><body><h1>{TITLE}</h1><p>{SUBTITLE}</p><p>Code: {OTP_CODE}</p></body></html>"


def resolve_smtp(app_doc):
    """The mailbox this app sends from: its own if it brought one, else ours.

    A tenant with its own server sends under its own address — that is the whole
    reason to bring one, and the platform mailbox cannot pass SPF/DMARC for
    somebody else's domain anyway. Partial config falls back rather than mixing
    the two: a tenant host with the platform password just fails auth, and the
    platform host with a tenant From address fails alignment at the recipient.
    """
    from ..utils.encryption import decrypt_value

    app_doc = app_doc or {}
    host = app_doc.get('smtp_host')
    sender = app_doc.get('smtp_sender')
    # Encrypted under the app's own webhook_secret, exactly like db_connection.
    password = decrypt_value(app_doc.get('smtp_password'), app_doc.get('webhook_secret', ''))

    if host and sender and password:
        return {
            "host": host,
            "port": int((app_doc.get('smtp_port') or 587)),
            "sender": sender,
            "password": password,
            "from_name": app_doc.get('smtp_sender_name') or app_doc.get('app_name'),
        }

    return {
        "host": current_app.config['SMTP_SERVER'],
        "port": current_app.config['SMTP_PORT'],
        "sender": current_app.config['SENDER_EMAIL'],
        "password": current_app.config['EMAIL_PASSWORD'],
        "from_name": None,
    }


def send_email(to_email, subject, html_content, text_content, app_name, app_doc=None):
    smtp = resolve_smtp(app_doc)
    sender_email = smtp['sender']
    app_password = smtp['password']
    smtp_server = smtp['host']
    smtp_port = smtp['port']

    message = MIMEMultipart("alternative")
    message["From"] = f"{smtp['from_name'] or app_name} <{sender_email}>"
    message["To"] = to_email
    message["Subject"] = subject
    message.attach(MIMEText(text_content, "plain"))
    message.attach(MIMEText(html_content, "html"))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, app_password)
        server.sendmail(sender_email, to_email, message.as_string())
        server.quit()
        return True
    except Exception as e:
        log.error(f"Email failed: {e}")
        return False


def send_otp_email(to_email, otp, app_name="Bifrost Identity", logo_url=None, app_url="#", app_doc=None):
    """Sends a standard OTP verification email."""
    html_template = load_email_template('verification_email.html')
    final_logo = logo_url if logo_url else get_default_logo_url()

    html_content = html_template.replace("{OTP_CODE}", str(otp)) \
        .replace("{APP_NAME}", app_name) \
        .replace("{LOGO_URL}", final_logo) \
        .replace("{APP_URL}", app_url) \
        .replace("{TITLE}", "Verification Code") \
        .replace("{SUBTITLE}", f"Use this code to verify your account for <b>{app_name}</b>.")

    text_content = f"Your {app_name} code is: {otp}"
    return send_email(to_email, f"🔐 {app_name} Code", html_content, text_content, app_name, app_doc)


def send_invite_email(to_email, otp, app_name, verification_id, client_id, logo_url=None, app_doc=None):
    """
    Sends an invitation email with a direct link to the password setup page.
    The link includes the verification_id so the user can enter their OTP and set a password.
    """
    html_template = load_email_template('verification_email.html')
    final_logo = logo_url if logo_url else get_default_logo_url()

    # Build the complete URL to the set-password page
    base_url = public_url()
    setup_url = f"{base_url}/auth/ui/set-password?verification_id={verification_id}&client_id={client_id}"

    html_content = html_template.replace("{OTP_CODE}", str(otp)) \
        .replace("{APP_NAME}", app_name) \
        .replace("{LOGO_URL}", final_logo) \
        .replace("{APP_URL}", setup_url) \
        .replace("{TITLE}", "You've been invited!") \
        .replace("{SUBTITLE}",
                 f"You have been granted access to <b>{app_name}</b>. Click below to activate your account.")

    text_content = f"You've been invited to {app_name}! Your activation code is: {otp}\nVisit: {setup_url}"
    return send_email(to_email, f"👋 Welcome to {app_name}", html_content, text_content, app_name, app_doc)


def send_reset_email(to_email, otp):
    """
    Sends a password reset OTP via SMTP.
    """
    app_name = "Bifrost Security"
    html_template = load_email_template('verification_email.html')
    final_logo = get_default_logo_url()

    # We point them to the backoffice login for context, though they need to use the OTP on the reset screen.
    base_url = public_url()
    login_url = f"{base_url}/backoffice/login"

    html_content = html_template.replace("{OTP_CODE}", str(otp)) \
        .replace("{APP_NAME}", app_name) \
        .replace("{LOGO_URL}", final_logo) \
        .replace("{APP_URL}", login_url) \
        .replace("{TITLE}", "Reset Password") \
        .replace("{SUBTITLE}", "A request was made to reset your Bifrost password. Use the code below.")

    text_content = f"Bifrost Password Reset Code: {otp}"

    return send_email(to_email, "⚠️ Password Reset Request", html_content, text_content, app_name)
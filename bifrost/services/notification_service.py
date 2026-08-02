# bifrost/services/notification_service.py
import requests
import logging

logger = logging.getLogger(__name__)

def send_telegram_notification(bot_token, chat_id, message, photo_url=None):
    """
    Sends a message or photo notification to a specified Telegram channel or group.
    """
    if not bot_token or not chat_id:
        logger.warning("Telegram notification skipped: Missing bot_token or chat_id.")
        return False
        
    try:
        if photo_url:
            url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
            payload = {
                "chat_id": chat_id,
                "photo": photo_url,
                "caption": message,
                "parse_mode": "HTML"
            }
        else:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            
        res = requests.post(url, json=payload, timeout=10)
        res.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")
        return False

def _send_email_notification(config, subject, message, app_doc=None):
    from .email_service import send_email
    to = config.get('email')
    if not to:
        logger.warning("Email notification skipped: no recipient configured.")
        return False
    text = message.replace('<b>', '').replace('</b>', '').replace('<code>', '').replace('</code>', '')
    return bool(send_email(to, subject, f"<pre>{message}</pre>", text, "Bifrost Console", app_doc))


def _send_webhook_notification(config, message, payload):
    url = config.get('url')
    if not url:
        logger.warning("Webhook notification skipped: no URL configured.")
        return False
    try:
        res = requests.post(url, json={"message": message, **(payload or {})}, timeout=10)
        res.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to POST notification webhook: {e}")
        return False


def dispatch_notification(app, message, photo_url=None, subject="Bifrost Console alert", payload=None):
    """Sends an operational alert over whichever channel the tenant configured.

    Channel is configuration, not code: telegram | email | webhook. Adding a fourth
    means adding a branch here, not touching any caller.
    """
    config = app.get('notification_configs') or {}
    channel = config.get('channel', 'telegram')

    if channel == 'telegram':
        return send_telegram_notification(
            config.get('bot_token'), config.get('chat_id'), message, photo_url=photo_url
        )
    if channel == 'email':
        return _send_email_notification(config, subject, message, app_doc=app)
    if channel == 'webhook':
        return _send_webhook_notification(config, message, payload)

    logger.warning(f"Unknown notification channel '{channel}' for app {app.get('app_name')}.")
    return False


def dispatch_sla_alert(app, payment_ref, customer_email, amount, receipt_url=None):
    """New-receipt alert. Preserves the existing Telegram bot flow."""
    app_name = app.get('app_name', 'Tenant App')
    message = (
        f"🚨 <b>[{app_name}] New Payment Uploaded</b>\n\n"
        f"• <b>Ref:</b> <code>{payment_ref}</code>\n"
        f"• <b>Customer:</b> {customer_email}\n"
        f"• <b>Amount:</b> ${amount} USD\n\n"
        f"Please verify this payment in the Bifrost Console."
    )
    # Receipts move to a private bucket (SOW 4.8), so signed URLs will not render in
    # Telegram. Only attach the image while the bucket is still public.
    return dispatch_notification(
        app, message, photo_url=receipt_url if app.get('receipts_public') else None,
        subject=f"[{app_name}] New payment uploaded",
        payload={"txn_ref": payment_ref, "email": customer_email, "amount": amount},
    )

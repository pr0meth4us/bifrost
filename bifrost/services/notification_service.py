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

def dispatch_sla_alert(app, payment_ref, customer_email, amount, receipt_url=None):
    """
    Dispatches a Telegram SLA notification to the tenant channel.
    """
    config = app.get('notification_configs', {})
    if not config or config.get('channel') != 'telegram':
        return False
        
    bot_token = config.get('bot_token')
    chat_id = config.get('chat_id')
    
    app_name = app.get('app_name', 'Tenant App')
    message = (
        f"🚨 <b>[{app_name}] New Payment Uploaded</b>\n\n"
        f"• <b>Ref:</b> <code>{payment_ref}</code>\n"
        f"• <b>Customer:</b> {customer_email}\n"
        f"• <b>Amount:</b> ${amount} USD\n\n"
        f"Please verify this payment in the Bifrost Console."
    )
    
    return send_telegram_notification(bot_token, chat_id, message, photo_url=receipt_url)

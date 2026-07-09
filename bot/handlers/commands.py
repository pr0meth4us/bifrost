# bot/handlers/commands.py
import logging
from pathlib import Path
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from ..services import get_transaction, get_app_by_id, get_app_details
from .payment import WAITING_PROOF

log = logging.getLogger(__name__)

# QR Code Path (Fallback)
BASE_DIR = Path(__file__).resolve().parents[1]  # Up one level to 'bot'
QR_IMAGE_PATH = BASE_DIR / "assets" / "qr.jpg"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles /start <payload> OR /pay <payload>.
    """
    args = context.args
    payload = args[0] if args else None

    if not payload:
        await update.message.reply_text(
            "👋 <b>Bifrost Payment Gateway</b>\n\n"
            "Please use the payment button provided in your app.\n"
            "Or use: <code>/pay [transaction_id]</code>",
            parse_mode='HTML'
        )
        return ConversationHandler.END

    ctx_data = {}
    custom_qr_url = None

    try:
        # Priority 1: Webhook Injected Tenant Identity
        injected_client_id = context.bot_data.get('client_id')

        # --- MODE 1: SECURE DATABASE LOOKUP (Enterprise) ---
        if payload.startswith("tx-"):
            tx = get_transaction(payload)

            if not tx:
                await update.message.reply_text("❌ Error: Invalid or expired Transaction ID.")
                return ConversationHandler.END

            if tx.get('status') == 'completed':
                await update.message.reply_text("✅ This transaction is already completed.")
                return ConversationHandler.END

            app_doc = get_app_by_id(tx['app_id'])
            
            if not app_doc:
                await update.message.reply_text("❌ Error: App associated with this transaction not found.")
                return ConversationHandler.END

            client_id = injected_client_id or app_doc.get('client_id', 'unknown')
            app_name = app_doc.get('app_name', 'Unknown App')
            custom_qr_url = app_doc.get('app_qr_url')

            ctx_data = {
                "client_id": client_id,
                "app_name": app_name,
                "amount": tx['amount'],
                "duration": tx.get('duration', '1m'),
                "target_role": tx.get('target_role', 'premium'),
                "ref_id": tx.get('client_ref_id', 'N/A'),
                "transaction_id": payload
            }

        # --- MODE 2: LEGACY PARAMETER PARSING ---
        else:
            clean_payload = payload.replace("|", "__").replace(" ", "__")
            parts = clean_payload.split("__")

            if len(parts) < 2:
                await update.message.reply_text("❌ Invalid format.")
                return ConversationHandler.END

            client_id = injected_client_id or parts[0]
            price = parts[1]
            duration = parts[2] if len(parts) > 2 else "1m"
            target_role = parts[3] if len(parts) > 3 else "premium_user"
            ref_id = parts[4] if len(parts) > 4 else "N/A"

            app_doc = get_app_details(client_id)
            app_name = app_doc.get('app_name', 'Unknown App') if app_doc else 'Unknown'
            if app_doc:
                custom_qr_url = app_doc.get('app_qr_url')

            ctx_data = {
                "client_id": client_id,
                "app_name": app_name,
                "amount": price,
                "duration": duration,
                "target_role": target_role,
                "ref_id": ref_id
            }

        # --- UI GENERATION ---
        context.user_data['payment_context'] = ctx_data

        dur_map = {'1m': '1 Month', '3m': '3 Months', '6m': '6 Months', '1y': '1 Year', 'lifetime': 'Lifetime'}
        duration_text = dur_map.get(ctx_data['duration'], ctx_data['duration'])

        msg = (
            f"💎 <b>Secure Payment via Bifrost</b>\n"
            f"────────────────\n"
            f"📱 <b>App:</b> {ctx_data['app_name']}\n"
            f"🏷 <b>Plan:</b> {ctx_data['target_role'].replace('_', ' ').title()}\n"
            f"⏳ <b>Duration:</b> {duration_text}\n"
            f"💵 <b>Total:</b> ${ctx_data['amount']}\n"
            f"🧾 <b>Ref:</b> <code>{ctx_data['ref_id']}</code>\n"
            f"────────────────\n\n"
            "1. <b>Scan QR</b> code below.\n"
            "2. Make the transfer.\n"
            "3. <b>Send a Screenshot</b> of the receipt here."
        )

        # PRIORITY: Custom QR URL -> Local Asset -> Error
        if custom_qr_url:
            await update.message.reply_photo(photo=custom_qr_url, caption=msg, parse_mode='HTML')
        elif QR_IMAGE_PATH.exists():
            with open(QR_IMAGE_PATH, 'rb') as photo:
                await update.message.reply_photo(photo=photo, caption=msg, parse_mode='HTML')
        else:
            await update.message.reply_text(f"⚠️ [QR Missing]\n\n{msg}", parse_mode='HTML')

        return WAITING_PROOF

    except Exception as e:
        log.error(f"Handler error: {e}")
        await update.message.reply_text("❌ System Error.")
        return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Action cancelled.")
    return ConversationHandler.END
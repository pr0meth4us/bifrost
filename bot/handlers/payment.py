import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from ..config import Config

log = logging.getLogger(__name__)

WAITING_PROOF = 1


async def receive_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2: User sends photo -> Bot forwards to Admin Group"""
    user = update.effective_user

    if not update.message.photo:
        await update.message.reply_text("⚠️ Please send a <b>Photo</b> of the receipt.", parse_mode='HTML')
        return WAITING_PROOF

    # Retrieve Context
    pay_ctx = context.user_data.get('payment_context')

    # --- SAFETY CHECK ---
    # If the bot restarted before we fixed persistence, pay_ctx might be None.
    # Prevent sending "Unknown" / "$?" to admins.
    if not pay_ctx:
        await update.message.reply_text(
            "⚠️ <b>Session Expired</b>\n\n"
            "We lost track of your payment details (likely due to a system update).\n"
            "Please click the <b>Payment Link</b> or scan the QR code again to restart.",
            parse_mode='HTML'
        )
        return ConversationHandler.END
    # --------------------

    photo = update.message.photo[-1]

    target_app = pay_ctx.get('client_id', 'unknown')
    app_name = pay_ctx.get('app_name', 'Unknown App')
    amount = pay_ctx.get('amount', '?')

    if not Config.PAYMENT_GROUP_ID:
        await update.message.reply_text("⚠️ System Error: Admin Group not configured.")
        return ConversationHandler.END

    # 1. Resolve User from MongoDB
    from bot.database import get_db
    mongo_db = get_db()
    
    # Try finding account linked to Telegram
    account = mongo_db.accounts.find_one({"telegram_id": str(user.id)})
    if not account and pay_ctx.get('transaction_id'):
        tx = mongo_db.transactions.find_one({"transaction_id": pay_ctx['transaction_id']})
        if tx:
            account = mongo_db.accounts.find_one({"_id": tx['account_id']})

    email = account.get('email') if account else None
    
    # 2. Check if App has custom SQL DB Connection
    app_doc = mongo_db.applications.find_one({"client_id": target_app})
    db_conn = app_doc.get("db_connection") if app_doc else None
    
    postgres_payment_id = None
    postgres_user_id = None
    
    if db_conn:
        db_conn_str = db_conn.get("url") if isinstance(db_conn, dict) else str(db_conn)
        if not email:
            await update.message.reply_text(
                "⚠️ <b>Account Link Required</b>\n\n"
                "We could not resolve your email address associated with this Telegram account.\n"
                "Please make sure you have logged in to the app first before submitting payment.",
                parse_mode='HTML'
            )
            return ConversationHandler.END

        try:
            from bifrost.utils.tenant_db import get_tenant_db
            import secrets
            
            with get_tenant_db(db_conn_str) as conn:
                with conn.cursor() as cur:
                    # Resolve Postgres user ID by email
                    cur.execute("SELECT id FROM users WHERE email = %s LIMIT 1", [email])
                    row = cur.fetchone()
                    if not row:
                        await update.message.reply_text(
                            "⚠️ <b>Account Not Found</b>\n\n"
                            f"We could not find an account with email <code>{email}</code> on the application database.\n"
                            "Please make sure you register/login to the app first.",
                            parse_mode='HTML'
                        )
                        return ConversationHandler.END
                    postgres_user_id = row[0]
                    
                    # Fetch downloadable direct URL for Telegram Photo
                    file_obj = await context.bot.get_file(photo.file_id)
                    receipt_url = file_obj.file_path
                    
                    txn_ref = pay_ctx.get('transaction_id') or f"tg-{secrets.token_hex(4)}"
                    
                    # Insert payment in Postgres
                    cur.execute(
                        "INSERT INTO payments (user_id, amount, txn_ref, receipt_url, status) VALUES (%s, %s, %s, %s, 'pending') RETURNING id",
                        [postgres_user_id, float(amount), txn_ref, receipt_url]
                    )
                    postgres_payment_id = cur.fetchone()[0]
                    conn.commit()
        except Exception as e:
            log.error(f"Postgres receipt logging failed: {e}")
            await update.message.reply_text("⚠️ Database connection error. Please try again later.")
            return ConversationHandler.END

    await update.message.reply_text("✅ Receipt received! Verification in progress...")

    caption = (
        f"💰 <b>Payment Request</b>\n"
        f"User: {user.full_name} (Email: <code>{email or 'N/A'}</code>)\n"
        f"App: <b>{app_name}</b>\n"
        f"Amount: ${amount}\n"
        f"Action: Verify Screenshot below."
    )

    # If Postgres payment exists, map callback payload using postgres_payment_id
    if postgres_payment_id:
        callback_data = f"{postgres_user_id}|{target_app}|{postgres_payment_id}"
    else:
        callback_data = f"{user.id}|{target_app}"

    keyboard = [[
        InlineKeyboardButton("✅ Approve", callback_data=f"pay_approve_{callback_data}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"pay_reject_menu_{callback_data}")
    ]]

    try:
        await context.bot.send_photo(
            chat_id=Config.PAYMENT_GROUP_ID,
            photo=photo.file_id,
            caption=caption,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        log.error(f"Failed to forward to Admin Group: {e}")
        await update.message.reply_text("⚠️ Error contacting admin. Try again later.")
        return ConversationHandler.END

    return ConversationHandler.END
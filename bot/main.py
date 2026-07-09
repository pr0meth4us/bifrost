import sys
import os
import logging
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler

# --- PATH FIX: Add project root to sys.path ---
# This ensures we can resolve siblings if needed
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bot.config import Config
from bot.persistence import MongoPersistence
from bot.handlers import (
    start_command, receive_proof, cancel, WAITING_PROOF,
    admin_approve, admin_reject_menu, admin_reject_confirm, admin_restore_menu
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("bifrost-bot")

def create_bifrost_bot(bot_token=None):
    """Factory function to build the PTB Application."""
    token = bot_token or Config.BIFROST_BOT_TOKEN
    if not token or not Config.MONGO_URI:
        logger.critical("Missing Bot Token or MONGO_URI!")
        return None

    # 1. Setup Persistence
    persistence = MongoPersistence(mongo_uri=Config.MONGO_URI)

    # 2. Build App
    app = Application.builder().token(token).persistence(persistence).build()

    # 3. Register Handlers
    payment_conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start_command),
            CommandHandler("pay", start_command)
        ],
        states={WAITING_PROOF: [MessageHandler(filters.PHOTO, receive_proof)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
        allow_reentry=True,
        name="payment_flow",
        persistent=True
    )

    app.add_handler(payment_conv)
    app.add_handler(CallbackQueryHandler(admin_approve, pattern="^pay_approve_"))
    app.add_handler(CallbackQueryHandler(admin_reject_menu, pattern="^pay_reject_menu_"))
    app.add_handler(CallbackQueryHandler(admin_reject_confirm, pattern="^pay_reject_confirm_"))
    app.add_handler(CallbackQueryHandler(admin_restore_menu, pattern="^pay_restore_"))

    return app

_ptb_apps = {}  # Cache of bot_token -> Application

async def get_or_create_app(bot_token, client_id):
    if bot_token in _ptb_apps:
        return _ptb_apps[bot_token]

    app = create_bifrost_bot(bot_token=bot_token)
    if not app:
        return None

    # Inject client_id into bot data so handlers know which tenant context to use
    app.bot_data['client_id'] = client_id

    await app.initialize()
    await app.start()
    _ptb_apps[bot_token] = app
    return app

async def process_webhook_update(update_json, bot_token=None, client_id=None):
    """PRODUCTION ENTRY POINT (Flask)"""
    app = await get_or_create_app(bot_token, client_id)
    if not app:
        return

    try:
        update = Update.de_json(update_json, app.bot)
        await app.process_update(update)
    except Exception as e:
        logger.error(f"Error processing update: {e}")
def run_polling():
    """LOCAL DEV ENTRY POINT"""
    app = create_bifrost_bot()
    if not app:
        return

    logger.info("⚡ Starting Local Polling Mode... (Press Ctrl+C to stop)")
    app.run_polling(drop_pending_updates=True, close_loop=False)

if __name__ == "__main__":
    run_polling()
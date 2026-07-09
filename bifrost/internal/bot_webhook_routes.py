from flask import Blueprint, request, jsonify, current_app
import logging
import asyncio
from bson import ObjectId
import os

from .. import mongo
from bot.main import process_webhook_update

log = logging.getLogger(__name__)

bot_webhook_bp = Blueprint('bot_webhook', __name__, url_prefix='/api/v1/webhooks')

@bot_webhook_bp.route('/telegram/<client_id>', methods=['POST'])
def telegram_webhook_multitenant(client_id):
    """Receives webhook payloads from Telegram for a specific tenant bot."""
    # 1. Fetch tenant from DB
    app_doc = mongo.db.applications.find_one({"client_id": client_id})
    if not app_doc:
        log.error(f"Webhook received for unknown client_id: {client_id}")
        return jsonify({"error": "Unknown client"}), 404

    # 2. Get the bot token for this tenant
    bot_token = app_doc.get("telegram_bot_token")
    if not bot_token:
        log.error(f"Tenant {client_id} does not have a telegram_bot_token configured.")
        return jsonify({"error": "Bot not configured for this tenant"}), 400

    data = request.get_json(force=True)
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # We pass client_id along with the data and bot_token
        loop.run_until_complete(process_webhook_update(data, bot_token=bot_token, client_id=client_id))
        loop.close()
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        log.error(f"Bot Webhook Processing Error for {client_id}: {e}")
        return jsonify({"error": "Internal Error"}), 500

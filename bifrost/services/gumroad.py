import logging
import urllib.parse
from flask import current_app

log = logging.getLogger(__name__)


class GumroadService:
    def __init__(self, app_doc=None):
        # Per-tenant product, from that app's vault. No platform-wide default:
        # a shared permalink would sell one tenant's upgrade through another
        # tenant's Gumroad account.
        from ..utils.encryption import app_secret
        self.default_permalink = app_secret(app_doc, 'GUMROAD_PRODUCT_PERMALINK')
        self.base_url = current_app.config.get('GUMROAD_BASE_URL', "https://gumroad.com/l")

    def generate_checkout_url(self, transaction_id, email, product_permalink=None):
        """
        Generates a direct checkout link with pre-filled email and custom tracking ID.

        Args:
            transaction_id (str): The Bifrost internal transaction ID.
            email (str): The user's email.
            product_permalink (str, optional): The specific Gumroad product slug
                                               (e.g., 'savvify-premium').
                                               Defaults to config if None.
        """
        # Logic: Use specific product if sent, otherwise default
        target_product = product_permalink if product_permalink else self.default_permalink

        if not target_product:
            log.error("No Gumroad Product Permalink provided and no default found!")
            return None

        # Gumroad URL Parameters
        params = {
            'email': email,
            # We pass the internal transaction ID as a custom field named 'transaction_id'
            # Gumroad will return this in the webhook payload
            'transaction_id': transaction_id
        }

        query_string = urllib.parse.urlencode(params)
        return f"{self.base_url}/{target_product}?{query_string}"

    def verify_webhook(self, request_form):
        """
        Verifies that the webhook data is structurally valid.
        Gumroad sends data as application/x-www-form-urlencoded.

        Note: We relax strict product checking here because we support dynamic products.
        Security relies on validating the 'transaction_id' against our DB in the route.
        """
        if not request_form.get('sale_id'):
            return False

        return True
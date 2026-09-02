from flask import Blueprint

internal_bp = Blueprint('internal', __name__, url_prefix='/internal')

# Import routes to register them with the blueprint
from . import routes
from . import payment_routes
from . import cron_routes
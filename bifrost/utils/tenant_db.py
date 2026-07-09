# bifrost/utils/tenant_db.py
import psycopg2
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

@contextmanager
def get_tenant_db(connection_string):
    """
    Context manager for establishing a safe database connection to a tenant's PostgreSQL DB.
    Enforces automatic closing of connections and logging of connection issues.
    """
    conn = None
    try:
        conn = psycopg2.connect(connection_string)
        yield conn
    except Exception as e:
        logger.error(f"Tenant database connection failed: {e}")
        raise
    finally:
        if conn:
            try:
                conn.close()
            except Exception as close_err:
                logger.error(f"Failed to close tenant database connection: {close_err}")

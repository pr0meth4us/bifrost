# bifrost/utils/tenant_db.py
import threading
import psycopg2
from psycopg2 import pool as pg_pool
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

# One pool per tenant connection string. Opening a fresh connection per query is
# explicitly not acceptable (SOW §5) — Supabase sits behind a pooler and the TLS
# handshake dominates the latency budget for the 5s payment-action requirement.
_POOLS = {}
_POOLS_LOCK = threading.Lock()

MIN_CONNECTIONS = 1
MAX_CONNECTIONS = 8


def _get_pool(connection_string):
    with _POOLS_LOCK:
        existing = _POOLS.get(connection_string)
        if existing and not existing.closed:
            return existing
        created = pg_pool.ThreadedConnectionPool(
            MIN_CONNECTIONS, MAX_CONNECTIONS, connection_string,
            application_name="bifrost_console",
        )
        _POOLS[connection_string] = created
        return created


@contextmanager
def get_tenant_db(connection_string):
    """Yields a pooled connection to a tenant's PostgreSQL database.

    The connection is always returned to the pool. If the caller left an open
    transaction — an early return out of a `SELECT ... FOR UPDATE` block, for
    instance — it is rolled back first, so a half-finished approval can never leak
    into the next request that borrows the same connection.
    """
    pool = _get_pool(connection_string)
    conn = None
    try:
        conn = pool.getconn()
        yield conn
    except Exception as e:
        logger.error(f"Tenant database operation failed: {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if conn:
            try:
                if conn.status != psycopg2.extensions.STATUS_READY:
                    conn.rollback()
                pool.putconn(conn)
            except Exception as release_err:
                logger.error(f"Failed to return tenant connection to the pool: {release_err}")
                try:
                    pool.putconn(conn, close=True)
                except Exception:
                    pass


def close_all_pools():
    """Closes every tenant pool. For shutdown, and for tests."""
    with _POOLS_LOCK:
        for connection_string, p in list(_POOLS.items()):
            try:
                p.closeall()
            except Exception as e:
                logger.error(f"Error closing pool: {e}")
            _POOLS.pop(connection_string, None)

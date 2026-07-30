"""MongoDB backend for the tenant CMS.

The CMS was written straight against PostgreSQL — `information_schema` for
discovery, SQL strings for everything else. Tenants that run on Mongo (Savvify,
and Bifrost itself) had no way in.

This mirrors the same seven operations against Mongo and is selected purely by
the connection string, so `payments.py` dispatches on `handles(conn)` and every
route above it is unchanged.

Two things Mongo does not give us for free:

  * **No schema.** Collections are sampled and the field set is inferred. A field
    absent from some documents is reported nullable, which is the closest honest
    equivalent of a nullable column.
  * **No type coercion.** psycopg2 casts a form's `"42"` to an integer column;
    Mongo would happily store the string and quietly corrupt the collection. So
    values are coerced against the inferred type before any write.

Not supported: the payment queue and publish validation. Both are built on
`QueueSchema`'s SQL, and a Mongo tenant simply does not get those screens.
"""
import json
import logging
import threading
from datetime import date, datetime
from decimal import Decimal

from bson import ObjectId
from bson.errors import InvalidId

log = logging.getLogger(__name__)

# How many documents to look at when inferring a collection's shape. Enough to
# catch optional fields, small enough to stay instant on a large collection.
SAMPLE_SIZE = 200

_CLIENTS = {}
_CLIENTS_LOCK = threading.Lock()


def handles(conn_str):
    """Whether this connection string belongs to Mongo rather than Postgres."""
    return bool(conn_str) and str(conn_str).startswith(('mongodb://', 'mongodb+srv://'))


def _database(conn_str):
    """The tenant's database, from a pooled client.

    MongoClient pools internally, so one client per connection string is both
    necessary and sufficient — building one per request would open a new pool
    every time.
    """
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(conn_str)
        if client is None:
            from pymongo import MongoClient
            client = MongoClient(conn_str, appname="bifrost_console",
                                 serverSelectionTimeoutMS=8000)
            _CLIENTS[conn_str] = client

    from pymongo import uri_parser
    name = uri_parser.parse_uri(conn_str).get('database')
    if not name:
        raise ValueError(
            "Mongo connection string must name a database, "
            "e.g. mongodb+srv://user:pass@host/savvify"
        )
    return client[name]


def close_all_clients():
    """For shutdown and tests."""
    with _CLIENTS_LOCK:
        for conn_str, client in list(_CLIENTS.items()):
            try:
                client.close()
            except Exception as e:
                log.error(f"Error closing Mongo client: {e}")
            _CLIENTS.pop(conn_str, None)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def get_tenant_tables(conn_str):
    return sorted(name for name in _database(conn_str).list_collection_names()
                  if not name.startswith('system.'))


def _type_of(value):
    """Report a Python value as the Postgres type name the console already renders."""
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, int):
        return 'integer'
    if isinstance(value, (float, Decimal)):
        return 'double precision'
    if isinstance(value, datetime):
        return 'timestamp without time zone'
    if isinstance(value, date):
        return 'date'
    if isinstance(value, ObjectId):
        return 'objectid'
    if isinstance(value, (dict, list)):
        return 'jsonb'
    return 'text'


def get_tenant_table_schema(conn_str, table_name):
    """Infer a column list by sampling documents.

    Returns the same dict shape the Postgres path returns, so the grid, the
    drawer and the config screens need no idea which backend answered.
    """
    collection = _database(conn_str)[table_name]

    seen = {}
    order = []
    count = 0
    for doc in collection.find({}, limit=SAMPLE_SIZE):
        count += 1
        for key, value in doc.items():
            if key not in seen:
                seen[key] = {'types': set(), 'present': 0}
                order.append(key)
            seen[key]['present'] += 1
            if value is not None:
                seen[key]['types'].add(_type_of(value))

    columns = []
    for key in order:
        info = seen[key]
        types = info['types']
        # A field holding more than one type across documents has no honest
        # single answer; text renders anything and refuses to lie about it.
        data_type = types.pop() if len(types) == 1 else 'text'
        columns.append({
            'column_name': 'id' if key == '_id' else key,
            'data_type': data_type,
            'udt_name': data_type,
            # Missing from some documents, or explicitly null, is nullable.
            'is_nullable': 'NO' if (info['present'] == count and not types) else 'YES',
            'character_maximum_length': None,
            'numeric_precision': None,
            'foreign_table': None,
            'foreign_column': None,
        })

    # An empty collection still has an identity column, so the grid can render.
    if not columns:
        columns = [{'column_name': 'id', 'data_type': 'objectid', 'udt_name': 'objectid',
                    'is_nullable': 'NO', 'character_maximum_length': None,
                    'numeric_precision': None, 'foreign_table': None,
                    'foreign_column': None}]
    return columns


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _row_id(raw):
    """Parse a row id from the URL back into whatever _id actually is.

    Documents are usually keyed by ObjectId but need not be, so a string that
    is not a valid ObjectId is tried as an integer and then as itself.
    """
    if isinstance(raw, ObjectId):
        return raw
    text = str(raw)
    try:
        return ObjectId(text)
    except (InvalidId, TypeError):
        pass
    try:
        return int(text)
    except (TypeError, ValueError):
        return text


def _present(value):
    """Render a stored value the way the grid expects — JSON-safe, no BSON."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return value


def _present_doc(doc):
    if not doc:
        return doc
    out = {}
    for key, value in doc.items():
        out['id' if key == '_id' else key] = _present(value)
    return out


def get_tenant_table_data(conn_str, table_name, limit=50, offset=0,
                          sort_by='id', sort_dir='desc', search_query=None):
    collection = _database(conn_str)[table_name]
    schema = get_tenant_table_schema(conn_str, table_name)
    valid_columns = [c['column_name'] for c in schema]

    if sort_by not in valid_columns:
        sort_by = valid_columns[0] if valid_columns else 'id'
    sort_field = '_id' if sort_by == 'id' else sort_by
    direction = 1 if str(sort_dir).lower() == 'asc' else -1

    query = {}
    if search_query:
        text_fields = [c['column_name'] for c in schema if c['data_type'] == 'text']
        if text_fields:
            import re
            pattern = re.escape(str(search_query))
            query = {"$or": [
                {('_id' if f == 'id' else f): {"$regex": pattern, "$options": "i"}}
                for f in text_fields
            ]}

    total_count = collection.count_documents(query)
    cursor = collection.find(query).sort(sort_field, direction).skip(int(offset)).limit(int(limit))
    rows = [_present_doc(doc) for doc in cursor]

    # Identity first, matching how the Postgres grid leads with id.
    columns = ['id'] + [c for c in valid_columns if c != 'id']
    return columns, rows, total_count


def get_distinct_column_values(conn_str, table_name, column_name):
    field = '_id' if column_name == 'id' else column_name
    values = _database(conn_str)[table_name].distinct(field)
    return [_present(v) for v in values if v is not None][:50]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _coerce(value, data_type):
    """Cast a form string to the type the collection already uses.

    Without this a posted "42" lands in an integer field as the string "42", and
    the next aggregation over that collection is quietly wrong. On a financial
    collection that is the whole ballgame.
    """
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        return value

    try:
        if data_type == 'integer':
            return int(value)
        if data_type == 'double precision':
            return float(value)
        if data_type == 'boolean':
            return value.strip().lower() in ('true', 'on', '1', 'yes')
        if data_type in ('timestamp without time zone', 'date'):
            return datetime.fromisoformat(value)
        if data_type == 'jsonb':
            return json.loads(value)
        if data_type == 'objectid':
            return ObjectId(value)
    except (ValueError, TypeError, InvalidId):
        # An uncastable value is stored as given rather than silently dropped —
        # the operator sees what they typed and can fix it.
        log.warning("CMS(mongo): could not cast %r to %s; storing as text", value, data_type)
    return value


def _typed(conn_str, table_name, data):
    types = {c['column_name']: c['data_type']
             for c in get_tenant_table_schema(conn_str, table_name)}
    return {k: _coerce(v, types.get(k, 'text'))
            for k, v in data.items() if k != 'id'}


def update_row(conn_str, table_name, row_id, data):
    """Returns (before, after) for the audit log."""
    collection = _database(conn_str)[table_name]
    key = {"_id": _row_id(row_id)}

    before = collection.find_one(key)
    if before is None:
        raise ValueError(f"No document with id {row_id} in {table_name}")

    updates = _typed(conn_str, table_name, data)
    if updates:
        collection.update_one(key, {"$set": updates})
    after = collection.find_one(key)
    return _present_doc(before), _present_doc(after)


def insert_row(conn_str, table_name, data):
    """Returns the created document for the audit log."""
    collection = _database(conn_str)[table_name]
    doc = _typed(conn_str, table_name, data)
    result = collection.insert_one(doc)
    return _present_doc(collection.find_one({"_id": result.inserted_id}))


def delete_row(conn_str, table_name, row_id):
    """Returns the deleted document for the audit log."""
    collection = _database(conn_str)[table_name]
    key = {"_id": _row_id(row_id)}
    before = collection.find_one(key)
    if before is None:
        raise ValueError(f"No document with id {row_id} in {table_name}")
    collection.delete_one(key)
    return _present_doc(before)

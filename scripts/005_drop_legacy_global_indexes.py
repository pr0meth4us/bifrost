"""Drop the legacy platform-wide unique indexes on accounts.

`accounts` carries two generations of index:

    email_1                 unique across the WHOLE platform   (legacy)
    client_id_1_email_1     unique within one directory        (current)

…and the same pair for username, telegram_id, google_id, phone_number. The
legacy ones are strictly stronger, so they win, and multi-tenancy is defeated at
the database layer no matter what the application code does: two tenants can
never both have a user with the same email.

The visible symptom is a 500 on registration —

    E11000 duplicate key error ... index: email_1 dup key: {email: "..."}

— when a person who already exists in one tenant signs up for another.

`init_indexes()` creates the compound indexes but has never dropped these, so a
database that predates the compound ones keeps both. A freshly created database
never has them.

Safe to run when no two accounts share a (client_id, field) pair — the script
checks that first and refuses otherwise, because the compound indexes must be
able to accept everything already stored.

Reversible. To put one back:

    db.accounts.create_index([("email", 1)], unique=True, name="email_1")

Dry run by default:

    .venv/bin/python scripts/005_drop_legacy_global_indexes.py
    .venv/bin/python scripts/005_drop_legacy_global_indexes.py --apply
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

APPLY = '--apply' in sys.argv
FIELDS = ('email', 'username', 'telegram_id', 'google_id', 'phone_number')


def main():
    client = MongoClient(os.environ['MONGO_URI'])
    db = client[os.environ.get('DB_NAME', 'bifrost_db')]
    indexes = db.accounts.index_information()

    legacy = []
    for field in FIELDS:
        name = f"{field}_1"
        spec = indexes.get(name)
        if not spec:
            continue
        if not spec.get('unique'):
            continue
        # Only drop it when the per-tenant replacement is actually in place.
        if f"client_id_1_{field}_1" not in indexes:
            print(f"  SKIP {name}: compound client_id_1_{field}_1 does not exist yet")
            continue
        legacy.append((name, field))

    if not legacy:
        print("Nothing to do — no legacy global unique indexes present.")
        return 0

    # The compound indexes have to be able to hold every existing document.
    blocked = False
    for _name, field in legacy:
        pairs = Counter()
        for doc in db.accounts.find({field: {"$type": "string"}},
                                    {field: 1, "client_id": 1}):
            pairs[(doc.get('client_id'), doc[field])] += 1
        clashes = {k: n for k, n in pairs.items() if n > 1}
        if clashes:
            blocked = True
            print(f"  BLOCKED {field}: duplicate (client_id, {field}) pairs exist:")
            for (client_id, value), n in clashes.items():
                print(f"      {client_id} / {value} x{n}")

    if blocked:
        print("\nRefusing to drop anything. Resolve the duplicates above first — "
              "the per-tenant indexes cannot accept the data as it stands.")
        return 1

    print(f"{len(legacy)} legacy global unique index(es) to drop:\n")
    for name, field in legacy:
        print(f"  {name:<20} -> superseded by client_id_1_{field}_1")
        if APPLY:
            db.accounts.drop_index(name)

    if APPLY:
        print("\nDropped. Per-tenant uniqueness is now the only constraint.")
    else:
        print("\nDry run. Re-run with --apply to drop.")
    return 0


if __name__ == '__main__':
    sys.exit(main())

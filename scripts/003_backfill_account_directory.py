"""Backfill accounts.client_id — the account directory key.

Accounts created by the backoffice (owner invites, "add user to app") were
written with no client_id at all. Once lookups became directory-scoped those
accounts stopped being visible to the tenant's own login, so an invited user
could never redeem their invite.

This assigns each orphan the directory of the app it is linked to. Accounts
linked to apps in two different directories are ambiguous — the same human would
have to become two accounts — so they are reported and left alone.

Also backfills applications.tenant_id, which defaults to the app's own client_id.

Idempotent. Dry run by default:

    .venv/bin/python scripts/003_backfill_account_directory.py
    .venv/bin/python scripts/003_backfill_account_directory.py --apply
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

APPLY = '--apply' in sys.argv


def main():
    client = MongoClient(os.environ['MONGO_URI'])
    db = client[os.environ.get('DB_NAME', 'bifrost_db')]

    # 1. applications.tenant_id ------------------------------------------------
    missing_tenant = list(db.applications.find({"tenant_id": {"$exists": False}}))
    print(f"applications without tenant_id: {len(missing_tenant)}")
    if APPLY:
        for app in missing_tenant:
            db.applications.update_one({"_id": app['_id']},
                                       {"$set": {"tenant_id": app['client_id']}})

    directory_of_app = {}
    for app in db.applications.find({}, {"client_id": 1, "tenant_id": 1}):
        directory_of_app[app['_id']] = app.get('tenant_id') or app['client_id']

    # 2. accounts.client_id ----------------------------------------------------
    orphans = list(db.accounts.find({"client_id": {"$in": [None, ""]}}))
    print(f"accounts without a directory: {len(orphans)}")

    links = defaultdict(set)
    for link in db.app_links.find({}, {"account_id": 1, "app_id": 1}):
        # Some historic rows are missing one side of the join; they carry no
        # directory information, so they simply do not vote.
        directory = directory_of_app.get(link.get('app_id'))
        if directory and link.get('account_id'):
            links[link['account_id']].add(directory)

    assigned = ambiguous = unlinked = 0
    for account in orphans:
        directories = links.get(account['_id'], set())

        if len(directories) == 1:
            directory = directories.pop()
            assigned += 1
            if APPLY:
                db.accounts.update_one({"_id": account['_id']},
                                       {"$set": {"client_id": directory}})
            else:
                print(f"  assign {account.get('email') or account['_id']} -> {directory}")
        elif len(directories) > 1:
            ambiguous += 1
            print(f"  AMBIGUOUS {account.get('email') or account['_id']}: {sorted(directories)}")
        else:
            unlinked += 1
            print(f"  UNLINKED  {account.get('email') or account['_id']} (no app_links row)")

    print(f"\nassigned={assigned} ambiguous={ambiguous} unlinked={unlinked}")
    if ambiguous or unlinked:
        print("Ambiguous and unlinked accounts were left untouched — decide which "
              "directory each belongs to, or delete them.")
    if not APPLY:
        print("\nDry run. Re-run with --apply to write.")


if __name__ == '__main__':
    main()

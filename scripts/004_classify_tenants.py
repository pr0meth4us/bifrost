"""Classify every existing application as an internal or external tenant.

`tenant_type` decides how much of a tenant a platform admin can see. The code
treats a missing value as *external* — the narrow answer — so that forgetting to
classify a new tenant can never widen access.

That default is wrong for the apps that already exist: they are all
platform-owned today, and leaving them unclassified would lock the platform team
out of its own products. So this marks them internal explicitly, and every tenant
onboarded from here on starts external until someone says otherwise.

Idempotent — only writes apps with no tenant_type at all. Dry run by default:

    .venv/bin/python scripts/004_classify_tenants.py
    .venv/bin/python scripts/004_classify_tenants.py --apply
    .venv/bin/python scripts/004_classify_tenants.py --apply --external ministry_exam_prep
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--external', nargs='*', default=[],
                        metavar='CLIENT_ID',
                        help="client_ids to classify as external customers instead")
    args = parser.parse_args()

    client = MongoClient(os.environ['MONGO_URI'])
    db = client[os.environ.get('DB_NAME', 'bifrost_db')]

    external = set(args.external)
    unclassified = list(db.applications.find({"tenant_type": {"$exists": False}},
                                             {"client_id": 1, "app_name": 1}))

    unknown = external - {a['client_id'] for a in db.applications.find({}, {"client_id": 1})}
    if unknown:
        print(f"No such client_id: {', '.join(sorted(unknown))}")
        return 1

    print(f"{len(unclassified)} application(s) to classify\n")
    for app in unclassified:
        kind = 'external' if app['client_id'] in external else 'internal'
        print(f"  {app['client_id']:<42} -> {kind}")
        if args.apply:
            db.applications.update_one({"_id": app['_id']},
                                       {"$set": {"tenant_type": kind}})

    already = db.applications.count_documents({"tenant_type": {"$exists": True}})
    print(f"\nalready classified: {already}")
    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
        print("Pass --external <client_id> ... for any of these that is a customer.")
    return 0


if __name__ == '__main__':
    sys.exit(main())

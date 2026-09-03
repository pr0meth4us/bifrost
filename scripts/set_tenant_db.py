"""Set a tenant's Postgres connection string, encrypted, and prove it works.

Stored as `db_connection` on the application document, encrypted with that
app's webhook_secret — the same envelope the vault uses. The backoffice form
does this too; this exists so a connection string can be replaced without
clicking through the console.

Why this is needed at all: Supabase no longer provisions the direct
`db.<ref>.supabase.co` hostname on free projects. It has no A record and no
AAAA record — resuming a paused project does not bring it back, because it is
not a pause, it is a hostname that was retired. Anything still holding a
connection string built on that host fails DNS forever. The replacement is the
pooler:

    postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres

Take it from the Supabase dashboard under Connect. Prefer the session pooler
(port 5432) for a long-lived worker like the SLA sweep; the transaction pooler
(6543) does not support prepared statements, which psycopg uses by default.

  python scripts/set_tenant_db.py ministry_exam_prep
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bifrost import create_app, mongo
from bifrost.utils.encryption import encrypt_value
from config import Config


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("client_id")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the connection test before writing")
    args = ap.parse_args()

    app = create_app(Config)
    with app.app_context():
        db = mongo.cx[app.config['DB_NAME']]
        doc = db.applications.find_one({"client_id": args.client_id})
        if not doc:
            sys.exit(f"No application with client_id {args.client_id}")

        secret = doc.get("webhook_secret")
        if not secret:
            sys.exit("App has no webhook_secret; cannot encrypt the connection string.")

        print(f"App: {doc.get('app_name')} ({args.client_id})")
        print(f"Currently set: {bool(doc.get('db_connection'))}")
        print()

        conn = getpass.getpass("Postgres connection string (hidden): ").strip()
        if not conn:
            sys.exit("Empty, nothing written.")

        if ".supabase.co" in conn and conn.split("@")[-1].startswith("db."):
            print()
            print("WARNING: that is the direct db.<ref>.supabase.co host, which no")
            print("longer resolves on free projects. Use the pooler string instead.")
            if input("Write it anyway? [y/N] ").strip().lower() != "y":
                sys.exit("Aborted.")

        if not args.no_verify:
            print("Verifying...", end=" ", flush=True)
            try:
                import psycopg2
                c = psycopg2.connect(conn, connect_timeout=15)
                cur = c.cursor()
                cur.execute("select current_database(), count(*) from information_schema.tables")
                name, tables = cur.fetchone()
                c.close()
                print(f"connected to '{name}', {tables} tables visible")
            except Exception as exc:
                print("FAILED")
                print(f"  {exc}")
                print("  Nothing written. Fix the string, or pass --no-verify to force.")
                sys.exit(1)

        db.applications.update_one(
            {"_id": doc["_id"]},
            {"$set": {"db_connection": encrypt_value(conn, secret)}},
        )
        print()
        print("Written and encrypted.")
        print("The SLA sweep picks it up on its next run (every 15 minutes), or:")
        print("  gcloud scheduler jobs run bifrost-payment-sla \\")
        print("    --project bifrost-prod-2026 --location asia-southeast1")


if __name__ == "__main__":
    main()

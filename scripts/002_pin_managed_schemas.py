#!/usr/bin/env python3
"""Migration for per-tenant schemas in the managed Postgres database.

Before: every `managed` app shared `public` in one database. After (see
get_tenant_db_conn_str), each app's connection is pinned to its own schema — so
an app with no `db_schema` set would suddenly look empty.

Two modes, because "keep working" and "actually isolate" are different jobs:

  pin        Set db_schema='public' on every managed app that has none. Restores
             exactly today's behaviour, moves no data. Safe, and the right thing
             to run before deploying.

  separate   Move one tenant's named tables out of public into its own schema and
             point the app at it. Tables must be named explicitly: `public` is
             shared, so nothing can infer which rows belong to whom.

    ./scripts/002_pin_managed_schemas.py pin
    ./scripts/002_pin_managed_schemas.py pin --apply
    ./scripts/002_pin_managed_schemas.py separate prolong --tables questions,choices --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from psycopg2 import sql
from pymongo import MongoClient

from bifrost.backoffice.tenant_routes import managed_schema_for
from config import Config

MANAGED = {"$or": [{"db_mode": "managed"}, {"db_connection": {"$in": [None, ""]}}]}


def apps_collection():
    if not Config.MONGO_URI:
        sys.exit("MONGO_URI is not set.")
    return MongoClient(Config.MONGO_URI, tlsAllowInvalidCertificates=True)[Config.DB_NAME].applications


def pin(apply):
    apps = apps_collection()
    targets = [a for a in apps.find({**MANAGED, "db_schema": {"$in": [None, ""]}})]
    if not targets:
        print("Nothing to pin — every managed app already has a db_schema.")
        return
    for a in targets:
        print(f"  {a.get('client_id')}: db_schema -> 'public'")
    if not apply:
        print(f"\nDry run. {len(targets)} app(s) would be pinned. Re-run with --apply.")
        return
    res = apps.update_many(
        {"_id": {"$in": [a["_id"] for a in targets]}}, {"$set": {"db_schema": "public"}}
    )
    print(f"\nPinned {res.modified_count} app(s) to 'public'.")


def separate(client_id, tables, apply):
    apps = apps_collection()
    app = apps.find_one({"client_id": client_id})
    if not app:
        sys.exit(f"No application with client_id '{client_id}'.")
    if not Config.MANAGED_POSTGRES_URL:
        sys.exit("MANAGED_POSTGRES_URL is not set.")

    schema = managed_schema_for({**app, "db_schema": None})
    print(f"{client_id}: move {', '.join(tables)} from public -> {schema}")
    if not apply:
        print("\nDry run. Re-run with --apply.")
        return

    # One transaction: either the tenant's tables all move and the app points at the
    # new schema, or nothing changes. A half-moved tenant is a broken tenant.
    conn = psycopg2.connect(Config.MANAGED_POSTGRES_URL)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
            for t in tables:
                cur.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name=%s", [t]
                )
                if not cur.fetchone():
                    raise SystemExit(f"public.{t} does not exist — aborting, nothing moved.")
                cur.execute(
                    sql.SQL("ALTER TABLE public.{} SET SCHEMA {}").format(
                        sql.Identifier(t), sql.Identifier(schema)
                    )
                )
    finally:
        conn.close()

    apps.update_one({"_id": app["_id"]}, {"$set": {"db_schema": schema, "db_mode": "managed"}})
    print(f"Moved {len(tables)} table(s); {client_id} now reads {schema}.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_pin = sub.add_parser("pin", help="keep managed apps on public (no data moves)")
    p_pin.add_argument("--apply", action="store_true")

    p_sep = sub.add_parser("separate", help="move one tenant's tables into its own schema")
    p_sep.add_argument("client_id")
    p_sep.add_argument("--tables", required=True, help="comma-separated table names")
    p_sep.add_argument("--apply", action="store_true")

    args = p.parse_args()
    if args.cmd == "pin":
        pin(args.apply)
    else:
        separate(args.client_id, [t.strip() for t in args.tables.split(",") if t.strip()], args.apply)


if __name__ == "__main__":
    main()

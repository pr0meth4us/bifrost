"""Rotate an application's Bifrost credentials.

The two secrets are NOT the same kind of thing:

  client_secret   Stored as a password hash. Nothing depends on the old value,
                  so rotating it is a straight replacement.

  webhook_secret  Also the vault's encryption key — utils/encryption.get_fernet
                  derives the Fernet key from it. Replacing it without
                  re-encrypting orphans every value in api_keys, and because
                  decrypt_value swallows failures and returns the ciphertext,
                  nothing raises: the app just receives an encrypted blob where
                  its MONGO_URI should be. So this script decrypts with the old
                  secret and re-encrypts with the new one, in that order.

Dry run by default. On --apply, writes a JSON backup of api_keys first.

  python scripts/rotate_app_secrets.py edcore_1d1887d2
  python scripts/rotate_app_secrets.py edcore_1d1887d2 --apply
  python scripts/rotate_app_secrets.py edcore_1d1887d2 --apply --only client
"""
import argparse
import json
import os
import secrets as pysecrets
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash

from bifrost import create_app, mongo
from bifrost.utils.encryption import decrypt_value, encrypt_value
from config import Config

# Fernet tokens are urlsafe-base64 of a payload whose first byte is 0x80, which
# always renders as this prefix. It is how we tell a decrypt that worked from
# decrypt_value's silent passthrough on failure.
FERNET_PREFIX = "gAAAAA"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("client_id")
    ap.add_argument("--apply", action="store_true",
                    help="actually write. Without it, nothing is modified.")
    ap.add_argument("--only", choices=["client", "webhook", "both"], default="both")
    args = ap.parse_args()

    app = create_app(Config)
    with app.app_context():
        db = mongo.cx[app.config['DB_NAME']]
        doc = db.applications.find_one({"client_id": args.client_id})
        if not doc:
            sys.exit(f"No application with client_id {args.client_id}")

        old_webhook = doc.get("webhook_secret") or ""
        api_keys = doc.get("api_keys") or {}
        print(f"App:        {doc.get('app_name')} ({args.client_id})")
        print(f"Vault keys: {len(api_keys)}")
        print(f"Mode:       {'APPLY' if args.apply else 'DRY RUN'}  (--only {args.only})")
        print()

        backup = None
        # Only on --apply: a dry run must not leave a file containing the
        # current encryption key lying around.
        if api_keys and args.apply:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = f"vault-backup-{args.client_id}-{stamp}.json"
            with open(backup, "w", encoding="utf-8") as fh:
                json.dump({"client_id": args.client_id,
                           "webhook_secret": old_webhook,
                           "api_keys": api_keys}, fh, indent=2)
            os.chmod(backup, 0o600)
            print(f"Backup:     {backup}  (contains the OLD key — delete when done)")
            print()

        updates = {}
        new_client_secret = None
        new_webhook = None

        if args.only in ("client", "both"):
            new_client_secret = pysecrets.token_urlsafe(32)
            updates["client_secret_hash"] = generate_password_hash(new_client_secret)
            print("client_secret: will rotate")

        if args.only in ("webhook", "both"):
            if not old_webhook:
                sys.exit("App has no webhook_secret; cannot re-encrypt the vault.")

            new_webhook = pysecrets.token_urlsafe(32)
            reencrypted = {}
            undecryptable = []

            for name, stored in api_keys.items():
                if not stored:
                    continue
                plain = decrypt_value(stored, old_webhook)
                # decrypt_value returns its input on failure. If what came back
                # still looks like a Fernet token, we did not actually decrypt
                # it — re-encrypting that would double-wrap a value we cannot
                # read, permanently.
                if plain == stored and str(stored).startswith(FERNET_PREFIX):
                    undecryptable.append(name)
                    continue
                reencrypted[name] = encrypt_value(plain, new_webhook)

            if undecryptable:
                print()
                print("REFUSING TO PROCEED. These vault keys cannot be decrypted with")
                print("the current webhook_secret, so re-encrypting would double-wrap")
                print("them and lose the plaintext for good:")
                for name in undecryptable:
                    print(f"  - {name}")
                print()
                print("They were probably written under an earlier secret. Re-upload")
                print("them via /api/v1/config/bulk-upload, then run this again.")
                sys.exit(1)

            updates["webhook_secret"] = new_webhook
            for name, value in reencrypted.items():
                updates[f"api_keys.{name}"] = value
            print(f"webhook_secret: will rotate, re-encrypting {len(reencrypted)} vault keys")

        if not args.apply:
            print()
            print("Dry run — nothing written. Re-run with --apply.")
            return

        db.applications.update_one({"_id": doc["_id"]}, {"$set": updates})

        print()
        print("Done. Record these now; they are not shown again:")
        if new_client_secret:
            print(f"  BIFROST_CLIENT_SECRET={new_client_secret}")
        if new_webhook:
            print(f"  BIFROST_WEBHOOK_SECRET={new_webhook}")
        print()
        print("The app cannot authenticate until its .env carries these and it is")
        print("redeployed. For EDCORE:")
        print("  1. update EDCORE/backend/.env")
        print("  2. cd ~/code/EDCORE && ./scripts/deploy_cloudrun.sh")
        if backup:
            print(f"  3. shred the backup once verified: rm {backup}")


if __name__ == "__main__":
    main()

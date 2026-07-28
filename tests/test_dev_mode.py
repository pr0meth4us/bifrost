"""The only thing worth testing here: the bypass cannot attach to a remote DB."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bifrost.dev_mode import is_local_mongo


def test_interlock():
    for uri in ["mongodb://localhost:27017",
                "mongodb://127.0.0.1:27017/bifrost_dev",
                "mongodb://user:pw@localhost:27017/db"]:
        assert is_local_mongo(uri), uri

    for uri in [
        # The shape of the real production URI. If this ever passes, dev mode
        # opens the prod console.
        "mongodb+srv://u:p@cluster.example.mongodb.net/bifrost_db",
        # srv:// stays rejected even when it *looks* local — DNS decides where
        # it actually lands, not the string.
        "mongodb+srv://localhost/db",
        "mongodb://prod.example.com:27017",
        # One remote node hiding in a replica-set list must sink the whole URI.
        "mongodb://localhost:27017,evil.example.com:27017",
        "", None,
    ]:
        assert not is_local_mongo(uri), uri


if __name__ == "__main__":
    test_interlock()
    print("ok: dev bypass refuses every non-loopback URI")

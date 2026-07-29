"""Tenant intake: a request provisions nothing until approved, and only once.

The two things that would hurt: a public form that creates real applications, and
a double-clicked Approve that registers the same tenant twice.

Run: .venv/bin/python tests/test_tenant_intake.py
"""
import sys, types
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])

from bson import ObjectId

from bifrost.models import BifrostDB


class FakeCollection:
    """Minimal Mongo stand-in: enough for insert/find/update_one with a filter."""

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        doc.setdefault("_id", ObjectId())
        self.docs.append(doc)
        return types.SimpleNamespace(inserted_id=doc["_id"])

    def _match(self, doc, query):
        return all(doc.get(k) == v for k, v in query.items())

    def find(self, query=None):
        hits = [d for d in self.docs if self._match(d, query or {})]
        return types.SimpleNamespace(sort=lambda *a, **k: hits)

    def find_one(self, query):
        return next((d for d in self.docs if self._match(d, query)), None)

    def update_one(self, query, update):
        doc = self.find_one(query)
        if not doc:
            return types.SimpleNamespace(modified_count=0)
        doc.update(update.get("$set", {}))
        return types.SimpleNamespace(modified_count=1)

    def count_documents(self, query):
        return len([d for d in self.docs if self._match(d, query)])


def make_db():
    db = BifrostDB.__new__(BifrostDB)          # no Mongo connection needed
    db.db = types.SimpleNamespace(tenant_requests=FakeCollection())
    return db


FORM = {
    "app_name": "  Ministry Exam Prep  ",
    "admin_email": "owner@example.com",
    "web_url": "https://example.com",
    "payments_enabled": "on",
    "pay_manual": "on",
    "notes": "150 questions, launching Q3.",
    # Anything not in REQUEST_FIELDS must not survive the trip.
    "payway_api_key": "should-never-be-stored",
}


def test_request_is_inert_and_trimmed():
    db = make_db()
    rid = db.create_tenant_request(FORM)
    req = db.get_tenant_request(rid)

    assert req["status"] == "pending"
    assert req["app_name"] == "Ministry Exam Prep", "values are trimmed"
    assert req["client_id"] is None, "no application exists yet"
    assert "payway_api_key" not in req, "a public form must not store credentials"
    assert db.db.tenant_requests.count_documents({"status": "pending"}) == 1


def test_approve_is_idempotent():
    db = make_db()
    rid = db.create_tenant_request(FORM)

    assert db.decide_tenant_request(rid, "approved", "admin-1", client_id="prep_ab12")
    # Second click: the status guard rejects it, so the caller never provisions again.
    assert not db.decide_tenant_request(rid, "approved", "admin-1", client_id="prep_cd34")

    req = db.get_tenant_request(rid)
    assert req["status"] == "approved"
    assert req["client_id"] == "prep_ab12", "the first decision stands"
    assert req["decided_by"] == "admin-1"


def test_rejection_records_a_reason():
    db = make_db()
    rid = db.create_tenant_request(FORM)

    assert db.decide_tenant_request(rid, "rejected", "admin-1", reason="duplicate of #4")
    req = db.get_tenant_request(rid)
    assert req["status"] == "rejected" and req["decision_reason"] == "duplicate of #4"
    # A rejected request cannot later be approved without a fresh submission.
    assert not db.decide_tenant_request(rid, "approved", "admin-2")


def test_listing_filters_by_status():
    db = make_db()
    a, b = db.create_tenant_request(FORM), db.create_tenant_request(FORM)
    db.decide_tenant_request(a, "approved", "admin-1")

    assert len(db.list_tenant_requests()) == 2
    assert [r["_id"] for r in db.list_tenant_requests("pending")] == [ObjectId(b)]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn()
            print(f"  {name} ok")
    print("ok")

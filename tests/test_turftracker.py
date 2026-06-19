"""TurfTracker backend API tests"""
import os
import pytest
import requests
from datetime import datetime, timezone

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://quick-billing-system.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---------------- Items ----------------
class TestItems:
    def test_list_items_has_seed(self, session):
        r = session.get(f"{API}/items")
        assert r.status_code == 200
        data = r.json()
        names = {d["name"]: d["price"] for d in data}
        for n in ["Winkin Strawberry", "Winkin Chocolate", "Winkin Vanilla"]:
            assert names.get(n) == 40, f"{n} expected 40, got {names.get(n)}"
        for n in ["Sting", "Campa", "Nimbu", "Water Bottle"]:
            assert names.get(n) == 20, f"{n} expected 20, got {names.get(n)}"
        # ensure _id never exposed
        for d in data:
            assert "_id" not in d
            assert "id" in d

    def test_create_update_delete_item(self, session):
        # create
        r = session.post(f"{API}/items", json={"name": "TEST_Item", "price": 99})
        assert r.status_code == 200
        item = r.json()
        assert item["name"] == "TEST_Item"
        assert item["price"] == 99
        assert "_id" not in item
        iid = item["id"]
        # update
        r = session.put(f"{API}/items/{iid}", json={"price": 55, "name": "TEST_Item2"})
        assert r.status_code == 200
        assert r.json()["price"] == 55
        assert r.json()["name"] == "TEST_Item2"
        # verify via GET
        r = session.get(f"{API}/items")
        assert any(d["id"] == iid and d["price"] == 55 for d in r.json())
        # delete
        r = session.delete(f"{API}/items/{iid}")
        assert r.status_code == 200
        # delete again -> 404
        r = session.delete(f"{API}/items/{iid}")
        assert r.status_code == 404

    def test_update_nonexistent(self, session):
        r = session.put(f"{API}/items/nonexistent-id", json={"price": 1})
        assert r.status_code == 404

    def test_update_no_fields(self, session):
        # get any item
        items = session.get(f"{API}/items").json()
        iid = items[0]["id"]
        r = session.put(f"{API}/items/{iid}", json={})
        assert r.status_code == 400


# ---------------- Transactions ----------------
class TestTransactions:
    created_ids = []

    def test_create_transaction_cash(self, session):
        items = session.get(f"{API}/items").json()
        strawberry = next(i for i in items if i["name"] == "Winkin Strawberry")
        sting = next(i for i in items if i["name"] == "Sting")
        payload = {
            "customer_name": "TEST_Alice",
            "items": [
                {"item_id": strawberry["id"], "name": strawberry["name"], "price": 40, "quantity": 2},
                {"item_id": sting["id"], "name": sting["name"], "price": 20, "quantity": 1},
            ],
            "payment_method": "cash",
        }
        r = session.post(f"{API}/transactions", json=payload)
        assert r.status_code == 200, r.text
        txn = r.json()
        assert txn["total"] == 100  # 2*40 + 1*20
        assert txn["payment_method"] == "cash"
        assert txn["customer_name"] == "TEST_Alice"
        assert "_id" not in txn
        TestTransactions.created_ids.append(txn["id"])

    def test_create_transaction_upi(self, session):
        items = session.get(f"{API}/items").json()
        water = next(i for i in items if i["name"] == "Water Bottle")
        payload = {
            "customer_name": "TEST_Bob",
            "items": [{"item_id": water["id"], "name": water["name"], "price": 20, "quantity": 3}],
            "payment_method": "upi",
        }
        r = session.post(f"{API}/transactions", json=payload)
        assert r.status_code == 200
        txn = r.json()
        assert txn["total"] == 60
        TestTransactions.created_ids.append(txn["id"])

    def test_create_empty_items_fails(self, session):
        r = session.post(f"{API}/transactions", json={
            "customer_name": "x", "items": [], "payment_method": "cash"
        })
        assert r.status_code == 400

    def test_invalid_payment_method(self, session):
        r = session.post(f"{API}/transactions", json={
            "customer_name": "x",
            "items": [{"item_id": "x", "name": "x", "price": 1, "quantity": 1}],
            "payment_method": "card",
        })
        assert r.status_code == 422

    def test_empty_customer_defaults_walkin(self, session):
        items = session.get(f"{API}/items").json()
        payload = {
            "customer_name": "   ",
            "items": [{"item_id": items[0]["id"], "name": items[0]["name"], "price": items[0]["price"], "quantity": 1}],
            "payment_method": "cash",
        }
        r = session.post(f"{API}/transactions", json=payload)
        assert r.status_code == 200
        assert r.json()["customer_name"] == "Walk-in"
        TestTransactions.created_ids.append(r.json()["id"])

    def test_list_transactions_sorted_desc(self, session):
        r = session.get(f"{API}/transactions")
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= len(TestTransactions.created_ids)
        # newest first
        timestamps = [d["created_at"] for d in data]
        assert timestamps == sorted(timestamps, reverse=True)
        for d in data:
            assert "_id" not in d

    def test_list_with_date_filter(self, session):
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        r = session.get(f"{API}/transactions", params={"start": today})
        assert r.status_code == 200
        # all returned should be today's
        for d in r.json():
            assert d["created_at"] >= today

    def test_delete_transaction(self, session):
        if not TestTransactions.created_ids:
            pytest.skip("no txn")
        tid = TestTransactions.created_ids.pop()
        r = session.delete(f"{API}/transactions/{tid}")
        assert r.status_code == 200
        r = session.delete(f"{API}/transactions/{tid}")
        assert r.status_code == 404


# ---------------- Analytics ----------------
class TestAnalytics:
    def test_summary_today(self, session):
        r = session.get(f"{API}/analytics/summary", params={"period": "today"})
        assert r.status_code == 200
        data = r.json()
        for k in ["period", "total_revenue", "total_transactions", "total_items_sold",
                  "cash_total", "upi_total", "buckets", "top_items"]:
            assert k in data, f"missing {k}"
        assert data["period"] == "today"
        assert len(data["buckets"]) == 1

    def test_summary_week(self, session):
        r = session.get(f"{API}/analytics/summary", params={"period": "week"})
        assert r.status_code == 200
        assert len(r.json()["buckets"]) == 7

    def test_summary_month(self, session):
        r = session.get(f"{API}/analytics/summary", params={"period": "month"})
        assert r.status_code == 200
        assert len(r.json()["buckets"]) == 30

    def test_summary_invalid_period(self, session):
        r = session.get(f"{API}/analytics/summary", params={"period": "year"})
        assert r.status_code == 422


# ---------------- Cleanup ----------------
def test_zz_cleanup(session):
    # cleanup remaining TEST_ items
    items = session.get(f"{API}/items").json()
    for it in items:
        if it["name"].startswith("TEST_"):
            session.delete(f"{API}/items/{it['id']}")
    # cleanup TEST_ transactions
    txns = session.get(f"{API}/transactions").json()
    for t in txns:
        if t["customer_name"].startswith("TEST_") or t["customer_name"] == "Walk-in":
            # only delete recently created ones tracked
            if t["id"] in TestTransactions.created_ids:
                session.delete(f"{API}/transactions/{t['id']}")
    for tid in TestTransactions.created_ids:
        session.delete(f"{API}/transactions/{tid}")

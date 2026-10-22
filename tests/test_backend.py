"""Offline test: fake Firestore + fake Claude, exercise /api/summary and /api/chat tool loop."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
# Personal fixtures (bank CSVs, course and workout files) live outside the public repo.
PRIVATE = _os.environ.get("LT_PRIVATE", _os.path.join(HERE, "..", "..", "lifetracker-private"))
def _need(path):
    if not _os.path.exists(path):
        print(f"SKIP: private fixture {_os.path.basename(path)} not found (set LT_PRIVATE)"); raise SystemExit(0)
    return path
import os, sys, types, json
sys.path.insert(0, _os.path.join(HERE, "..", "backend"))
os.environ.update(DEV_SKIP_AUTH="1", PLAID_CLIENT_ID="x", PLAID_SECRET="y", ANTHROPIC_API_KEY="test")

import firebase_admin
firebase_admin.initialize_app = lambda *a, **k: None
from google.cloud import firestore
firestore.Client = lambda *a, **k: object()

import main, assistant

FAKE = [
    {"id": "1", "date": "2026-10-02", "amount": 54.20, "merchant": "Trader Joe's", "category": "Food And Drink", "source": "Chase"},
    {"id": "2", "date": "2026-10-05", "amount": 15.99, "merchant": "Netflix", "category": "Entertainment", "source": "Chase"},
    {"id": "3", "date": "2026-10-07", "amount": -2500.0, "merchant": "Payroll", "category": "Income", "source": "Chase"},
    {"id": "4", "date": "2026-10-09", "amount": 30.0, "merchant": "Alex", "note": "pizza", "category": "Venmo", "source": "Venmo"},
    {"id": "5", "date": "2026-09-12", "amount": 400.0, "merchant": "Chase Card Payment", "category": "Loan Payments", "source": "Chase"},
]
main._load_transactions = lambda s, e: [t for t in FAKE if s <= t["date"] <= e]
main.load_holdings = lambda: {"latest": {"total": 1000, "holdings": []}, "history": []}
main.assistant_context = lambda: "Linked via Plaid: Chase (bank)."

c = main.app.test_client()
r = c.get("/api/summary?start=2026-09-01&end=2026-10-31")
print("summary", r.status_code, r.get_json())
assert r.get_json()["total_spent"] == 100.19, r.get_json()

# --- fake Claude: first asks for a tool, then answers using the tool result
calls = []
class Blk(types.SimpleNamespace): pass
class FakeMsgs:
    def create(self, **kw):
        calls.append(kw)
        if len(calls) == 1:
            return types.SimpleNamespace(stop_reason="tool_use", content=[
                Blk(type="text", text="Let me check."),
                Blk(type="tool_use", id="tu1", name="search_transactions", input={"text": "pizza"})])
        last = kw["messages"][-1]["content"][0]
        assert last["type"] == "tool_result" and "pizza" in last["content"], last
        return types.SimpleNamespace(stop_reason="end_turn", content=[Blk(type="text", text="You spent **$30.00** on pizza.")])
assistant._client = types.SimpleNamespace(messages=FakeMsgs())

r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "How much on pizza?"}]})
print("chat", r.status_code, r.get_json())
assert r.status_code == 200 and "pizza" in r.get_json()["reply"]
assert calls[0]["model"] == "claude-sonnet-5-5" and len(calls[0]["tools"]) == 3

# bad request -> JSON 400, not HTML
r = c.post("/api/chat", json={"messages": []}); print("empty chat", r.status_code, r.get_json())
assert r.status_code == 400

# unhandled error -> JSON 500
main._load_transactions = lambda s, e: 1 / 0
r = c.get("/api/summary"); print("error", r.status_code, r.get_json())
assert r.status_code == 500 and "ZeroDivisionError" in r.get_json()["error"]
print("ALL BACKEND TESTS PASSED")

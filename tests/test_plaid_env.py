"""Offline test of the Plaid environment handling in main.py (no network)."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
import time, types
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
os.environ.update(DEV_SKIP_AUTH="1", PLAID_CLIENT_ID="x", PLAID_SECRET="y", PLAID_ENV="production")

# ---- tiny in-memory Firestore ------------------------------------------------
DATA = {}
class Snap:
    def __init__(s, path): s.path = path; s.exists = path in DATA; s.id = path.split("/")[-1]
    def to_dict(s): return dict(DATA[s.path]) if s.exists else None
    def get(s, k): return DATA[s.path].get(k)
class DocRef:
    def __init__(s, path): s.path = path; s.id = path.split("/")[-1]
    def get(s): return Snap(s.path)
    def set(s, d, merge=False):
        d = {k: (time.time() if v == "SERVER_TS" else v) for k, v in d.items()}
        DATA[s.path] = {**DATA.get(s.path, {}), **d} if merge else d
    def update(s, d): s.set(d, merge=True)
    def delete(s): DATA.pop(s.path, None)
    def collection(s, n): return ColRef(f"{s.path}/{n}")
class Query:
    def __init__(s, path, filters=()): s.path, s.filters = path, filters
    def where(s, f, op, v): return Query(s.path, s.filters + ((f, op, v),))
    def stream(s):
        ops = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, "==": lambda a, b: a == b}
        for p in list(DATA):
            if p.rsplit("/", 1)[0] == s.path and all(ops[o](DATA[p].get(f), v) for f, o, v in s.filters):
                yield Snap(p)
class ColRef(Query):
    def __init__(s, path): super().__init__(path)
    def document(s, k=None): return DocRef(f"{s.path}/{k or os.urandom(6).hex()}")
class Batch:
    def __init__(s): s.ops = []
    def set(s, ref, d, merge=False): s.ops.append((ref, d, merge))
    def commit(s): [r.set(d, merge=m) for r, d, m in s.ops]; s.ops = []
class FakeDB:
    def collection(s, n): return ColRef(n)
    def batch(s): return Batch()

import firebase_admin
firebase_admin.initialize_app = lambda *a, **k: None
from google.cloud import firestore as gfs
gfs.Client = lambda *a, **k: FakeDB()
gfs.SERVER_TIMESTAMP = "SERVER_TS"
import main  # noqa: E402

c = main.app.test_client()
assert c.get("/health").json["plaid_env"] == "production"

seen = {}
def fake_link(req):
    seen["req"] = req.to_dict()
    return {"link_token": "link-x"}
main.plaid_client.link_token_create = fake_link
assert c.post("/api/plaid/link-token", json={"kind": "bank"}).json["link_token"] == "link-x"
assert seen["req"]["transactions"]["days_requested"] == 730, seen
c.post("/api/plaid/link-token", json={"kind": "investments"})
assert "transactions" not in seen["req"], "investments link should not ask for transactions history"

# An Item linked in sandbox is skipped in production instead of failing on Plaid.
DATA["users/dev/items/old"] = {"access_token": "access-sandbox-1", "kind": "bank"}
r = c.post("/api/sync").json["results"]
assert r == [{"item": "old", "skipped": "linked in Plaid sandbox"}], r

main.plaid_client.item_public_token_exchange = lambda req: {"item_id": "new", "access_token": "access-production-1"}
c.post("/api/plaid/exchange", json={"public_token": "p", "kind": "bank", "institution": "Bank"})
assert DATA["users/dev/items/new"]["env"] == "production"
print("plaid env: ok")

# A Plaid error without a JSON body still says what went wrong.
import plaid
def boom(req): raise plaid.ApiException(status=0, reason="SSLError\nbad handshake")
main.plaid_client.link_token_create = boom
r = c.post("/api/plaid/link-token", json={"kind": "bank"})
assert r.status_code == 502 and "status 0, SSLError bad handshake" in r.json["error"], r.json
print("plaid error text: ok")

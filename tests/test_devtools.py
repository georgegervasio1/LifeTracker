"""Offline test of devtools.py (Developer dashboard: usage counts, Claude counts, task list) and the developer-only
account. In-memory Firestore, made-up accounts only."""
import os as _os
HERE = _os.environ.get("LT_TESTS") or _os.path.dirname(_os.path.abspath(__file__))
exec(open(_os.path.join(HERE, "test_health.py")).read().split("# ---- fake WHOOP HTTP")[0])

import types as _types
import access, devtools, assistant
from flask import g
c = main.app.test_client()
os.environ.update(ALLOWED_EMAIL="owner@example.com", ADMIN_EMAILS="dev@example.com")

# ---- pings: a session start, then one minute per ping at most every 45 s; sections are known ids only
T = 1_800_000_000.0                       # a fixed instant; the day is counted in New York time
day = devtools.today(T)
with main.app.test_request_context("/api/usage/ping"):
    g.uid, g.role = "u1", "member"
    assert devtools._counted()
    assert devtools.ping("u1", "health", start=True, now=T) == {"minute": False, "session": True}
    assert devtools.ping("u1", "health", now=T + 60) == {"minute": True, "session": False}
    assert devtools.ping("u1", "health", now=T + 70) == {"minute": False, "session": False}     # a second tab
    assert devtools.ping("u1", "nonsense", now=T + 120)["minute"]                               # counted as home
    devtools.ping("u2", "goals", start=True, now=T)
    devtools.ping("u2", "goals", now=T + 60)
    # back after an hour: a new session
    assert devtools.ping("u1", "education", now=T + 3700) == {"minute": True, "session": True}
d = DATA[f"usage_days/{day}"]
assert d["minutes"] == 4 and d["sessions"] == 3 and d["users"] == {"u1": 3, "u2": 1}
assert d["sections"] == {"health": 1, "home": 1, "goals": 1, "education": 1}
assert DATA["signups/u1"]["minutes"] == 3 and DATA["signups/u1"]["sessions"] == 2 and DATA["signups/u1"]["days"] == 1

# the developer account, the demo and pending accounts are never counted
with main.app.test_request_context("/"):
    g.uid, g.role = "d1", "admin"
    assert not devtools._counted()
    g.role, g.demo_db = "member", object()
    assert not devtools._counted()

# ---- Claude calls: counted through assistant.client() by route, tokens only
class Inner:
    def create(self, **kw):
        return _types.SimpleNamespace(model=kw["model"], content=[],
                                      usage=_types.SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=5))
wrapped = assistant._Counted(_types.SimpleNamespace(messages=Inner(), other="x"))
assert wrapped.other == "x"
with main.app.test_request_context("/api/goals/coach", method="POST"):
    g.uid, g.role = "u1", "member"
    wrapped.messages.create(model="m-1", messages=[])
    wrapped.messages.create(model="m-1", messages=[])
with main.app.test_request_context("/api/edu/syllabi/upload", method="POST"):
    g.uid, g.role = "u2", "member"
    wrapped.messages.create(model="m-1", messages=[])
d = DATA[f"usage_days/{devtools.today()}"]
assert d["claude"]["Goals coach"] == {"calls": 2, "input": 210, "output": 40}
assert d["claude"]["Syllabus reader"]["calls"] == 1 and d["models"] == {"m-1": 3}
assert DATA["signups/u1"]["claude_calls"] == 2
assert devtools.feature_for("/api/chat") == "Finance chat" and devtools.feature_for("/api/x") == "Other"

# ---- summary over a window
s = devtools.summary(30, now=T + 4000)
assert len(s["series"]) == 30 and s["series"][-1]["date"] == day
assert s["totals"]["active_today"] == 2 and s["totals"]["minutes"] == 4 and s["totals"]["sessions"] == 3
assert s["sections"][0]["minutes"] == 1 and {r["id"] for r in s["sections"]} == {"health", "home", "goals", "education"}
assert devtools.summary(500)["days"] == 120

# ---- routes: owner and developer only
who = {"email": "owner@example.com"}
main.fb_auth.verify_id_token = lambda tok, *a, **k: {"uid": "own", "email": who["email"], "email_verified": True, "name": "O"}
H = {"Authorization": "Bearer t"}
r = c.get("/api/admin/usage?days=7", headers=H).get_json()
assert r["days"] == 7 and "totals" in r
who["email"] = "dev@example.com"
assert c.get("/api/admin/usage", headers=H).status_code == 200
who["email"] = "member@example.com"
assert c.get("/api/admin/usage", headers=H).status_code == 403
assert c.get("/api/admin/tasks", headers=H).status_code == 403
who["email"] = "dev@example.com"

# tasks: add, edit, done, delete, starters (once)
assert c.post("/api/admin/tasks", json={"title": "  "}, headers=H).status_code == 400
r = c.post("/api/admin/tasks", json={"title": "Dark mode for charts", "kind": "bug", "area": "nope", "priority": "high",
                                     "notes": "x" * 5000}, headers=H).get_json()
t = r["task"]
assert t["kind"] == "bug" and t["area"] == "general" and t["status"] == "todo" and len(t["notes"]) == 4000
r = c.post("/api/admin/tasks", json={"title": "Recipe import", "kind": "idea"}, headers=H).get_json()
assert r["task"]["status"] == "idea" and r["task"]["priority"] == "medium"
assert [x["title"] for x in r["tasks"]] == ["Dark mode for charts", "Recipe import"]       # high priority first
r = c.put(f"/api/admin/tasks/{t['id']}", json={"status": "done", "title": "Fix chart colors"}, headers=H).get_json()
row = next(x for x in r["tasks"] if x["id"] == t["id"])
assert row["status"] == "done" and row["title"] == "Fix chart colors" and row["done_at"] and row["kind"] == "bug"
assert c.put(f"/api/admin/tasks/{t['id']}", json={"title": ""}, headers=H).status_code == 400
assert c.put("/api/admin/tasks/missing", json={"title": "x"}, headers=H).status_code == 404
n = len(c.post("/api/admin/tasks/starters", headers=H).get_json()["tasks"])
assert n == 2 + len(devtools.STARTERS)
assert len(c.post("/api/admin/tasks/starters", headers=H).get_json()["tasks"]) == n
r = c.delete(f"/api/admin/tasks/{t['id']}", headers=H).get_json()
assert all(x["id"] != t["id"] for x in r["tasks"])

# users list carries services picked and usage counts, never section data
DATA["signups/u1"].update(email="a@example.com", name="A", last_seen=T)
DATA["users/u1/settings/services"] = {"picked": ["health", "goals"], "onboarded": True}
DATA["signups/u2"].update(email="b@example.com", name="B", last_seen=T - 10)
DATA["signups/d1"] = {"email": "dev@example.com", "name": "Dev", "last_seen": T - 20}
access._approved.update(at=time.time() + 3600, emails=set(), open=True)
r = c.get("/api/admin/users", headers=H).get_json()
rows = {u["email"]: u for u in r["users"]}
assert rows["a@example.com"]["services"] == {"picked": ["health", "goals"], "onboarded": True}
assert rows["a@example.com"]["minutes"] == 3 and rows["a@example.com"]["claude_calls"] == 2
assert rows["b@example.com"]["services"] == {"picked": [], "onboarded": False}
assert rows["dev@example.com"]["role"] == "admin" and rows["dev@example.com"]["services"] is None
assert set(rows["a@example.com"]) == {"email", "name", "role", "first_seen", "last_seen", "minutes", "sessions", "days",
                                      "claude_calls", "services"}

# the ping route: counted for a signed-in member, refused for the developer account
os.environ.pop("DEV_SKIP_AUTH")
main.DEV_SKIP_AUTH = False
who["email"] = "member@example.com"
assert c.post("/api/usage/ping", json={"section": "goals", "start": True}, headers=H).get_json()["counted"]
who["email"] = "dev@example.com"
assert c.post("/api/usage/ping", json={"section": "goals"}, headers=H).get_json()["error"] == "developer account"
assert c.get("/api/me", headers=H).get_json()["developer_only"] is True
who["email"] = "owner@example.com"
me = c.get("/api/me", headers=H).get_json()
assert me["admin"] and not me["developer_only"] and me["finances"]

print("devtools ok")

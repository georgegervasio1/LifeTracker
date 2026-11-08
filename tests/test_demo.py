"""Offline test of the demo profile (demo.py, demo_store.py, demo_seed.py): it seeds, every page's data loads,
paid and external features are blocked, visitors are kept apart, and real Firestore is never touched."""
import os as _os
HERE = _os.environ.get("LT_TESTS") or _os.path.dirname(_os.path.abspath(__file__))
# Reuse the in-memory Firestore from test_health.py (everything before its fake WHOOP HTTP).
exec(open(_os.path.join(HERE, "test_health.py")).read().split("# ---- fake WHOOP HTTP")[0])

import time as _t
import demo
from datetime import date, timedelta

today = date.today()
c = main.app.test_client()
A = {"Authorization": "Demo " + "a1" * 16}
B = {"Authorization": "Demo " + "b2" * 16}
y = today.year

t0 = _t.time()
r = c.get("/api/fin/overview", headers=A)
assert r.status_code == 200, r.get_data(as_text=True)[:2000]
print(f"seeded in {_t.time() - t0:.1f}s, {demo.template().size()} docs")

# ---- every page's data loads in the demo
s, s90 = today.isoformat(), (today - timedelta(days=90)).isoformat()
PAGES = ["/api/fin/overview", "/api/fin/cash", "/api/fin/investments", "/api/fin/investments?account=demo_roth",
         "/api/fin/investments?account=demo_brokerage", f"/api/fin/income?year={y}", f"/api/fin/taxes?year={y}",
         "/api/fin/imports", f"/api/earnings?year={y}", f"/api/taxes?year={y}", "/api/health/summary", "/api/workouts",
         "/api/training", "/api/training/history", f"/api/running?year={y}", f"/api/runs?start={s90}&end={s}", "/api/raceplans",
         f"/api/food/day?date={s}", "/api/food/history", "/api/food/goals", "/api/food/recent", "/api/edu", f"/api/edu/today?date={s}",
         "/api/edu/tasks", "/api/goals", "/api/goals/metrics", "/api/goals/library", "/api/transactions", "/api/summary", "/api/holdings"]
for p in PAGES:
    r = c.get(p, headers=A)
    assert r.status_code == 200, (p, r.status_code, r.get_data(as_text=True)[:300])

ov = c.get("/api/fin/overview", headers=A).get_json()
assert ov["net_worth"] > 10000 and all(a["balance"] is None or a["balance"] > -5000 for a in ov["accounts"]), ov["accounts"]
assert not any(a["account"].startswith("chase") for a in ov["accounts"])          # the built-in defaults stay hidden
assert c.get(f"/api/earnings?year={y}", headers=A).get_json()["ready"]
assert len(c.get(f"/api/running?year={y}", headers=A).get_json()["runs"]) > 20
assert len(c.get("/api/training/history", headers=A).get_json()["sessions"]) > 20
assert c.get("/api/health/summary", headers=A).get_json()["whoop"]["connected"]
edu = c.get("/api/edu", headers=A).get_json()
assert len(edu["courses"]) >= 10
gl = c.get("/api/goals", headers=A).get_json()["goals"]
assert len(gl) >= 6 and any(g["eval"].get("current") for g in gl)
plan = c.get("/api/raceplans", headers=A).get_json()["plans"][0]
assert c.get(f"/api/raceplans/{plan['id']}", headers=A).status_code == 200
run = c.get(f"/api/running?year={y}", headers=A).get_json()["runs"][0]
assert c.get(f"/api/running/{run['id']}", headers=A).get_json()["polyline"]

# ---- the same data every time today (every server instance builds the same demo)
docs1 = demo.template().docs
demo._reset_template()
docs2 = demo.template().docs
keep = ("/transactions/", "/health_days/", "/strava_activities/", "/fin_balances/", "/inv_snapshots/")
pick = lambda d: {k: v for k, v in d.items() if any(x in k for x in keep)}
assert pick(docs1) == pick(docs2) and len(pick(docs1)) > 500

# ---- visitors are kept apart, and edits stay in memory
r = c.post("/api/workouts", json={"date": s, "type": "Climbing", "duration_min": 40}, headers=A)
assert r.status_code == 200
see = lambda h: [w["type"] for w in c.get("/api/workouts", headers=h).get_json()["workouts"]]
assert "Climbing" in see(A) and "Climbing" not in see(B)

# ---- nothing reached the real (here: fake) Firestore
assert not [p for p in DATA if p.startswith("users/demo")], [p for p in DATA if p.startswith("users/demo")][:5]
assert not [p for p in DATA if "/workouts/" in p]

# ---- blocked: connections, Plaid, the AI assistant and coach, Gmail, syllabus reading, the owner's demo controls
for m, p in [("post", "/api/chat"), ("post", "/api/goals/coach"), ("post", "/api/plaid/link-token"), ("post", "/api/sync"),
             ("post", "/api/whoop/connect"), ("post", "/api/whoop/sync"), ("delete", "/api/whoop"), ("post", "/api/strava/connect"),
             ("post", "/api/strava/sync"), ("delete", "/api/strava"), ("post", "/api/edu/gmail/scan"), ("post", "/api/edu/plan"),
             ("post", "/api/edu/syllabi/upload"), ("post", "/api/demo/schedule"), ("delete", "/api/demo/schedule")]:
    r = getattr(c, m)(p, json={}, headers=A)
    assert r.status_code == 403 and r.get_json().get("demo"), (m, p, r.status_code)
# the assistant client refuses in the demo even if a route were missed
import assistant
with main.app.test_request_context("/"):
    from flask import g as _g
    _g.demo_db = demo.template()
    try:
        assistant.client(); raise AssertionError("assistant should be blocked")
    except demo.Blocked:
        pass

# ---- bad or missing session ids are refused; the demo can be turned off
assert c.get("/api/goals", headers={"Authorization": "Demo nope"}).status_code == 401
assert c.get("/api/goals", headers={"Authorization": "Demo " + demo._SEED_SID}).status_code == 401
demo.ENABLED = False
assert c.get("/api/goals", headers=A).status_code == 403
demo.ENABLED = True

# ---- sessions are capped
for i in range(demo.MAX_SESSIONS + 5):
    c.get("/api/food/goals", headers={"Authorization": "Demo " + f"{i:032x}"})
assert len(demo._sessions) <= demo.MAX_SESSIONS

# ---- the owner shares their course schedule: courses and syllabi without grades, notes or P/F choices
real = {"Authorization": "Bearer x"}       # DEV_SKIP_AUTH: the owner ("dev")
c.post("/api/edu/import", json={"replace": True, "courses": [
    {"term": "Fall 2026", "title": "Seminar Alpha", "credits": 3, "status": "enrolled", "professor": "Prof. A", "meetings_text": "MW 9:00-10:30 AM",
     "notes": "private note", "pf_elected": True},
    {"term": "Spring 2026", "title": "Course Beta", "credits": 4, "status": "completed", "grade": "A", "grade_status": "actual"}],
    "profile": {"target_gpa": 3.9}}, headers=real)
c.post("/api/edu/syllabi/import", json={"syllabi": [{"course_title": "Seminar Alpha", "term": "Fall 2026",
                                                     "sessions": [{"date": s, "topic": "Intro"}]}]}, headers=real)
r = c.post("/api/demo/schedule", headers=real).get_json()
assert r["courses"] == 2 and r["syllabi"] == 1, r
shared = DATA["demo/education"]
flat = str(shared)
assert "private note" not in flat and "'grade'" not in flat and "pf_elected" not in flat and "3.9" not in flat, flat[:500]
assert c.get("/api/demo/schedule", headers=real).get_json()["shared"]
edu = c.get("/api/edu", headers={"Authorization": "Demo " + "c3" * 16}).get_json()
titles = {x["title"]: x for x in edu["courses"]}
assert set(titles) == {"Seminar Alpha", "Course Beta"} and not any(x.get("grade") for x in edu["courses"])
assert titles["Seminar Alpha"]["meetings_text"] == "MW 9:00-10:30 AM"
# the owner's own data is unchanged
own = c.get("/api/edu", headers=real).get_json()
assert any(x.get("grade") == "A" for x in own["courses"])
c.delete("/api/demo/schedule", headers=real)
assert "demo/education" not in DATA
edu = c.get("/api/edu", headers={"Authorization": "Demo " + "d4" * 16}).get_json()
assert "Seminar Alpha" not in {x["title"] for x in edu["courses"]}

print("ALL DEMO TESTS PASSED")

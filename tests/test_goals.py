"""Offline test of goals.py: library, validation, linked metrics, pace, manual log, import, coach (fake Claude)."""
import os as _os
HERE = _os.environ.get("LT_TESTS") or _os.path.dirname(_os.path.abspath(__file__))
# Reuse the in-memory Firestore from test_health.py (everything before its fake WHOOP HTTP).
exec(open(_os.path.join(HERE, "test_health.py")).read().split("# ---- fake WHOOP HTTP")[0])

import goals, goals_library as LIB
from datetime import date, timedelta

today = date.today()
d = lambda n: (today + timedelta(days=n)).isoformat()
c = main.app.test_client()

# ---- library
assert len(LIB.LIBRARY) == 100, len(LIB.LIBRARY)
assert all(g["area"] in goals.AREAS and g["category"] in LIB.CATEGORIES for g in LIB.LIBRARY)
for g in LIB.LIBRARY:
    if g["metric"]:
        assert goals.split_metric(g["metric"])[0] in goals.METRICS, g
lib = c.get("/api/goals/library").get_json()
assert len(lib["library"]) == 100 and "Strength" in lib["categories"]

# ---- validation
assert c.post("/api/goals", json={}).status_code == 400
assert c.post("/api/goals", json={"title": "x", "area": "nope"}).status_code == 400
assert c.post("/api/goals", json={"title": "x", "start_date": d(5), "due_date": d(1)}).status_code == 400
assert c.post("/api/goals", json={"title": "x", "measure": {"kind": "number", "metric": "health.lift_top"}}).status_code == 400
assert c.post("/api/goals", json={"title": "x", "measure": {"kind": "number", "metric": "bogus"}}).status_code == 400

# ---- empty state: no data anywhere
r = c.get("/api/goals").get_json()
assert r["goals"] == [] and r["counts"]["active"] == 0

# ---- step plan goal: on track, then behind when a step is overdue
r = c.post("/api/goals", json={"title": "Climb Mount Kilimanjaro", "area": "personal", "library_id": "kilimanjaro",
                               "start_date": d(-30), "due_date": d(300),
                               "steps": [{"title": "Pick a route", "due": d(10)}, {"title": "Training hikes", "due": d(60)}, "Summit"]}).get_json()
kili = next(g for g in r["goals"] if g["id"] == r["id"])
e = kili["eval"]
assert e["progress"] == 0 and e["pace"] == "on_track" and e["next_step"]["title"] == "Pick a route" and e["steps_total"] == 3
steps = kili["steps"]; steps[0]["due"] = d(-2)
kili = next(g for g in c.put(f"/api/goals/{kili['id']}", json={"steps": steps}).get_json()["goals"] if g["id"] == kili["id"])
assert kili["eval"]["pace"] == "behind" and kili["eval"]["overdue_steps"] == 1
steps[0]["done"] = True
kili = next(g for g in c.put(f"/api/goals/{kili['id']}", json={"steps": steps}).get_json()["goals"] if g["id"] == kili["id"])
assert kili["eval"]["pace"] == "on_track" and abs(kili["eval"]["progress"] - 1 / 3) < 1e-3 and kili["steps"][0]["done"]
assert kili["eval"]["next_step"]["title"] == "Training hikes"

# ---- number goal the user updates: pace against the straight line
r = c.post("/api/goals", json={"title": "Run the six World Marathon Majors", "area": "health",
                               "measure": {"kind": "number", "unit": "races", "start": 0, "target": 6},
                               "start_date": d(-100), "due_date": d(100)}).get_json()
gid = r["id"]
g = next(x for x in r["goals"] if x["id"] == gid)
assert g["eval"]["current"] is None and g["eval"]["progress"] is None and g["eval"]["pace"] == "no_data"   # never guessed
r = c.post(f"/api/goals/{gid}/log", json={"value": 3, "date": d(-1)}).get_json()
g = next(x for x in r["goals"] if x["id"] == gid)
assert g["eval"]["current"]["value"] == 3 and g["eval"]["progress"] == 0.5 and g["eval"]["expected"] == 0.5 and g["eval"]["pace"] == "on_track"
r = c.post(f"/api/goals/{gid}/log", json={"value": 2}).get_json()
g = next(x for x in r["goals"] if x["id"] == gid)
assert g["eval"]["pace"] == "behind" and len(g["log"]) == 2
assert c.post(f"/api/goals/{gid}/log", json={"value": ""}).status_code == 400

# ---- linked lift goal reads training history; start snapshots today's best
DATA["users/dev/workouts/s1"] = {"source": "session", "date": d(-20), "exercises": [
    {"name": "Bench Press", "sets": [{"weight": 135, "reps": 10, "warmup": True}, {"weight": 205, "reps": 5}, {"weight": 225, "reps": 2}]}]}
r = c.post("/api/goals", json={"title": "Bench 315", "area": "health", "due_date": d(180),
                               "measure": {"kind": "number", "metric": "health.lift_top:bench press", "target": 315}}).get_json()
lift = next(x for x in r["goals"] if x["id"] == r["id"])
assert lift["measure"]["start"] == 225 and lift["measure"]["unit"] == "lb" and lift["eval"]["current"]["value"] == 225
assert lift["eval"]["progress"] == 0 and lift["eval"]["metric_label"].startswith("Heaviest set")
DATA["users/dev/workouts/s2"] = {"source": "session", "date": d(-1), "exercises": [
    {"name": "Bench Press", "sets": [{"weight": 270, "reps": 1}]}]}
lift = next(x for x in c.get("/api/goals").get_json()["goals"] if x["id"] == lift["id"])
assert lift["eval"]["current"]["value"] == 270 and lift["eval"]["current"]["as_of"] == d(-1) and lift["eval"]["progress"] == 0.5
m = c.get("/api/goals/metrics?exercise=Bench Press").get_json()
assert m["exercises"] == ["Bench Press"] and m["lift"]["health.lift_top"]["value"] == 270
assert abs(m["lift"]["health.lift_e1rm"]["value"] - 270) < 1e-6     # 1 rep: e1RM = weight (205x5 = 239.2 is lower)

# workouts since the goal started count from zero
r = c.post("/api/goals", json={"title": "Train often", "area": "health", "start_date": d(-10),
                               "measure": {"kind": "number", "metric": "health.workouts_since_start", "target": 4}}).get_json()
w = next(x for x in r["goals"] if x["id"] == r["id"])
assert w["eval"]["current"]["value"] == 1 and w["eval"]["progress"] == 0.25 and w["measure"]["start"] is None

# a linked metric with no data stays blank (no GPA without courses)
r = c.post("/api/goals", json={"title": "GPA", "area": "education", "measure": {"kind": "number", "metric": "edu.gpa", "target": 3.7}}).get_json()
gpa = next(x for x in r["goals"] if x["id"] == r["id"])
assert gpa["eval"]["current"] is None and gpa["measure"]["start"] is None and gpa["eval"]["pace"] == "no_data"

# ---- done, someday, ordering, counts
r = c.put(f"/api/goals/{gpa['id']}", json={"status": "someday"}).get_json()
r = c.put(f"/api/goals/{w['id']}", json={"status": "done"}).get_json()
done = next(x for x in r["goals"] if x["id"] == w["id"])
assert done["done_on"] == today.isoformat() and done["eval"]["pace"] == "done"
assert [x["status"] for x in r["goals"]][-2:] == ["someday", "done"]
assert r["counts"] == {"active": 3, "someday": 1, "done": 1, "behind": 1}

# ---- import: adds new, updates by title, validates
r = c.post("/api/goals/import", json={"goals": [
    {"title": "Run a marathon", "area": "health", "status": "done", "done_on": "2025-11-02", "library_id": "marathon"},
    {"title": "climb mount kilimanjaro", "area": "personal", "why": "updated"}]}).get_json()
assert r["added"] == 1 and r["updated"] == 1
assert next(x for x in r["goals"] if x["title"] == "Run a marathon")["done_on"] == "2025-11-02"
assert c.post("/api/goals/import", json={"goals": [{"title": ""}]}).status_code == 400

# ---- delete
n = len(c.get("/api/goals").get_json()["goals"])
assert len(c.delete(f"/api/goals/{gpa['id']}").get_json()["goals"]) == n - 1

# ---- coach: Claude proposes, nothing saved until the user saves
import assistant
class Blk:
    def __init__(s, **k): s.__dict__.update(k)
class Resp:
    def __init__(s, stop, content): s.stop_reason, s.content = stop, content
calls = []
def fake_create(**k):
    calls.append(k)
    if len(calls) == 1:
        return Resp("tool_use", [Blk(type="tool_use", id="t1", name="get_metrics", input={"exercise": "Bench Press"})])
    if len(calls) == 2:
        assert "270" in k["messages"][-1]["content"][0]["content"]
        return Resp("tool_use", [Blk(type="tool_use", id="t2", name="propose_goal", input={
            "title": "Dunk a basketball", "area": "health", "category": "Sport and combat",
            "smart": {"specific": "Dunk a regulation ball on a 10 ft rim", "time_bound": "By Jun 1"},
            "measure": {"kind": "steps"}, "due_date": d(220),
            "steps": [{"title": "Measure vertical", "due": d(7)}, {"title": "Touch rim", "due": d(60)}]})])
    return Resp("end_turn", [Blk(type="text", text="Here is a 7-month plan.")])
assistant._client = type("C", (), {"messages": type("M", (), {"create": staticmethod(fake_create)})()})()
before = len(c.get("/api/goals").get_json()["goals"])
r = c.post("/api/goals/coach", json={"messages": [{"role": "user", "content": "I want to dunk"}]}).get_json()
assert r["reply"] == "Here is a 7-month plan." and r["proposal"]["title"] == "Dunk a basketball"
assert len(r["proposal"]["steps"]) == 2 and r["proposal"]["smart"]["specific"].startswith("Dunk")
assert "Never invent" in calls[0]["system"] and calls[0]["tools"][-1]["name"] == "propose_goal"
assert len(c.get("/api/goals").get_json()["goals"]) == before
assert c.post("/api/goals/coach", json={"messages": []}).status_code == 400
# saving the proposal is a normal create
assert c.post("/api/goals", json=r["proposal"]).status_code == 200

# ---- pace math directly
g = {"measure": {"kind": "number", "start": 100, "target": 50, "current": 80}, "start_date": d(-10), "due_date": d(10), "log": []}
e = goals.evaluate(g, goals.Metrics())
assert e["progress"] == 0.4 and e["expected"] == 0.5 and e["pace"] == "behind"        # a "down" goal works the same way
g["due_date"] = d(-1)
assert goals.evaluate(g, goals.Metrics())["pace"] == "overdue"
g["measure"]["current"] = 50
assert goals.evaluate(g, goals.Metrics())["pace"] == "reached"

print("ALL GOALS TESTS PASSED")

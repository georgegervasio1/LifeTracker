"""Offline test of health.py: in-memory Firestore + fake WHOOP API."""
import os as _os
HERE = _os.environ.get("LT_TESTS") or _os.path.dirname(_os.path.abspath(__file__))
# Personal fixtures (bank CSVs, course and workout files) live outside the public repo.
PRIVATE = _os.environ.get("LT_PRIVATE", _os.path.join(HERE, "..", "..", "lifetracker-private"))
def _need(path):
    if not _os.path.exists(path):
        print(f"SKIP: private fixture {_os.path.basename(path)} not found (set LT_PRIVATE)"); raise SystemExit(0)
    return path
import os, sys, time, types
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, _os.path.join(HERE, "..", "backend"))
os.environ.update(DEV_SKIP_AUTH="1", PLAID_CLIENT_ID="x", PLAID_SECRET="y", WHOOP_CLIENT_ID="cid",
                  WHOOP_CLIENT_SECRET="csec", WHOOP_REDIRECT_URI="https://api.example/api/whoop/callback",
                  FRONTEND_URL="https://georgegervasio1.github.io/LifeTracker/")

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
import main, health
health.firestore = types.SimpleNamespace(SERVER_TIMESTAMP="SERVER_TS")
# last_sync is stored as a float in the fake; summary calls .isoformat() on real timestamps
import datetime as _dt
class TS(float):
    def isoformat(self): return _dt.datetime.fromtimestamp(self).isoformat()
_orig_set = DocRef.set
def _set(s, d, merge=False):
    d = {k: (TS(time.time()) if v == "SERVER_TS" else v) for k, v in d.items()}
    _orig_set(s, d, merge)
DocRef.set = _set

# ---- fake WHOOP HTTP ----------------------------------------------------------
calls = []
class R:
    def __init__(s, code, js): s.status_code, s._js = code, js
    def json(s): return s._js
    def raise_for_status(s):
        if s.status_code >= 400: raise Exception(f"HTTP {s.status_code}")
def fake_post(url, data=None, **k):
    calls.append(("POST", url, data))
    if data["grant_type"] == "authorization_code":
        assert data["code"] == "good-code" and data["redirect_uri"] == os.environ["WHOOP_REDIRECT_URI"]
        return R(200, {"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600, "scope": "offline read:recovery"})
    if data["grant_type"] == "refresh_token":
        assert data["refresh_token"] == "RT1"
        return R(200, {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 3600})
PAGES = {
  "/v2/cycle": [{"records": [{"id": 101, "start": "2026-10-20T11:00:00.000Z", "timezone_offset": "-04:00", "score_state": "SCORED", "score": {"strain": 12.345}}], "next_token": "p2"},
                {"records": [{"id": 100, "start": "2026-10-19T03:30:00.000Z", "timezone_offset": "-04:00", "score_state": "SCORED", "score": {"strain": 8.0}}], "next_token": None}],
  "/v2/recovery": [{"records": [{"cycle_id": 101, "score_state": "SCORED", "score": {"recovery_score": 72, "hrv_rmssd_milli": 61.27, "resting_heart_rate": 52}},
                                {"cycle_id": 100, "score_state": "PENDING_SCORE", "score": None}], "next_token": None}],
  "/v2/activity/sleep": [{"records": [{"id": "s1", "nap": False, "score_state": "SCORED", "end": "2026-10-20T11:00:00.000Z", "timezone_offset": "-04:00",
                                       "score": {"sleep_performance_percentage": 88, "stage_summary": {"total_in_bed_time_milli": 8 * 3.6e6, "total_awake_time_milli": 0.5 * 3.6e6}}},
                                      {"id": "s2", "nap": True, "score_state": "SCORED", "end": "2026-10-20T19:00:00.000Z", "timezone_offset": "-04:00", "score": {}}], "next_token": None}],
  "/v2/activity/workout": [{"records": [{"id": "w-uuid-1", "sport_name": "weightlifting", "start": "2026-10-20T22:00:00.000Z", "end": "2026-10-20T23:05:00.000Z",
                                         "timezone_offset": "-04:00", "score": {"strain": 9.81, "average_heart_rate": 121, "max_heart_rate": 165, "kilojoule": 1500}},
                                        {"id": "w-uuid-2", "sport_name": "running", "start": "2026-10-21T11:00:00.000Z", "end": "2026-10-21T11:40:00.000Z",
                                         "timezone_offset": "-04:00", "score": {"strain": 11.2, "distance_meter": 8046.7, "altitude_gain_meter": 31.4}}], "next_token": None}],
}
def fake_get(url, headers=None, params=None, **k):
    path = url.replace(health.WHOOP_API, "")
    calls.append(("GET", path, dict(params or {}), headers["Authorization"]))
    pages = PAGES[path]
    return R(200, pages[1] if params.get("nextToken") == "p2" else pages[0])
health.requests = types.SimpleNamespace(post=fake_post, get=fake_get, delete=lambda *a, **k: R(204, {}))

c = main.app.test_client()

# 1. connect -> authorize URL with 8-char state stored server-side
r = c.post("/api/whoop/connect").get_json()
q = parse_qs(urlparse(r["url"]).query)
state = q["state"][0]
assert r["url"].startswith(health.WHOOP_AUTH_URL) and len(state) == 8 and q["response_type"] == ["code"]
assert "offline" in q["scope"][0] and f"oauth_states/{state}" in DATA
print("connect OK, scopes:", q["scope"][0])

# 2. callback with bad state -> expired, good state -> connected (state is single-use)
r = c.get("/api/whoop/callback?code=good-code&state=WRONG123"); assert "whoop=expired" in r.headers["Location"]
r = c.get(f"/api/whoop/callback?code=good-code&state={state}"); assert "whoop=connected" in r.headers["Location"], r.headers["Location"]
assert DATA["users/dev/integrations/whoop"]["access_token"] == "AT1" and f"oauth_states/{state}" not in DATA
r = c.get(f"/api/whoop/callback?code=good-code&state={state}"); assert "whoop=expired" in r.headers["Location"]
r = c.get("/api/whoop/callback?error=access_denied&state=x"); assert "whoop=cancelled" in r.headers["Location"]
print("callback OK ->", "connected / replay rejected / cancel handled")

# 3. sync (pagination, nap skipped, unscored recovery skipped, local dates)
r = c.post("/api/whoop/sync").get_json(); print("sync:", r)
assert r == {"cycles": 2, "recoveries": 1, "sleeps": 1, "workouts": 2, "days": 2}
run = DATA["users/dev/workouts/whoop_w-uuid-2"]
assert run["distance_m"] == 8046.7 and run["elev_m"] == 31.4 and run["start_local"] == "2026-10-21T07:00:00" and run["type"] == "Running"
assert DATA["users/dev/integrations/whoop"]["has_distance"]
d20 = DATA["users/dev/health_days/2026-10-20"]; d18 = DATA["users/dev/health_days/2026-10-18"]
assert d20 == {"date": "2026-10-20", "strain": 12.3, "recovery": 72, "hrv": 61.3, "rhr": 52, "sleep_hours": 7.5, "sleep_performance": 88}, d20
assert d18["strain"] == 8.0   # 03:30Z on the 19th is the evening of the 18th in UTC-4
w = DATA["users/dev/workouts/whoop_w-uuid-1"]
assert w["type"] == "Weightlifting" and w["duration_min"] == 65 and w["calories"] == 359 and w["date"] == "2026-10-20"
assert any(x[0] == "GET" and x[2].get("nextToken") == "p2" for x in calls)
print("sync data OK:", d20)

# 4. token refresh when expired (rotating refresh token is saved)
DATA["users/dev/integrations/whoop"]["expires_at"] = time.time() - 5
c.post("/api/whoop/sync")
assert DATA["users/dev/integrations/whoop"]["refresh_token"] == "RT2" and calls[-1][3] == "Bearer AT2"
print("refresh OK")

# 5. manual workouts + validation + summary
assert c.post("/api/workouts", json={"date": "2026-10-21", "type": ""}).status_code == 400
assert c.post("/api/workouts", json={"date": "nope", "type": "Run"}).status_code == 400
assert c.post("/api/workouts", json={"date": "2026-10-21", "type": "Strength", "exercises": [{"name": "Bench", "sets": "x"}]}).status_code == 400
r = c.post("/api/workouts", json={"date": "2026-10-21", "type": "Strength", "duration_min": "50", "notes": "felt good",
                                  "exercises": [{"name": "Bench press", "sets": "4", "reps": "8", "weight": "185"}, {"name": ""}]})
wid = r.get_json()["id"]; saved = DATA[f"users/dev/workouts/{wid}"]
assert saved["exercises"] == [{"name": "Bench press", "sets": 4.0, "reps": 8.0, "weight": 185.0}] and saved["duration_min"] == 50
lst = c.get("/api/workouts?start=2026-10-01&end=2026-10-31").get_json()["workouts"]
assert [x["date"] for x in lst] == ["2026-10-21", "2026-10-21", "2026-10-20"]
assert c.delete("/api/workouts/whoop_w-uuid-1").status_code == 400
s = c.get("/api/health/summary?start=2026-10-01&end=2026-10-31").get_json()
assert s["latest"]["recovery"] == 72 and s["whoop"]["connected"] and s["averages"]["strain"] == 10.2
print("workouts + summary OK:", s["averages"])
assert c.delete(f"/api/workouts/{wid}").status_code == 200 and f"users/dev/workouts/{wid}" not in DATA

# 6. disconnect
assert c.delete("/api/whoop").status_code == 200 and "users/dev/integrations/whoop" not in DATA
assert c.post("/api/whoop/sync").status_code == 400
print("ALL HEALTH TESTS PASSED")

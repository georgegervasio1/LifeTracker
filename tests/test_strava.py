"""Offline test of strava.py (Running) and raceplan.py (race training): in-memory Firestore + fake Strava API."""
import os as _os
HERE = _os.environ.get("LT_TESTS") or _os.path.dirname(_os.path.abspath(__file__))
_os.environ.update(STRAVA_CLIENT_ID="sid", STRAVA_CLIENT_SECRET="ssec",
                   STRAVA_REDIRECT_URI="https://api.example/api/strava/callback")
# Reuse the in-memory Firestore from test_health.py (everything before its fake WHOOP HTTP).
exec(open(_os.path.join(HERE, "test_health.py")).read().split("# ---- fake WHOOP HTTP")[0])

import strava, raceplan
from datetime import date, timedelta
strava.firestore = raceplan.firestore = types.SimpleNamespace(SERVER_TIMESTAMP="SERVER_TS")

# ---- fake Strava HTTP ---------------------------------------------------------
calls = []
class R:
    def __init__(s, code, js): s.status_code, s._js = code, js
    def json(s): return s._js
    def raise_for_status(s):
        if s.status_code >= 400: raise Exception(f"HTTP {s.status_code}")
def fake_post(url, data=None, **k):
    calls.append(("POST", url, data))
    if url == strava.STRAVA_DEAUTH_URL: return R(200, {})
    if data["grant_type"] == "authorization_code":
        assert data["code"] == "good-code" and data["client_secret"] == "ssec"
        return R(200, {"access_token": "AT1", "refresh_token": "RT1", "expires_at": int(time.time()) + 21600,
                       "athlete": {"id": 7, "firstname": "Test", "lastname": "Runner"}})
    if data["grant_type"] == "refresh_token":
        assert data["refresh_token"] == "RT1"
        return R(200, {"access_token": "AT2", "refresh_token": "RT2", "expires_at": int(time.time()) + 21600})

def act(i, day, miles, secs, sport="Run", **kw):
    return {"id": i, "name": f"Run {i}", "sport_type": sport, "start_date": f"{day}T11:00:00Z",
            "start_date_local": f"{day}T07:00:00Z", "distance": miles * 1609.344, "moving_time": secs,
            "elapsed_time": secs + 30, "total_elevation_gain": 20.0, "average_speed": miles * 1609.344 / secs,
            "average_heartrate": 150.0, "map": {"summary_polyline": "abc"}, **kw}
today = date.today()
d = lambda n: (today - timedelta(days=n)).isoformat()
ACTS = [act(1, d(2), 6.0, 6 * 480, workout_type=1), act(2, d(5), 3.1, 3.1 * 420), act(3, d(9), 10.0, 10 * 540),
        act(4, d(12), 20.0, 3600, sport="Ride"), act(5, f"{today.year - 1}-06-01", 5.0, 5 * 500)]
def fake_get(url, headers=None, params=None, **k):
    path = url.replace(strava.STRAVA_API, "")
    calls.append(("GET", path, dict(params or {}), headers["Authorization"]))
    if path == "/athlete/activities":
        items = [a for a in ACTS if a["start_date"] >= _dt.datetime.fromtimestamp(params["after"], _dt.timezone.utc).strftime("%Y-%m-%d")]
        return R(200, items if params["page"] == 1 else [])
    aid = int(path.split("/")[-1])
    a = next(x for x in ACTS if x["id"] == aid)
    return R(200, a | {"description": "nice", "calories": 600.4, "map": {"polyline": "full", "summary_polyline": "abc"},
                       "splits_standard": [{"split": 1, "distance": 1609.3, "moving_time": 480, "elevation_difference": 2.0}],
                       "best_efforts": [{"name": "5K", "elapsed_time": 1500, "distance": 5000}, {"name": "1 mile", "elapsed_time": 450, "distance": 1609}]})
strava.requests = types.SimpleNamespace(post=fake_post, get=fake_get, RequestException=Exception)

c = main.app.test_client()

# 1. connect + callback (state single use, scope must include activity access)
r = c.post("/api/strava/connect").get_json()
q = parse_qs(urlparse(r["url"]).query); state = q["state"][0]
assert r["url"].startswith(strava.STRAVA_AUTH_URL) and q["scope"] == ["read,activity:read_all"] and f"oauth_states/{state}" in DATA
r = c.get(f"/api/strava/callback?code=good-code&state={state}&scope=read"); assert "strava=scope" in r.headers["Location"]
r = c.post("/api/strava/connect").get_json(); state = parse_qs(urlparse(r["url"]).query)["state"][0]
r = c.get(f"/api/strava/callback?code=good-code&state={state}&scope=read,activity:read_all")
assert "#/health/connect?strava=connected" in r.headers["Location"], r.headers["Location"]
assert DATA["users/dev/integrations/strava"]["athlete_name"] == "Test Runner"
assert "strava=expired" in c.get(f"/api/strava/callback?code=good-code&state={state}&scope=read,activity:read_all").headers["Location"]
assert "strava=cancelled" in c.get("/api/strava/callback?error=access_denied&state=x").headers["Location"]
print("connect + callback OK")

# 2. first sync pulls Jan 1 last year onward, then details for runs only
r = c.post("/api/strava/sync").get_json(); print("sync:", r)
first = next(x for x in calls if x[0] == "GET" and x[1] == "/athlete/activities")
assert _dt.datetime.fromtimestamp(first[2]["after"], _dt.timezone.utc).date() == date(today.year - 1, 1, 1)
assert r["activities"] == 5 and r["runs"] == 4 and r["details"] == 4 and r["note"] is None
a1 = DATA["users/dev/strava_activities/1"]
assert a1["has_detail"] and a1["splits"][0]["moving_s"] == 480 and a1["name"] == "Run 1" and a1["date"] == d(2)
assert "has_detail" not in DATA["users/dev/strava_activities/4"]       # rides are stored but not detailed
# incremental sync re-reads only the last week and keeps detail fields
calls.clear(); ACTS[0]["name"] = "Renamed"
c.post("/api/strava/sync")
assert DATA["users/dev/strava_activities/1"]["name"] == "Renamed" and DATA["users/dev/strava_activities/1"]["has_detail"]
assert not any(x[1].startswith("/activities/") for x in calls if x[0] == "GET")
print("sync OK")

# 3. Running tab for this year
y = c.get(f"/api/running?year={today.year}").get_json()
expect = [x for x in ACTS if x["sport_type"] == "Run" and x["start_date"][:4] == str(today.year)]
assert y["totals"]["runs"] == len(expect) and y["strava"]["connected"] and y["strava"]["athlete"] == "Test Runner"
assert abs(y["totals"]["miles"] - sum(x["distance"] for x in expect) / 1609.344) < 0.01
assert {b["name"] for b in y["bests"]} == {"1 mile", "5K"} and str(today.year - 1) in y["years"]
assert abs(sum(w["miles"] for w in y["weekly"]) - y["totals"]["miles"]) < 0.05
assert "splits" not in y["runs"][0] and y["runs"][0]["polyline"] == "abc"
one = c.get("/api/running/3").get_json(); assert one["polyline_full"] == "full" and one["calories"] == 600
assert c.get("/api/running/999").status_code == 404
print("running year OK:", y["totals"])

# 3b. WHOOP runs fill in when Strava doesn't have them (same run on both shows once)
DATA["users/dev/workouts/whoop_a"] = {"source": "whoop", "type": "Running", "date": d(3), "start_local": f"{d(3)}T18:00:00",
                                      "duration_min": 30, "distance_m": 5000.0, "avg_hr": 150, "elev_m": 12.0}
DATA["users/dev/workouts/whoop_b"] = {"source": "whoop", "type": "Running", "date": d(2), "start_local": f"{d(2)}T07:10:00",
                                      "duration_min": 48, "distance_m": 9600.0}
DATA["users/dev/workouts/whoop_c"] = {"source": "whoop", "type": "Weightlifting", "date": d(3), "duration_min": 60}
DATA["users/dev/workouts/whoop_d"] = {"source": "whoop", "type": "Running", "date": d(4), "duration_min": 20}   # no GPS
y2 = c.get(f"/api/running?year={today.year}").get_json()
ids = [r["id"] for r in y2["runs"]]
assert "whoop_a" in ids and "whoop_d" in ids and "whoop_b" not in ids and "whoop_c" not in ids, ids
assert y2["totals"]["runs"] == y["totals"]["runs"] + 2 and y2["totals"]["no_distance"] == 1
assert abs(y2["totals"]["miles"] - (y["totals"]["miles"] + 5000 / 1609.344)) < 0.01
wa = c.get("/api/running/whoop_a").get_json(); assert wa["source"] == "whoop" and wa["moving_s"] == 1800
assert {r["id"] for r in c.get(f"/api/runs?start={d(5)}&end={d(0)}").get_json()["runs"]} >= {"whoop_a", "1"}
for k in ("a", "b", "c", "d"):
    DATA.pop(f"users/dev/workouts/whoop_{k}")
print("WHOOP runs OK")

# 4. VDOT matches Daniels' published table (VDOT 50 = 19:57 5K, 3:10:49 marathon, T 6:51/mi)
v = raceplan.vdot(5000, 19 * 60 + 57)
assert abs(v - 50.0) < 0.1, v
assert abs(raceplan.race_seconds(50, 42195) - (3 * 3600 + 10 * 60 + 49)) < 15        # table rounds VDOT
p = raceplan.training_paces(50)
assert raceplan.fmt_pace(p["T"]) == "6:51" and abs(p["M"] - 437) <= 2, p
print("VDOT OK:", {k: v for k, v in p.items()})

# 5. plans: structure and safety rules
T0 = date(2026, 10, 22)
for race, runs, cur in [("5k", 3, 8), ("10k", 4, 20), ("half", 5, 18), ("marathon", 5, 25), ("marathon", 6, 45)]:
    rd = T0 + timedelta(weeks=raceplan.RACES[race]["ideal"] + 1)
    pl = raceplan.build_plan({"race": race, "race_date": rd.isoformat(), "runs_per_week": runs,
                              "current_weekly_mi": cur, "current_long_mi": cur * 0.3}, today=T0)
    W = pl["weeks"]
    assert W[0]["start"] == "2026-10-26" and W[-1]["phase"] == "race"
    assert W[-1]["days"][-1]["date"] == rd.isoformat() and W[-1]["days"][-1]["kind"] == "race"
    for w in W[:-1]:
        assert sum(1 for x in w["days"] if x["kind"] != "rest") == runs, (race, w)
        assert len(w["days"]) == 7 and sum(1 for x in w["days"] if x["kind"] == "long") == 1
    pre = [w for w in W if w["phase"] not in ("taper", "race")]
    for a, b in zip(pre, pre[1:]):     # no jump over ~10% + rounding, except coming back from a cutback
        if not a["cutback"]:
            assert b["miles"] <= a["miles"] * 1.12 + 2.5, (race, a["miles"], b["miles"])
    taper = [w for w in W if w["phase"] == "taper"]
    assert len(taper) == raceplan.RACES[race]["taper"] and all(t["miles"] < pl["peak_miles"] for t in taper)
    longest = max(x["miles"] for w in W[:-1] for x in w["days"] if x["kind"] == "long")
    assert longest <= raceplan.RACES[race]["long_peak"] + 0.01
    print(f"plan {race:8s} {len(W)} wk, peak {pl['peak_miles']} mi, long {longest} mi")
# a long-run day other than Sunday moves the pattern
pl = raceplan.build_plan({"race": "half", "race_date": "2027-02-20", "runs_per_week": 4, "long_day": 5,
                          "current_weekly_mi": 20, "current_long_mi": 8}, today=T0)
assert all(date.fromisoformat(x["date"]).weekday() == 5 for w in pl["weeks"][:-1] for x in w["days"] if x["kind"] == "long")
# validation
for bad in [{"race": "ultra", "race_date": "2027-01-01"}, {"race": "5k", "race_date": "2026-10-01"},
            {"race": "5k", "race_date": "2027-01-01", "runs_per_week": 9}]:
    try:
        raceplan.build_plan(bad, today=T0); raise AssertionError(bad)
    except ValueError:
        pass
# goal time sets goal pace; no fitness and no goal means effort words, never invented paces
pl = raceplan.build_plan({"race": "half", "race_date": "2027-02-21", "goal_seconds": 6300}, today=T0)
assert pl["paces"]["G"] == round(6300 / (21097.5 / 1609.344)) and pl["fitness"]["source"] == "your goal time"
pl = raceplan.build_plan({"race": "half", "race_date": "2027-02-21"}, today=T0)
assert pl["paces"] is None and pl["fitness"]["vdot"] is None and "/mi" not in str(pl["weeks"])
# a far-off race is capped at MAX_WEEKS with a note
pl = raceplan.build_plan({"race": "marathon", "race_date": "2027-10-10", "current_weekly_mi": 30}, today=T0)
assert len(pl["weeks"]) == raceplan.MAX_WEEKS and pl["notes"]
print("plan rules OK")

# 6. fitness from Strava + API: preview, save, planned vs done
fit = raceplan.fitness_from_runs([DATA[k] for k in DATA if k.startswith("users/dev/strava_activities/")], today)
assert fit["runs_4wk"] == 3 and fit["long_mi"] == 10.0 and fit["vdot"] and fit["source"], fit
lst = c.get("/api/raceplans").get_json(); assert lst["plans"] == [] and lst["fitness"]["vdot"] == fit["vdot"]
body = {"race": "10k", "race_date": (today + timedelta(weeks=10)).isoformat(), "runs_per_week": 4, "current_weekly_mi": 15}
assert c.post("/api/raceplans/preview", json=body).get_json()["fitness"]["vdot"] == fit["vdot"]
assert c.post("/api/raceplans/preview", json={**body, "race": "x"}).status_code == 400
pid = c.post("/api/raceplans", json={**body, "name": "Turkey Trot"}).get_json()["id"]
g = c.get(f"/api/raceplans/{pid}").get_json()
assert g["name"] == "Turkey Trot" and "done_miles" in g["weeks"][0]
# planned vs done: a plan that started three weeks ago, matched against the synced runs
past = raceplan.build_plan(body, today=today - timedelta(weeks=3))
with main.app.test_request_context():
    main.g.uid = "dev"
    runs_now = strava.list_runs(past["start"], past["race_date"])
done = raceplan.attach_actuals(past, runs_now)
hit = [x for w in done["weeks"] for x in w["days"] if x.get("actual")]
expect_days = {r["date"] for r in runs_now}
assert {x["date"] for x in hit} == expect_days and expect_days
assert abs(sum(w["done_miles"] for w in done["weeks"]) - sum(r["distance_m"] for r in runs_now) / 1609.344) < 0.2
assert c.get("/api/raceplans").get_json()["plans"][0]["name"] == "Turkey Trot"
assert c.delete(f"/api/raceplans/{pid}").status_code == 200 and c.get(f"/api/raceplans/{pid}").status_code == 404
print("race plan API OK")

# 7. refresh when expired, full resync removes deleted activities, disconnect
DATA["users/dev/integrations/strava"]["expires_at"] = time.time() - 5
del ACTS[1]
r = c.post("/api/strava/sync?full=1").get_json()
assert DATA["users/dev/integrations/strava"]["refresh_token"] == "RT2" and r["removed"] == 1
assert "users/dev/strava_activities/2" not in DATA
assert c.delete("/api/strava").status_code == 200 and "users/dev/integrations/strava" not in DATA
assert c.post("/api/strava/sync").status_code == 400
assert not c.get(f"/api/running?year={today.year}").get_json()["strava"]["connected"]
print("ALL STRAVA + RACE PLAN TESTS PASSED")

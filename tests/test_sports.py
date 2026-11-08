"""Offline test of sports.py: in-memory Firestore + fake ESPN responses (shapes copied from the live API)."""
import os as _os
HERE = _os.environ.get("LT_TESTS") or _os.path.dirname(_os.path.abspath(__file__))
exec(open(_os.path.join(HERE, "test_health.py")).read().split("# ---- fake WHOOP HTTP")[0])

import sports
sports.firestore = types.SimpleNamespace(SERVER_TIMESTAMP="SERVER_TS")


def team(i, loc, nick, abbr, color="112233"):
    return {"id": str(i), "location": loc, "name": nick, "nickname": nick, "abbreviation": abbr,
            "displayName": f"{loc} {nick}", "shortDisplayName": nick, "color": color,
            "logos": [{"href": f"https://a.espncdn.com/i/teamlogos/nfl/500/{abbr.lower()}.png", "rel": ["full", "default"]}]}

A, B, C = team(1, "Alpha", "Owls", "ALP"), team(2, "Beta", "Bears", "BET", "zzz"), team(3, "Gamma", "Goats", "GAM")

def comp(home, away, hs=None, as_=None, done=False, venue="Big Stadium", city="Springfield", st="IL", tv="FOX", valid=True, neutral=False):
    def side(t, ha, sc, win):
        c = {"id": t["id"], "homeAway": ha, "team": t, "record": [{"type": "total", "displayValue": "1-0"}, {"type": "home", "displayValue": "1-0"}],
             "curatedRank": {"current": 99}}
        if sc is not None: c["score"] = {"value": float(sc), "displayValue": str(sc)}
        if win is not None: c["winner"] = win
        return c
    status = ({"type": {"name": "STATUS_FINAL", "state": "post", "completed": True, "description": "Final", "shortDetail": "Final"}}
              if done else {"type": {"name": "STATUS_SCHEDULED", "state": "pre", "completed": False, "description": "Scheduled",
                                     "shortDetail": "Sun, Nov 15th at 1:00 PM EST"}})
    return {"id": "c", "timeValid": valid, "neutralSite": neutral,
            "venue": {"fullName": venue, "address": {"city": city, "state": st, "country": "USA"}},
            "competitors": [side(home, "home", hs, (hs > as_) if done else None), side(away, "away", as_, (as_ > hs) if done else None)],
            "broadcasts": [{"type": {"shortName": "TV"}, "market": {"type": "National"}, "media": {"shortName": tv}}], "status": status}

def ev(i, date, home, away, **k):
    return {"id": str(i), "date": date, "name": f"{away['displayName']} at {home['displayName']}", "shortName": f"{away['abbreviation']} @ {home['abbreviation']}",
            "seasonType": {"id": "2", "type": 2, "name": "Regular Season", "abbreviation": "reg"}, "week": {"number": 1, "text": f"Week {i}"},
            "timeValid": k.get("valid", True), "competitions": [comp(home, away, **k)]}

SCHED = {
    "1": {"status": "success", "season": {"year": 2026, "displayName": "2026"},
          "team": {**{k: A[k] for k in ("id", "abbreviation", "location", "name", "displayName", "color")}, "logo": A["logos"][0]["href"],
                   "recordSummary": "1-1", "standingSummary": "2nd in North"},
          "events": [ev(102, "2026-11-15T18:00Z", B, A, valid=False),
                     ev(100, "2026-09-13T17:00Z", A, B, hs=27, as_=20, done=True),
                     ev(101, "2026-09-20T20:25Z", C, A, hs=31, as_=10, done=True, venue="Goat Field", city="Austin", st="TX", tv="CBS")]},
    "2": {"team": {**{k: B[k] for k in ("id", "abbreviation", "location", "name", "displayName")}},
          "events": [ev(102, "2026-11-15T18:00Z", B, A, valid=False), ev(100, "2026-09-13T17:00Z", A, B, hs=27, as_=20, done=True)]},
}
STAND = {"name": "League", "children": [
    {"name": "North Conference", "abbreviation": "NC", "standings": {"seasonDisplayName": "2026", "entries": [
        {"team": B, "stats": [{"name": "wins", "value": 0, "displayValue": "0"}, {"name": "losses", "value": 1, "displayValue": "1"},
                              {"name": "winPercent", "value": 0.0, "displayValue": ".000"}, {"name": "playoffSeed", "value": 2, "displayValue": "2"},
                              {"name": "streak", "value": -1, "displayValue": "L1"},
                              {"id": "0", "name": "overall", "type": "total", "summary": "0-1", "displayValue": "0-1"}]},
        {"team": A, "stats": [{"name": "wins", "value": 1, "displayValue": "1"}, {"name": "losses", "value": 1, "displayValue": "1"},
                              {"name": "ties", "value": 0, "displayValue": "0"},
                              {"name": "winPercent", "value": 0.5, "displayValue": ".500"}, {"name": "playoffSeed", "value": 1, "displayValue": "1"},
                              {"name": "streak", "value": -1, "displayValue": "L1"}]}]}}]}

calls = []
class R:
    def __init__(s, code, js): s.status_code, s._js = code, js
    def json(s): return s._js
    def raise_for_status(s):
        if s.status_code >= 400: raise Exception(f"HTTP {s.status_code}")
down = {"on": False}
def fake_get(url, params=None, **k):
    calls.append(url)
    if down["on"]: raise ConnectionError("down")
    base = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
    if url == base + "/teams": return R(200, {"sports": [{"leagues": [{"teams": [{"team": B}, {"team": A}, {"team": C}]}]}]})
    if url.startswith(base + "/teams/") and url.endswith("/schedule"):
        tid = url.split("/")[-2]
        return R(200, SCHED[tid]) if tid in SCHED else R(404, {})
    if url == "https://site.api.espn.com/apis/v2/sports/football/nfl/standings": return R(200, STAND)
    return R(404, {})
sports.requests = types.SimpleNamespace(get=fake_get)
c = main.app.test_client()

# 1. leagues + teams (sorted, colors validated, logo from logos[])
r = c.get("/api/sports").get_json()
assert any(l["key"] == "nfl" for l in r["leagues"]) and r["favorites"] == []
r = c.get("/api/sports/teams?league=nfl").get_json()
assert [t["abbr"] for t in r["teams"]] == ["ALP", "BET", "GAM"]
assert r["teams"][0]["color"] == "#112233" and r["teams"][1]["color"] == "" and r["teams"][0]["logo"].endswith("/alp.png")
assert c.get("/api/sports/teams?league=xyz").status_code == 400
n = len(calls); c.get("/api/sports/teams?league=nfl"); assert len(calls) == n, "teams list is cached"

# 2. favorites: validated, deduped, junk dropped
r = c.put("/api/sports/favorites", json={"teams": [
    {"league": "nfl", "id": "1", "name": "Alpha Owls", "abbr": "ALP", "color": "#112233", "logo": "https://a.espncdn.com/x.png"},
    {"league": "nfl", "id": "1"}, {"league": "nfl", "id": "2", "name": "Beta Bears", "logo": "javascript:alert(1)"},
    {"league": "zzz", "id": "3"}, {"league": "nfl", "id": "../4"}]}).get_json()
assert [(t["league"], t["id"]) for t in r["favorites"]] == [("nfl", "1"), ("nfl", "2")], r
assert r["favorites"][1]["logo"] == ""
assert c.get("/api/sports").get_json()["favorites"][0]["name"] == "Alpha Owls"
too_many = [{"league": "nba", "id": str(i)} for i in range(sports.MAX_FAVORITES + 1)]
assert c.put("/api/sports/favorites", json={"teams": too_many}).status_code == 400

# 3. one team: record + standing from ESPN, games sorted, scores, venue, TV, TBD
r = c.get("/api/sports/team/nfl/1").get_json()
assert r["team"]["record"] == "1-1" and r["team"]["standing"] == "2nd in North"
gs = r["games"]
assert [gm["id"] for gm in gs] == ["100", "101", "102"]
g0, g1, g2 = gs
assert g0["home"]["score"] == 27 and g0["home"]["winner"] is True and g0["completed"] and g0["detail"] == "Final"
assert g1["venue"] == {"name": "Goat Field", "city": "Austin", "state": "TX"} and g1["tv"] == "CBS" and g1["away"]["abbr"] == "ALP"
assert g2["tbd"] is True and g2["state"] == "pre" and g2["home"]["score"] is None and g2["home"]["record"] == "1-0"
assert c.get("/api/sports/team/nfl/abc").status_code == 400

# record falls back to completed games when ESPN leaves it out (team 2: lost 20-27)
r = c.get("/api/sports/team/nfl/2").get_json()
assert r["team"]["record"] == "0-1" and r["team"]["standing"] == ""

# 4. merged schedule: game 100 and 102 involve both favorites, shown once each
r = c.get("/api/sports/schedule").get_json()
ids = [gm["id"] for gm in r["games"]]
assert ids == ["100", "101", "102"], ids
assert sorted(r["games"][0]["favorites"]) == ["nfl:1", "nfl:2"] and r["games"][1]["favorites"] == ["nfl:1"]
assert {t["id"]: t["record"] for t in r["teams"]} == {"1": "1-1", "2": "0-1"} and r["errors"] == []

# 5. standings: grouped, sorted by seed, columns only where data exists
r = c.get("/api/sports/standings/nfl").get_json()
grp = r["groups"][0]
assert grp["name"] == "North Conference" and [t["abbr"] for t in grp["teams"]] == ["ALP", "BET"]
assert r["columns"] == ["W", "L", "T", "PCT", "STRK"] and grp["teams"][0]["stats"]["PCT"] == ".500" and r["season"] == "2026"

# 6. iCalendar: public, folded lines, escaped text, all-day when the time is TBD, finals show the score
res = c.get("/api/sports/calendar.ics?teams=nfl:1,nfl:2,bogus,nfl:1&download=1")
assert res.status_code == 200 and res.mimetype == "text/calendar" and "attachment" in res.headers["Content-Disposition"]
body = res.get_data(as_text=True)
assert body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
assert body.count("BEGIN:VEVENT") == 3
assert "UID:nfl-100@lifetracker" in body and "DTSTART:20260913T170000Z" in body and "DTEND:20260913T203000Z" in body
assert "DTSTART;VALUE=DATE:20261115" in body and "DTEND;VALUE=DATE:20261116" in body
unfolded = body.replace("\r\n ", "")
assert "SUMMARY:Beta Bears at Alpha Owls (BET 20\\, ALP 27)" in unfolded
assert "LOCATION:Goat Field\\, Austin\\, TX" in unfolded
assert all(len(l.encode()) <= 75 for l in body.split("\r\n")), "lines folded at 75 octets"
assert "X-WR-CALNAME:Sports: Owls\\, Bears" in unfolded
assert c.get("/api/sports/calendar.ics?teams=").status_code == 400
one = c.get("/api/sports/calendar.ics?teams=nfl:1")
assert 'filename="owls-schedule.ics"' in one.headers["Content-Disposition"] and "inline" in one.headers["Content-Disposition"]

# 7. ESPN down: cached data still served; uncached answers 502 with a readable error
down["on"] = True
assert c.get("/api/sports/team/nfl/1").status_code == 200
sports._cache.clear()
r = c.get("/api/sports/team/nfl/1"); assert r.status_code == 502 and "ESPN" in r.get_json()["error"]
r = c.get("/api/sports/schedule").get_json(); assert r["games"] == [] and len(r["errors"]) == 2
down["on"] = False

print("sports: favorites, schedules, merged calendar, standings, ics: OK")

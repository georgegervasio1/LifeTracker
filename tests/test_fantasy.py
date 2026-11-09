"""Offline test of fantasy.py (Sports → Fantasy Football): in-memory Firestore + a fake ESPN fantasy league (made up)."""
import os as _os
HERE = _os.environ.get("LT_TESTS") or _os.path.dirname(_os.path.abspath(__file__))
exec(open(_os.path.join(HERE, "test_health.py")).read().split("# ---- fake WHOOP HTTP")[0])

import fantasy, json
from datetime import date
fantasy.firestore = types.SimpleNamespace(SERVER_TIMESTAMP="SERVER_TS")
SWID = "{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}"
S2 = "AEB" + "x1Y2z3%2F" * 12

def player(pid, name, pos, pro, period, act, proj, inj="ACTIVE"):
    return {"id": pid, "fullName": name, "defaultPositionId": pos, "proTeamId": pro, "injuryStatus": inj,
            "ownership": {"percentOwned": 97.5},
            "stats": [{"seasonId": 2026, "scoringPeriodId": period, "statSourceId": 0, "statSplitTypeId": 1, "appliedTotal": act},
                      {"seasonId": 2026, "scoringPeriodId": period, "statSourceId": 1, "statSplitTypeId": 1, "appliedTotal": proj},
                      {"seasonId": 2026, "scoringPeriodId": 0, "statSourceId": 0, "statSplitTypeId": 0, "appliedTotal": 150.0}]}
def entry(slot, p):
    return {"playerId": p["id"], "lineupSlotId": slot, "playerPoolEntry": {"id": p["id"], "appliedStatTotal": 150.04, "player": p}}
def team(i, loc, nick, owner, w, l, seed, roster=()):
    return {"id": i, "abbrev": nick[:4].upper(), "location": loc, "nickname": nick, "divisionId": 0, "owners": [owner],
            "primaryOwner": owner, "playoffSeed": seed, "logo": "https://example.com/l.png" if i == 1 else "javascript:x",
            "record": {"overall": {"wins": w, "losses": l, "ties": 0, "pointsFor": 1001.456, "pointsAgainst": 950.1,
                                   "streakLength": 2, "streakType": "WIN"}},
            "currentSimulationResults": {"playoffPct": 0.734},
            "roster": {"entries": list(roster)}}
WEEK = 10
LEAGUE = {
    "seasonId": 2026, "scoringPeriodId": WEEK, "status": {"currentMatchupPeriod": WEEK, "finalScoringPeriod": 17},
    "settings": {"name": "Sample League", "size": 4, "scoringSettings": {"scoringType": "H2H_POINTS"},
                 "scheduleSettings": {"matchupPeriodCount": 14, "playoffTeamCount": 2, "divisions": [{"id": 0, "name": "East"}]},
                 "acquisitionSettings": {"isUsingAcquisitionBudget": True}, "tradeSettings": {"deadlineDate": 1795000000000}},
    "members": [{"id": SWID, "displayName": "me_user"}, {"id": "{OTHER-1}", "firstName": "Pat", "lastName": "Doe"},
                {"id": "{OTHER-2}", "displayName": "sam"}, {"id": "{OTHER-3}", "displayName": "lee"}],
    "teams": [team(2, "North", "Ninjas", "{OTHER-1}", 7, 2, 1),
              team(1, "South", "Sharks", SWID, 6, 3, 2, [
                  entry(20, player(9, "Bench Guy", 2, 12, WEEK, 3.1, 8.0)),
                  entry(23, player(5, "Flex Wide", 3, 21, WEEK, 11.0, 12.5)),
                  entry(0, player(3, "Qb Prime", 1, 2, WEEK, 24.36, 21.0)),
                  entry(21, player(8, "Hurt Back", 2, 6, WEEK, None, 0, "INJURY_RESERVE")),
                  entry(2, player(4, "Run Fast", 2, 33, WEEK, 17.5, 14.2, "QUESTIONABLE"))]),
              team(3, "East", "Eagles", "{OTHER-2}", 3, 6, 3), team(4, "West", "Wolves", "{OTHER-3}", 2, 7, 4)],
    "schedule": [{"id": 1, "matchupPeriodId": 9, "home": {"teamId": 1, "totalPoints": 120.5}, "away": {"teamId": 3, "totalPoints": 99.2}, "winner": "HOME"},
                 {"id": 2, "matchupPeriodId": WEEK, "home": {"teamId": 2, "totalPoints": 0, "totalPointsLive": 55.25, "totalProjectedPointsLive": 118.4},
                  "away": {"teamId": 1, "totalPoints": 0, "totalPointsLive": 62.0, "totalProjectedPointsLive": 121.9}, "winner": "UNDECIDED"},
                 {"id": 3, "matchupPeriodId": WEEK, "home": {"teamId": 3, "totalPoints": 40}, "away": {"teamId": 4, "totalPoints": 31}, "winner": "UNDECIDED"},
                 {"id": 4, "matchupPeriodId": 15, "home": {"teamId": 1}, "away": {"teamId": 2}, "winner": "UNDECIDED", "playoffTierType": "WINNERS_BRACKET"}]}

calls = []
class R:
    def __init__(s, code, js): s.status_code, s._js = code, js
    def json(s):
        if s._js is None: raise ValueError("html")
        return s._js
mode = {"private": False, "down": False}
def fake_get(url, params=None, cookies=None, **k):
    calls.append((url, params, cookies))
    if mode["down"]: raise ConnectionError("down")
    if "/leagues/999" in url: return R(404, {})
    if "/leagues/12345" not in url: return R(500, {})
    if mode["private"] and not (cookies and cookies.get("SWID") == SWID and cookies.get("espn_s2") == S2): return R(401, {})
    return R(200, LEAGUE)
fantasy.requests = types.SimpleNamespace(get=fake_get)
c = main.app.test_client()

# 0. helpers
assert fantasy.default_season(date(2027, 1, 10)) == 2026 and fantasy.default_season(date(2026, 9, 5)) == 2026
assert fantasy.clean_swid("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee") == SWID and fantasy.clean_swid("nope") == ""
assert fantasy.clean_s2(S2) == S2 and fantasy.clean_s2("short") == ""

# 1. not connected
assert c.get("/api/fantasy").get_json()["connected"] is False
assert c.get("/api/fantasy/league").status_code == 400

# 2. validation
assert c.put("/api/fantasy/connect", json={"league_id": "abc"}).status_code == 400
assert c.put("/api/fantasy/connect", json={"league_id": "12345", "espn_s2": S2}).status_code == 400          # one cookie only
assert c.put("/api/fantasy/connect", json={"league_id": "12345", "swid": "bad", "espn_s2": S2}).status_code == 400
r = c.put("/api/fantasy/connect", json={"league_id": "999", "season": 2026}); assert r.status_code == 404 and "999" in r.get_json()["error"]

# 3. private league without cookies -> 401 with a clear message; with cookies -> saved, my team found from SWID
mode["private"] = True
r = c.put("/api/fantasy/connect", json={"league_id": "https://fantasy.espn.com/football/league?leagueId=12345&seasonId=2026", "season": 2026})
assert r.status_code == 401 and "private" in r.get_json()["error"]
r = c.put("/api/fantasy/connect", json={"league_id": "12345", "season": 2026, "espn_s2": S2, "swid": SWID.strip("{}").lower()})
j = r.get_json()
assert r.status_code == 200 and j["connected"] and j["has_cookies"] and j["team_id"] == 1 and j["league_name"] == "Sample League", j
assert "espn_s2" not in j and "swid" not in j and S2 not in json.dumps(j)
stored = DATA["users/dev/integrations/espn_fantasy"]["leagues"][0]
assert stored["espn_s2"] == S2 and stored["swid"] == SWID and DATA["users/dev/integrations/espn_fantasy"]["active"] == "12345"
assert calls[-1][1]["view"] == fantasy.VIEWS
assert S2 not in json.dumps(c.get("/api/fantasy").get_json())

# reconnect without retyping the cookies keeps them
r = c.put("/api/fantasy/connect", json={"league_id": "12345", "season": 2026}); assert r.status_code == 200 and r.get_json()["has_cookies"]

# 4. league summary
r = c.get("/api/fantasy/league").get_json()
L = r["league"]
assert L["name"] == "Sample League" and L["week"] == 10 and L["regular_season_weeks"] == 14 and L["playoff_teams"] == 2 and not L["in_playoffs"]
assert [t["id"] for t in r["teams"]] == [2, 1, 3, 4]
me = next(t for t in r["teams"] if t["id"] == 1)
assert me["name"] == "South Sharks" and me["owners"] == ["me_user"] and me["pf"] == 1001.46 and me["streak"] == "W2" and me["playoff_pct"] == 73.4
assert me["logo"] == "https://example.com/l.png" and r["teams"][0]["logo"] == "" and r["teams"][0]["owners"] == ["Pat Doe"]
ro = r["roster"]
assert [p["name"] for p in ro] == ["Qb Prime", "Run Fast", "Flex Wide", "Bench Guy", "Hurt Back"], [p["name"] for p in ro]
qb = ro[0]; assert qb["slot"] == "QB" and qb["pro"] == "BUF" and qb["points"] == 24.36 and qb["projected"] == 21.0 and qb["starter"]
assert ro[1]["injury"] == "QUESTIONABLE" and ro[2]["slot"] == "FLEX" and ro[3]["slot"] == "Bench" and not ro[3]["starter"] and ro[4]["slot"] == "IR"
assert len(r["this_week"]) == 2
m = next(x for x in r["this_week"] if x["id"] == 2)
assert m["away"] == {"team_id": 1, "points": 62.0, "projected": 121.9} and m["home"]["points"] == 55.25 and m["winner"] == ""
assert [x["week"] for x in r["my_schedule"]] == [9, 10, 15] and r["my_schedule"][0]["winner"] == "home" and r["my_schedule"][2]["playoff"]

# 5. pick a different team
assert c.put("/api/fantasy/team", json={"team_id": "2"}).status_code == 400
assert c.put("/api/fantasy/team", json={"team_id": 2}).get_json()["team_id"] == 2
assert c.get("/api/fantasy/league").get_json()["my_team_id"] == 2

# 6. ESPN down: cached league still answers; cold cache -> 502
mode["down"] = True
assert c.get("/api/fantasy/league").status_code == 200
fantasy._cache.clear()
r = c.get("/api/fantasy/league"); assert r.status_code == 502 and "ESPN" in r.get_json()["error"]
mode["down"] = False

# 7. disconnect forgets the cookies
assert c.delete("/api/fantasy/connect").get_json()["connected"] is False
assert "users/dev/integrations/espn_fantasy" not in DATA

# 8. old one-league doc still reads; a second league reuses the saved cookies; switch; remove one
DATA["users/dev/integrations/espn_fantasy"] = {"league_id": "12345", "season": 2026, "espn_s2": S2, "swid": SWID, "team_id": 1, "league_name": "Sample League"}
st = c.get("/api/fantasy").get_json()
assert st["connected"] and st["league_id"] == "12345" and st["has_cookies"] and len(st["leagues"]) == 1
SECOND = json.loads(json.dumps(LEAGUE)); SECOND["settings"]["name"] = "Work League"
_orig = fake_get
def fake_get2(url, params=None, cookies=None, **k):
    if "/leagues/77777" in url:
        calls.append((url, params, cookies))
        return R(200, SECOND) if cookies and cookies.get("SWID") == SWID else R(401, {})
    return _orig(url, params=params, cookies=cookies, **k)
fantasy.requests = types.SimpleNamespace(get=fake_get2)
r = c.put("/api/fantasy/connect", json={"league_id": "77777", "season": 2026}).get_json()
assert r["active"] == "77777" and [l["league_id"] for l in r["leagues"]] == ["12345", "77777"] and r["league_name"] == "Work League", r
assert r["team_id"] == 1 and r["leagues"][1]["has_cookies"]
assert c.get("/api/fantasy/league").get_json()["league"]["name"] == "Work League"
assert c.get("/api/fantasy/league?league_id=12345").get_json()["league"]["name"] == "Sample League"
assert c.put("/api/fantasy/active", json={"league_id": "999"}).status_code == 404
assert c.put("/api/fantasy/active", json={"league_id": "12345"}).get_json()["active"] == "12345"
assert c.put("/api/fantasy/team", json={"team_id": 2, "league_id": "77777"}).get_json()["team_id"] == 2
assert c.get("/api/fantasy/league?league_id=77777").get_json()["my_team_id"] == 2
assert c.get("/api/fantasy/league").get_json()["my_team_id"] == 1
r = c.delete("/api/fantasy/connect?league_id=12345").get_json()
assert [l["league_id"] for l in r["leagues"]] == ["77777"] and r["active"] == "77777"
assert c.delete("/api/fantasy/connect").get_json()["connected"] is False

# 9. trade suggestions (FantasyCalc values, made-up players)
import fantasy_trades as ft
assert ft.league_format({"rosterSettings": {"lineupSlotCounts": {"0": 1, "2": 2, "4": 2, "6": 1, "23": 1, "20": 6, "16": 1}},
                         "scoringSettings": {"scoringItems": [{"statId": 53, "points": 0.5}]}}) == (1, 0.5, {0: 1, 2: 2, 4: 2, 6: 1, 23: 1})
assert ft.league_format({"rosterSettings": {"lineupSlotCounts": {"0": 1, "7": 1}}, "scoringSettings": {}})[0] == 2
assert ft.norm_name("Marvin Harrison Jr.") == ft.norm_name("marvin harrison")
P = lambda i, n, pos, v: {"id": i, "name": n, "pos": pos, "slot": "Bench", "value": v, "priced": True}
mine = [P(1, "Qb A", "QB", 5000), P(2, "Wr One", "WR", 7000), P(3, "Wr Two", "WR", 6800), P(4, "Wr Three", "WR", 6500),
        P(8, "Wr Four", "WR", 6200), P(5, "Rb Weak", "RB", 1500), P(6, "Rb Weaker", "RB", 1200), P(7, "Te A", "TE", 3000)]
them = [P(11, "Qb B", "QB", 5200), P(12, "Rb Star", "RB", 6600), P(13, "Rb Two", "RB", 6000), P(14, "Rb Three", "RB", 5500),
        P(15, "Wr Weak", "WR", 1400), P(16, "Wr Weaker", "WR", 1100), P(17, "Te B", "TE", 2900)]
slots = {0: 1, 2: 2, 4: 2, 6: 1, 23: 1}
st_, bench = ft.best_lineup(mine, slots)
assert {p["id"] for p in st_} == {1, 2, 3, 4, 5, 6, 7} and [p["id"] for p in bench] == [8]
ev = ft.evaluate(mine, them, {8}, {14}, slots)
assert ev["fair"] and ev["value_give"] == 6200 and ev["value_get"] == 5500 and ev["my_gain"] > 0 and ev["their_gain"] > 0, ev
sug = ft.suggest(mine, {9: them}, slots)
assert sug and all(t["fair"] and t["my_gain"] > 0 and t["their_gain"] >= 0 for t in sug) and sug[0]["team_id"] == 9
assert any(p["pos"] == "RB" for p in sug[0]["get"]) and any(p["pos"] == "WR" for p in sug[0]["give"]), sug[0]
assert len(sug) <= 3 and sug[0]["why"]

# through the API: FantasyCalc rows matched by ESPN id, then by name + position; K and D/ST ignored
FC = [{"player": {"name": "Qb Prime", "position": "QB", "espnId": "3"}, "value": 6000, "overallRank": 10, "trend30Day": 50},
      {"player": {"name": "Run Fast", "position": "RB", "espnId": "999999"}, "value": 4000, "overallRank": 30},
      {"player": {"name": "Flex Wide", "position": "WR", "espnId": "5"}, "value": 5200, "overallRank": 20}]
fc_calls = []
def fake_get3(url, params=None, cookies=None, **k):
    if url == ft.FC_URL:
        fc_calls.append(params)
        return R(200, FC)
    return fake_get2(url, params=params, cookies=cookies, **k)
class R3(R):
    def raise_for_status(s): pass
fantasy.requests = types.SimpleNamespace(get=fake_get3)
ft.requests = types.SimpleNamespace(get=lambda url, params=None, **k: (fc_calls.append(params), R3(200, FC))[1])
c.put("/api/fantasy/connect", json={"league_id": "12345", "season": 2026, "espn_s2": S2, "swid": SWID})
r = c.get("/api/fantasy/trades").get_json()
assert fc_calls[-1] == {"isDynasty": "false", "numQbs": 1, "numTeams": 8, "ppr": 0}, fc_calls[-1]
mr = {p["name"]: p for p in r["rosters"]["1"]}
assert mr["Qb Prime"]["value"] == 6000 and mr["Run Fast"]["value"] == 4000 and mr["Flex Wide"]["value"] == 5200 and not mr["Bench Guy"]["priced"]
assert "Bench Guy" in r["unpriced"] and r["format"]["slots"] == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1}
assert isinstance(r["trades"], list) and len(r["teams"]) == 4
bad = c.post("/api/fantasy/trades", json={"partner": 1, "give": [3], "get": []}); assert bad.status_code == 400
ok = c.post("/api/fantasy/trades", json={"partner": 2, "give": [3], "get": []}).get_json()
assert ok["trade"]["value_give"] == 6000 and ok["trade"]["value_get"] == 0 and ok["trade"]["my_gain"] < 0 and ok["trade"]["team_id"] == 2
ft._cache.clear(); ft.requests = types.SimpleNamespace(get=lambda *a, **k: (_ for _ in ()).throw(ConnectionError("x")))
r = c.get("/api/fantasy/trades"); assert r.status_code == 502 and "FantasyCalc" in r.get_json()["error"]

print("fantasy: connect (public/private), summary, roster, matchups, team pick, disconnect: OK")

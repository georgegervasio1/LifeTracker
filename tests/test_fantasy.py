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
stored = DATA["users/dev/integrations/espn_fantasy"]
assert stored["espn_s2"] == S2 and stored["swid"] == SWID
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

print("fantasy: connect (public/private), summary, roster, matchups, team pick, disconnect: OK")

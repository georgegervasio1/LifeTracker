"""Sports → Fantasy Football: read the user's ESPN fantasy league.

ESPN has no official fantasy API. The site itself (and tools like FantasyPros or Sleeper's importer) reads the
same JSON endpoint used here: lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/<year>/segments/0/leagues/<id>.
Public leagues need only the league id. Private leagues need the two cookies ESPN sets after sign-in, espn_s2 and
SWID; ESPN's sign-in itself (Disney OneID with bot checks) can't be automated, so the user copies them from the
browser once. They are stored in Firestore under the user and never sent back to the browser.

Per user:
  integrations/espn_fantasy -> {league_id, season, espn_s2, swid, team_id}

Routes:
  GET    /api/fantasy            -> connection status (no cookies)
  PUT    /api/fantasy/connect    -> {league_id, season?, espn_s2?, swid?} test the league and save; picks "my team" from SWID
  PUT    /api/fantasy/team       -> {team_id} which team is mine
  DELETE /api/fantasy/connect    -> forget the league and cookies
  GET    /api/fantasy/league     -> league, standings, this week's matchups, my roster
League-mate names and rosters are read at request time and never stored or committed.
"""
import re
import threading
import time
from datetime import date

import requests
from flask import Blueprint, g, jsonify, request

bp = Blueprint("fantasy", __name__)
db = firestore = require_owner = user_ref = None

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league}"
VIEWS = ["mTeam", "mRoster", "mMatchup", "mMatchupScore", "mSettings", "mStandings", "mStatus"]
TTL = 120
_cache, _lock = {}, threading.Lock()

SLOT = {0: "QB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE", 7: "OP", 16: "D/ST", 17: "K", 18: "P", 19: "HC",
        20: "Bench", 21: "IR", 23: "FLEX", 8: "DT", 9: "DE", 10: "LB", 11: "DL", 12: "CB", 13: "S", 14: "DB", 15: "DP"}
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST", 7: "P", 9: "DT", 10: "DE", 11: "LB", 12: "CB", 13: "S", 14: "HC"}
PRO = {0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GB", 10: "TEN",
       11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
       21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX",
       33: "BAL", 34: "HOU"}
STARTER_ORDER = [0, 2, 3, 4, 5, 6, 23, 7, 16, 17, 18, 19, 8, 9, 10, 11, 12, 13, 14, 15]


class FantasyError(Exception):
    def __init__(self, msg, code=502):
        super().__init__(msg)
        self.code = code


def init(app, _db, _firestore, _require_owner, _user_ref):
    global db, firestore, require_owner, user_ref
    db, firestore, require_owner, user_ref = _db, _firestore, _require_owner, _user_ref
    _register_routes()
    app.register_blueprint(bp)


def default_season(today=None):
    """NFL fantasy seasons run Sep-Jan; January and February still belong to last year's season."""
    today = today or date.today()
    return today.year - 1 if today.month <= 2 else today.year


def clean_swid(s):
    s = (s or "").strip().strip("{}").upper()
    return f"{{{s}}}" if re.fullmatch(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}", s) else ""


def clean_s2(s):
    s = re.sub(r"\s+", "", s or "")
    return s if re.fullmatch(r"[A-Za-z0-9%+/=_.\-]{40,2000}", s) else ""


# ---------------------------------------------------------------- ESPN fetch
def fetch_league(league_id, season, s2="", swid="", fresh=False):
    key = (str(league_id), int(season), bool(s2))
    now = time.time()
    with _lock:
        hit = _cache.get(key)
    if hit and hit[0] > now and not fresh:
        return hit[1]
    cookies = {"espn_s2": s2, "SWID": swid} if s2 and swid else None
    try:
        r = requests.get(BASE.format(season=int(season), league=league_id), params={"view": VIEWS}, cookies=cookies,
                         timeout=15, headers={"User-Agent": "LifeTracker/1.0", "Accept": "application/json"})
    except Exception as e:
        if hit:
            return hit[1]
        raise FantasyError(f"ESPN didn't answer ({type(e).__name__}). Try again in a minute.")
    if r.status_code in (401, 403):
        raise FantasyError("ESPN says this league is private. Add your espn_s2 and SWID cookies (see the steps), "
                           "or check that they're from an account in this league." if cookies else
                           "This league is private. Add your espn_s2 and SWID cookies to read it.", 401)
    if r.status_code == 404:
        raise FantasyError(f"ESPN has no league {league_id} for the {season} season. Check the league id and season.", 404)
    if r.status_code >= 400:
        if hit:
            return hit[1]
        raise FantasyError(f"ESPN returned an error ({r.status_code}). Try again in a minute.")
    try:
        data = r.json()
    except ValueError:
        raise FantasyError("ESPN sent something that isn't league data. If the league is private, the cookies may have expired.", 401)
    if not isinstance(data, dict) or "teams" not in data:
        raise FantasyError("ESPN sent something that isn't league data. If the league is private, the cookies may have expired.", 401)
    with _lock:
        if len(_cache) > 200:
            _cache.clear()
        _cache[key] = (now + TTL, data)
    return data


# ---------------------------------------------------------------- normalize
def _r2(x):
    return round(float(x), 2) if isinstance(x, (int, float)) else None


def _team_name(t):
    return t.get("name") or " ".join(x for x in (t.get("location"), t.get("nickname")) if x) or t.get("abbrev") or f"Team {t.get('id')}"


def _player_points(player, period, season):
    """(actual, projected) fantasy points for one scoring period."""
    act = proj = None
    for s in player.get("stats") or []:
        if s.get("scoringPeriodId") != period or (s.get("seasonId") not in (None, season)) or s.get("statSplitTypeId") == 2:
            continue
        if s.get("statSourceId") == 0:
            act = _r2(s.get("appliedTotal"))
        elif s.get("statSourceId") == 1:
            proj = _r2(s.get("appliedTotal"))
    return act, proj


def norm_roster(team, period, season):
    out = []
    for e in (team.get("roster") or {}).get("entries") or []:
        pe = e.get("playerPoolEntry") or {}
        p = pe.get("player") or {}
        act, proj = _player_points(p, period, season)
        slot = e.get("lineupSlotId")
        out.append({"id": e.get("playerId") or p.get("id"), "name": p.get("fullName") or "",
                    "pos": POS.get(p.get("defaultPositionId"), ""), "pro": PRO.get(p.get("proTeamId"), ""),
                    "slot": SLOT.get(slot, ""), "slot_id": slot, "starter": slot not in (20, 21),
                    "injury": (p.get("injuryStatus") or "") if p.get("injuryStatus") not in (None, "ACTIVE", "NORMAL") else "",
                    "points": act, "projected": proj,
                    "season_points": _r2(pe.get("appliedStatTotal")),
                    "owned": _r2((p.get("ownership") or {}).get("percentOwned"))})
    order = {s: i for i, s in enumerate(STARTER_ORDER)}
    out.sort(key=lambda x: (not x["starter"], x["slot_id"] == 21, order.get(x["slot_id"], 50), -(x["projected"] or 0)))
    return out


def _side(s):
    if not s:
        return None
    pts = s.get("totalPointsLive", s.get("totalPoints"))
    return {"team_id": s.get("teamId"), "points": _r2(pts),
            "projected": _r2(s.get("totalProjectedPointsLive", s.get("totalProjectedPoints")))}


def summarize(data, my_team_id=None, swid=""):
    season = data.get("seasonId")
    status = data.get("status") or {}
    settings = data.get("settings") or {}
    sched = settings.get("scheduleSettings") or {}
    week = status.get("currentMatchupPeriod") or data.get("scoringPeriodId")
    period = data.get("scoringPeriodId") or week
    members = {m.get("id"): (m.get("displayName") or " ".join(x for x in (m.get("firstName"), m.get("lastName")) if x))
               for m in data.get("members") or []}
    divisions = {d.get("id"): d.get("name") for d in sched.get("divisions") or []}
    teams = []
    for t in data.get("teams") or []:
        rec = (t.get("record") or {}).get("overall") or {}
        owners = t.get("owners") or ([t["primaryOwner"]] if t.get("primaryOwner") else [])
        teams.append({"id": t.get("id"), "name": _team_name(t), "abbrev": t.get("abbrev") or "",
                      "logo": t.get("logo") if str(t.get("logo") or "").startswith("https://") else "",
                      "owners": [members.get(o, "") for o in owners if members.get(o)],
                      "wins": rec.get("wins", 0), "losses": rec.get("losses", 0), "ties": rec.get("ties", 0),
                      "pf": _r2(rec.get("pointsFor")), "pa": _r2(rec.get("pointsAgainst")),
                      "streak": f"{(rec.get('streakType') or '')[:1]}{rec.get('streakLength') or ''}" if rec.get("streakLength") else "",
                      "seed": t.get("playoffSeed"), "division": divisions.get(t.get("divisionId"), ""),
                      "playoff_pct": _r2(((t.get("currentSimulationResults") or {}).get("playoffPct") or 0) * 100)
                                     if t.get("currentSimulationResults") else None,
                      "_owners": owners, "_raw": t})
    if my_team_id is None and swid:
        mine = next((t for t in teams if swid in t["_owners"]), None)
        my_team_id = mine["id"] if mine else None
    teams.sort(key=lambda t: (t["seed"] or 99, -(t["wins"] - t["losses"]), -(t["pf"] or 0)))
    by_id = {t["id"]: t for t in teams}

    def matchup(m):
        h, a = _side(m.get("home")), _side(m.get("away"))
        return {"id": m.get("id"), "week": m.get("matchupPeriodId"), "home": h, "away": a,
                "winner": {"HOME": "home", "AWAY": "away", "TIE": "tie"}.get(m.get("winner"), ""),
                "playoff": (m.get("playoffTierType") or "NONE") != "NONE"}
    all_m = [matchup(m) for m in data.get("schedule") or []]
    this_week = [m for m in all_m if m["week"] == week]
    my_games = sorted([m for m in all_m if my_team_id is not None and my_team_id in
                ((m["home"] or {}).get("team_id"), (m["away"] or {}).get("team_id"))], key=lambda m: m["week"] or 0)
    my = by_id.get(my_team_id)
    roster = norm_roster(my["_raw"], period, season) if my else []
    for t in teams:
        t.pop("_raw"); t.pop("_owners")
    reg_weeks = sched.get("matchupPeriodCount")
    return {
        "league": {"name": settings.get("name") or "", "season": season, "size": settings.get("size") or len(teams),
                   "week": week, "scoring_period": period, "regular_season_weeks": reg_weeks,
                   "playoff_teams": sched.get("playoffTeamCount"),
                   "final_week": status.get("finalScoringPeriod"),
                   "in_playoffs": bool(reg_weeks and week and week > reg_weeks),
                   "scoring": (settings.get("scoringSettings") or {}).get("scoringType") or "",
                   "faab": ((settings.get("acquisitionSettings") or {}).get("isUsingAcquisitionBudget")),
                   "trade_deadline": (settings.get("tradeSettings") or {}).get("deadlineDate")},
        "teams": teams, "my_team_id": my_team_id, "roster": roster,
        "this_week": this_week, "my_schedule": my_games,
    }


# ---------------------------------------------------------------- storage
def _ref():
    return user_ref().collection("integrations").document("espn_fantasy")


def get_conn():
    s = _ref().get()
    return (s.to_dict() or {}) if s.exists else {}


def public_conn(c):
    return {"connected": bool(c.get("league_id")), "league_id": c.get("league_id") or "",
            "season": c.get("season") or default_season(), "team_id": c.get("team_id"),
            "has_cookies": bool(c.get("espn_s2") and c.get("swid")), "league_name": c.get("league_name") or ""}


def _register_routes():
    def fail(e):
        return jsonify(error=str(e)), e.code if e.code in (401, 404) else 502

    @bp.get("/api/fantasy")
    @require_owner
    def fantasy_status():
        return jsonify(public_conn(get_conn()))

    @bp.put("/api/fantasy/connect")
    @require_owner
    def fantasy_connect():
        b = request.get_json(silent=True) or {}
        old = get_conn()
        league = re.sub(r"\D", "", str(b.get("league_id") or ""))
        m = re.search(r"leagueId=(\d+)", str(b.get("league_id") or ""))   # a pasted league URL works too
        if m:
            league = m.group(1)
        if not re.fullmatch(r"\d{3,12}", league):
            return jsonify(error="Enter the league id (the number after leagueId= in your league's URL)."), 400
        season = int(b.get("season") or default_season())
        if not 2018 <= season <= date.today().year + 1:
            return jsonify(error="Pick a season from 2018 on."), 400
        s2_in, swid_in = str(b.get("espn_s2") or "").strip(), str(b.get("swid") or "").strip()
        s2 = clean_s2(s2_in) if s2_in else (old.get("espn_s2", "") if old.get("league_id") == league else "")
        swid = clean_swid(swid_in) if swid_in else (old.get("swid", "") if old.get("league_id") == league else "")
        if s2_in and not s2:
            return jsonify(error="That espn_s2 value doesn't look right. Copy the whole cookie value."), 400
        if swid_in and not swid:
            return jsonify(error="SWID should look like {XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}."), 400
        if bool(s2) != bool(swid):
            return jsonify(error="Add both cookies, espn_s2 and SWID, or neither for a public league."), 400
        try:
            data = fetch_league(league, season, s2, swid, fresh=True)
        except FantasyError as e:
            return fail(e)
        out = summarize(data, None, swid)
        team_id = out["my_team_id"] if out["my_team_id"] is not None else (old.get("team_id") if old.get("league_id") == league else None)
        _ref().set({"league_id": league, "season": season, "espn_s2": s2, "swid": swid, "team_id": team_id,
                    "league_name": out["league"]["name"][:80], "updated": firestore.SERVER_TIMESTAMP})
        return jsonify(**public_conn({"league_id": league, "season": season, "espn_s2": s2, "swid": swid,
                                      "team_id": team_id, "league_name": out["league"]["name"]}),
                       teams=[{"id": t["id"], "name": t["name"]} for t in out["teams"]])

    @bp.put("/api/fantasy/team")
    @require_owner
    def fantasy_team():
        c = get_conn()
        if not c.get("league_id"):
            return jsonify(error="Connect a league first."), 400
        tid = (request.get_json(silent=True) or {}).get("team_id")
        if not isinstance(tid, int):
            return jsonify(error="team_id must be a number"), 400
        _ref().set({"team_id": tid}, merge=True)
        return jsonify(public_conn({**c, "team_id": tid}))

    @bp.delete("/api/fantasy/connect")
    @require_owner
    def fantasy_disconnect():
        _ref().delete()
        return jsonify(public_conn({}))

    @bp.get("/api/fantasy/league")
    @require_owner
    def fantasy_league():
        c = get_conn()
        if not c.get("league_id"):
            return jsonify(error="Connect your ESPN league first.", connect=True), 400
        try:
            data = fetch_league(c["league_id"], c.get("season") or default_season(), c.get("espn_s2", ""), c.get("swid", ""),
                                fresh=request.args.get("fresh") == "1")
        except FantasyError as e:
            return fail(e)
        out = summarize(data, c.get("team_id"), c.get("swid", ""))
        return jsonify(conn=public_conn(c), **out)

"""Sports: favorite teams, their schedules (with venues and times), records and standings.

Data comes from ESPN's public site API (site.api.espn.com). It is unofficial and needs no key or login;
responses are cached in memory so one page load doesn't fan out into dozens of requests.

Per user (users/{uid}/...):
  sports/favorites -> {teams: [{league, id, name, short, abbr, color, logo}]}  (starred teams, in the user's order)

Routes:
  GET  /api/sports                       -> leagues + favorites
  GET  /api/sports/teams?league=nfl      -> every team in a league (for search and starring)
  PUT  /api/sports/favorites             -> {teams: [{league, id}, ...]} replaces the starred list
  GET  /api/sports/team/<league>/<id>    -> team, record, standing and season schedule
  GET  /api/sports/schedule              -> every favorite's games merged (calendar), plus each team's record
  GET  /api/sports/standings/<league>    -> standings grouped by conference / division
  GET  /api/sports/calendar.ics?teams=nfl:13,nba:5   (public, no sign-in: public schedule data only)
       Downloadable, and subscribable from Apple / Google Calendar (webcal://). The URL holds team ids only.
"""
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from flask import Blueprint, Response, g, jsonify, request

bp = Blueprint("sports", __name__)
db = firestore = require_owner = user_ref = None

SITE = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}"
STANDINGS = "https://site.api.espn.com/apis/v2/sports/{sport}/{league}/standings"
ET = ZoneInfo("America/New_York")

# key -> (ESPN sport, ESPN league, label, typical game length in hours for calendar events)
LEAGUES = {
    "nfl":  ("football", "nfl", "NFL", 3.5),
    "nba":  ("basketball", "nba", "NBA", 2.5),
    "mlb":  ("baseball", "mlb", "MLB", 3.0),
    "nhl":  ("hockey", "nhl", "NHL", 2.5),
    "wnba": ("basketball", "wnba", "WNBA", 2.0),
    "mls":  ("soccer", "usa.1", "MLS", 2.0),
    "epl":  ("soccer", "eng.1", "Premier League", 2.0),
    "cfb":  ("football", "college-football", "College Football", 3.5),
    "mcbb": ("basketball", "mens-college-basketball", "Men's College Basketball", 2.0),
    "wcbb": ("basketball", "womens-college-basketball", "Women's College Basketball", 2.0),
}
MAX_FAVORITES = 40
TTL_TEAMS, TTL_SCHEDULE, TTL_LIVE, TTL_STANDINGS = 24 * 3600, 30 * 60, 2 * 60, 60 * 60
_cache, _lock = {}, threading.Lock()


class Upstream(Exception):
    pass


def init(app, _db, _firestore, _require_owner, _user_ref):
    global db, firestore, require_owner, user_ref
    db, firestore, require_owner, user_ref = _db, _firestore, _require_owner, _user_ref
    _register_routes()
    app.register_blueprint(bp)


# ---------------------------------------------------------------- ESPN fetch + cache
def _get(url, ttl, params=None):
    key = (url, tuple(sorted((params or {}).items())))
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    try:
        r = requests.get(url, params=params, timeout=12, headers={"User-Agent": "LifeTracker/1.0"})
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        if hit:                       # stale beats nothing when ESPN hiccups
            return hit[1]
        raise Upstream(f"ESPN didn't answer ({type(e).__name__}). Try again in a minute.")
    with _lock:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[key] = (now + ttl, data)
    return data


def _logo(t):
    logos = t.get("logos") or []
    if logos and isinstance(logos[0], dict):
        return logos[0].get("href") or ""
    return t.get("logo") or ""


def _color(c):
    c = (c or "").strip().lstrip("#")
    return f"#{c}" if re.fullmatch(r"[0-9a-fA-F]{6}", c) else ""


def team_brief(t, league):
    """ESPN team object -> the small dict the app uses everywhere."""
    t = t or {}
    return {"league": league, "id": str(t.get("id") or ""),
            "name": t.get("displayName") or " ".join(x for x in (t.get("location"), t.get("name")) if x) or "",
            "short": t.get("shortDisplayName") or t.get("name") or t.get("abbreviation") or "",
            "abbr": t.get("abbreviation") or "", "color": _color(t.get("color")),
            "logo": _logo(t)}


def league_teams(league):
    sport, lg, _, _ = LEAGUES[league]
    data = _get(SITE.format(sport=sport, league=lg) + "/teams", TTL_TEAMS, {"limit": 1000})
    out = []
    for s in data.get("sports") or []:
        for l in s.get("leagues") or []:
            for item in l.get("teams") or []:
                b = team_brief(item.get("team") or item, league)
                if b["id"]:
                    out.append(b)
    out.sort(key=lambda t: t["name"])
    return out


# ---------------------------------------------------------------- normalize games
def _score(v):
    if isinstance(v, dict):
        v = v.get("displayValue", v.get("value"))
    if v in (None, ""):
        return None
    try:
        f = float(v)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return None


def _side(c, league):
    t = team_brief(c.get("team"), league)
    rec = c.get("record") or c.get("records") or []
    rec = next((r.get("summary") or r.get("displayValue") for r in rec if isinstance(r, dict)
                and (r.get("type") in (None, "total", "overall") or r.get("name") == "overall")), None) if isinstance(rec, list) else None
    return {**t, "score": _score(c.get("score")), "winner": c.get("winner"), "record": rec,
            "rank": (c.get("curatedRank") or {}).get("current") if (c.get("curatedRank") or {}).get("current", 99) < 99 else None}


def norm_game(ev, league):
    comp = (ev.get("competitions") or [{}])[0]
    sides = {c.get("homeAway"): _side(c, league) for c in comp.get("competitors") or []}
    if "home" not in sides or "away" not in sides:
        return None
    st = ((comp.get("status") or ev.get("status") or {}).get("type") or {})
    venue = comp.get("venue") or {}
    addr = venue.get("address") or {}
    tv = []
    for b in comp.get("broadcasts") or []:
        name = (b.get("media") or {}).get("shortName") or ", ".join(b.get("names") or [])
        if name and name not in tv:
            tv.append(name)
    for b in comp.get("geoBroadcasts") or []:
        name = (b.get("media") or {}).get("shortName")
        if name and name not in tv and (b.get("market") or {}).get("type") != "Away":
            tv.append(name)
    when = comp.get("date") or ev.get("date") or ""
    return {
        "id": str(ev.get("id") or comp.get("id") or ""), "league": league, "date": when,
        "tbd": comp.get("timeValid", ev.get("timeValid")) is False,
        "name": ev.get("name") or "", "short": ev.get("shortName") or "",
        "season_type": (ev.get("seasonType") or {}).get("name") or "",
        "week": (ev.get("week") or {}).get("text") or "",
        "home": sides["home"], "away": sides["away"], "neutral": bool(comp.get("neutralSite")),
        "venue": {"name": venue.get("fullName") or "", "city": addr.get("city") or "", "state": addr.get("state") or addr.get("country") or ""},
        "state": st.get("state") or "pre",             # pre | in | post
        "completed": bool(st.get("completed")),
        "detail": st.get("shortDetail") or st.get("detail") or st.get("description") or "",
        "status": st.get("description") or "",
        "tv": ", ".join(tv[:3]),
    }


def _record_from_games(games, team_id):
    w = l = t = 0
    for gm in games:
        if not gm["completed"] or gm["season_type"].lower().startswith("pre"):
            continue
        me, op = (gm["home"], gm["away"]) if gm["home"]["id"] == team_id else (gm["away"], gm["home"])
        if me["winner"] is True: w += 1
        elif op["winner"] is True: l += 1
        elif me["score"] is not None and me["score"] == op["score"]: t += 1
    return f"{w}-{l}" + (f"-{t}" if t else "") if (w or l or t) else ""


def team_schedule(league, team_id, season=None):
    sport, lg, _, _ = LEAGUES[league]
    url = SITE.format(sport=sport, league=lg) + f"/teams/{team_id}/schedule"
    params = {"season": season} if season else None
    data = _get(url, TTL_SCHEDULE, params)
    games = [x for x in (norm_game(e, league) for e in data.get("events") or []) if x]
    today = datetime.now(timezone.utc).date().isoformat()
    if any(gm["state"] == "in" or (gm["date"][:10] == today and not gm["completed"]) for gm in games):
        with _lock:                    # a game today or in progress: refresh scores sooner
            k = (url, tuple(sorted((params or {}).items())))
            if k in _cache:
                _cache[k] = (min(_cache[k][0], time.time() + TTL_LIVE), _cache[k][1])
    t = data.get("team") or {}
    team = team_brief(t, league)
    team["record"] = t.get("recordSummary") or _record_from_games(games, team["id"])
    team["standing"] = t.get("standingSummary") or ""
    season_info = data.get("requestedSeason") or data.get("season") or {}
    games.sort(key=lambda gm: gm["date"])
    return {"team": team, "season": season_info.get("displayName") or season_info.get("year") or "",
            "games": games}


# ---------------------------------------------------------------- standings
STAT_COLS = [  # (ESPN stat name, column label)
    ("wins", "W"), ("losses", "L"), ("ties", "T"), ("otLosses", "OTL"), ("overtimeLosses", "OTL"),
    ("winPercent", "PCT"), ("points", "PTS"), ("gamesBehind", "GB"), ("streak", "STRK"),
]


def _entries(node, path, out):
    name = node.get("name") or node.get("abbreviation") or ""
    entries = (node.get("standings") or {}).get("entries") or []
    if entries:
        out.append({"name": name or " · ".join(path), "entries": entries})
    for ch in node.get("children") or []:
        _entries(ch, path + [name], out)


def standings(league):
    sport, lg, _, _ = LEAGUES[league]
    data = _get(STANDINGS.format(sport=sport, league=lg), TTL_STANDINGS)
    groups = []
    _entries(data, [], groups)
    out, cols_seen = [], []
    for grp in groups:
        rows = []
        for e in grp["entries"]:
            stats = {s.get("name"): s for s in e.get("stats") or [] if s.get("name")}
            row = {**team_brief(e.get("team"), league), "stats": {}}
            for key, label in STAT_COLS:
                s = stats.get(key)
                if s is not None and label not in row["stats"]:
                    row["stats"][label] = s.get("displayValue") if s.get("displayValue") is not None else s.get("value")
                    if label not in cols_seen:
                        cols_seen.append(label)
            seed = stats.get("playoffSeed") or {}
            row["seed"] = seed.get("value")
            num = lambda k: (stats.get(k) or {}).get("value") or 0
            row["_sort"] = (seed.get("value") or 99, -num("winPercent"), -num("points"))
            rows.append(row)
        rows.sort(key=lambda r: r.pop("_sort"))
        out.append({"name": grp["name"], "teams": rows})
    cols = [label for _, label in STAT_COLS if label in cols_seen]
    cols = list(dict.fromkeys(cols))
    season = next((((c.get("standings") or {}).get("seasonDisplayName")) for c in [data] + (data.get("children") or [])
                   if (c.get("standings") or {}).get("seasonDisplayName")), "")
    return {"league": league, "label": LEAGUES[league][2], "season": season,
            "columns": cols, "groups": out}


# ---------------------------------------------------------------- favorites
def _fav_ref():
    return user_ref().collection("sports").document("favorites")


def get_favorites():
    snap = _fav_ref().get()
    return (snap.to_dict() or {}).get("teams", []) if snap.exists else []


def _clean_fav(t):
    league, tid = str(t.get("league") or ""), str(t.get("id") or "")
    if league not in LEAGUES or not re.fullmatch(r"\d{1,8}", tid):
        return None
    clip = lambda v, n: str(v or "")[:n]
    logo = clip(t.get("logo"), 300)
    return {"league": league, "id": tid, "name": clip(t.get("name"), 80), "short": clip(t.get("short"), 40),
            "abbr": clip(t.get("abbr"), 8), "color": _color(t.get("color")),
            "logo": logo if logo.startswith("https://") else ""}


def merged_schedule(teams):
    """Games for a list of {league, id}, one row per game even when two favorites play each other."""
    games, records, errors = {}, [], []
    for f in teams:
        try:
            s = team_schedule(f["league"], f["id"])
        except Upstream as e:
            errors.append({"team": f.get("name") or f["id"], "error": str(e)})
            continue
        records.append({**f, **{k: v for k, v in s["team"].items() if v}})
        for gm in s["games"]:
            key = f"{gm['league']}:{gm['id']}"
            row = games.setdefault(key, {**gm, "favorites": []})
            row["favorites"].append(f"{f['league']}:{f['id']}")
    return sorted(games.values(), key=lambda gm: gm["date"]), records, errors


# ---------------------------------------------------------------- iCalendar
def _ics_text(s):
    return str(s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line):
    b = line.encode("utf-8")
    if len(b) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        e = ch.encode("utf-8")
        if len(cur) + len(e) > (75 if not parts else 74):
            parts.append(cur.decode("utf-8")); cur = b""
        cur += e
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def _parse_when(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def game_title(gm):
    a, h = gm["away"], gm["home"]
    sep = "vs" if gm["neutral"] else "at"
    title = f"{a['name']} {sep} {h['name']}"
    if gm["completed"] and a["score"] is not None and h["score"] is not None:
        title += f" ({a['abbr'] or a['short']} {a['score']}, {h['abbr'] or h['short']} {h['score']})"
    return title


def build_ics(games, cal_name, now=None):
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//LifeTracker//Sports//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_ics_text(cal_name)}", "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
             "X-PUBLISHED-TTL:PT6H"]
    for gm in games:
        start = _parse_when(gm["date"])
        if not start:
            continue
        hours = LEAGUES[gm["league"]][3]
        lines += ["BEGIN:VEVENT", f"UID:{gm['league']}-{gm['id']}@lifetracker", f"DTSTAMP:{stamp}"]
        if gm["tbd"]:
            day = start.astimezone(ET).date()
            lines += [f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}"]
        else:
            lines += [f"DTSTART:{start:%Y%m%dT%H%M%SZ}", f"DTEND:{start + timedelta(hours=hours):%Y%m%dT%H%M%SZ}"]
        v = gm["venue"]
        loc = ", ".join(x for x in (v["name"], v["city"], v["state"]) if x)
        desc = [LEAGUES[gm["league"]][2] + (f" · {gm['week']}" if gm["week"] else "")]
        if gm["tv"]: desc.append(f"TV: {gm['tv']}")
        if gm["tbd"]: desc.append("Start time not announced yet.")
        if gm["completed"]: desc.append(gm["detail"] or "Final")
        else: desc.append(f"End time is an estimate ({hours:g} h).")
        lines.append(f"SUMMARY:{_ics_text(game_title(gm))}")
        if loc: lines.append(f"LOCATION:{_ics_text(loc)}")
        lines.append(f"DESCRIPTION:{_ics_text(chr(10).join(desc))}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


def parse_team_list(s):
    out = []
    for part in (s or "").split(","):
        m = re.fullmatch(r"([a-z]{2,5}):(\d{1,8})", part.strip())
        if m and m.group(1) in LEAGUES and {"league": m.group(1), "id": m.group(2)} not in out:
            out.append({"league": m.group(1), "id": m.group(2)})
    return out[:MAX_FAVORITES]


# ---------------------------------------------------------------- routes
def _league_arg(league):
    if league not in LEAGUES:
        return None, (jsonify(error="unknown league"), 400)
    return league, None


def _register_routes():
    @bp.get("/api/sports")
    @require_owner
    def sports_home():
        return jsonify(leagues=[{"key": k, "label": v[2]} for k, v in LEAGUES.items()], favorites=get_favorites())

    @bp.get("/api/sports/teams")
    @require_owner
    def sports_teams():
        league, err = _league_arg(request.args.get("league", ""))
        if err: return err
        try:
            return jsonify(league=league, teams=league_teams(league))
        except Upstream as e:
            return jsonify(error=str(e)), 502

    @bp.put("/api/sports/favorites")
    @require_owner
    def sports_set_favorites():
        body = request.get_json(silent=True) or {}
        teams, seen = [], set()
        for t in body.get("teams") or []:
            c = _clean_fav(t if isinstance(t, dict) else {})
            if c and (c["league"], c["id"]) not in seen:
                seen.add((c["league"], c["id"])); teams.append(c)
        if len(teams) > MAX_FAVORITES:
            return jsonify(error=f"Up to {MAX_FAVORITES} favorite teams."), 400
        _fav_ref().set({"teams": teams, "updated": firestore.SERVER_TIMESTAMP})
        return jsonify(favorites=teams)

    @bp.get("/api/sports/team/<league>/<team_id>")
    @require_owner
    def sports_team(league, team_id):
        league, err = _league_arg(league)
        if err: return err
        if not re.fullmatch(r"\d{1,8}", team_id):
            return jsonify(error="bad team id"), 400
        season = request.args.get("season", "")
        try:
            return jsonify(team_schedule(league, team_id, season if re.fullmatch(r"\d{4}", season) else None))
        except Upstream as e:
            return jsonify(error=str(e)), 502

    @bp.get("/api/sports/schedule")
    @require_owner
    def sports_schedule():
        favs = get_favorites()
        games, records, errors = merged_schedule(favs)
        return jsonify(favorites=favs, teams=records, games=games, errors=errors)

    @bp.get("/api/sports/standings/<league>")
    @require_owner
    def sports_standings(league):
        league, err = _league_arg(league)
        if err: return err
        try:
            return jsonify(standings(league))
        except Upstream as e:
            return jsonify(error=str(e)), 502

    @bp.get("/api/sports/calendar.ics")
    def sports_ics():          # public on purpose: public schedules only, so calendar apps can subscribe
        teams = parse_team_list(request.args.get("teams", ""))
        if not teams:
            return jsonify(error="add ?teams=league:id,..."), 400
        games, records, _ = merged_schedule(teams)
        names = [r.get("short") or r.get("name") for r in records if r.get("name") or r.get("short")]
        name = names[0] if len(names) == 1 else ("Sports" if not names else "Sports: " + ", ".join(names[:4]) + ("…" if len(names) > 4 else ""))
        body = build_ics(games, name)
        fname = re.sub(r"[^a-z0-9]+", "-", (names[0] if len(names) == 1 else "sports").lower()).strip("-") or "sports"
        dl = request.args.get("download") == "1"
        return Response(body, mimetype="text/calendar", headers={
            "Content-Disposition": f'{"attachment" if dl else "inline"}; filename="{fname}-schedule.ics"',
            "Cache-Control": "public, max-age=1800"})

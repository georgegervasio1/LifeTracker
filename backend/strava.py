"""Running: Strava integration (OAuth 2.0, API v3) and the Running tab.

Data layout (per user, under users/{uid}/):
  integrations/strava       -> tokens, athlete id/name, last_sync
  strava_activities/{id}    -> one doc per Strava activity (all sports; the UI shows runs).
                               Summary fields come from the activity list; "detail" fields
                               (splits, best efforts, full route) are filled in by later calls.

Strava rate limits are 100 read requests per 15 minutes and 1,000 per day, so a sync pulls the
activity list (200 per page) and then fetches details for at most DETAIL_PER_SYNC runs.
"""
import os
import secrets
import string
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from flask import Blueprint, g, jsonify, redirect, request

bp = Blueprint("strava", __name__)

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_DEAUTH_URL = "https://www.strava.com/oauth/deauthorize"
STRAVA_API = "https://www.strava.com/api/v3"
STRAVA_SCOPES = "read,activity:read_all"
STATE_TTL = 600
DETAIL_PER_SYNC = 30
RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}
MILE = 1609.344
# Strava's best-effort names, shortest first, as shown on the Running tab.
BEST_EFFORTS = ["400m", "1/2 mile", "1K", "1 mile", "2 mile", "5K", "10K", "15K", "10 mile", "20K",
                "Half-Marathon", "30K", "Marathon"]

db = firestore = require_owner = user_ref = None


def init(app, _db, _firestore, _require_owner, _user_ref):
    global db, firestore, require_owner, user_ref
    db, firestore, require_owner, user_ref = _db, _firestore, _require_owner, _user_ref
    _register_routes()
    app.register_blueprint(bp)


def _cfg(name):
    v = os.environ.get(name, "")
    if not v:
        raise RuntimeError(f"{name} is not configured on the server")
    return v


# ---------------------------------------------------------------- OAuth
def _integration():
    return user_ref().collection("integrations").document("strava")


def _activities():
    return user_ref().collection("strava_activities")


def _save_tokens(ref, tok, extra=None):
    data = {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
            "expires_at": int(tok.get("expires_at") or time.time() + int(tok.get("expires_in", 21600)))}
    data.update(extra or {})
    ref.set(data, merge=True)


def _access_token():
    ref = _integration()
    snap = ref.get()
    if not snap.exists:
        raise LookupError("Strava is not connected")
    d = snap.to_dict()
    if d.get("expires_at", 0) > time.time() + 120:
        return d["access_token"]
    r = requests.post(STRAVA_TOKEN_URL, data={
        "client_id": _cfg("STRAVA_CLIENT_ID"), "client_secret": _cfg("STRAVA_CLIENT_SECRET"),
        "grant_type": "refresh_token", "refresh_token": d.get("refresh_token"),
    }, timeout=20)
    if r.status_code != 200:
        raise PermissionError("Strava session expired. Reconnect Strava.")
    _save_tokens(ref, r.json())   # Strava may rotate the refresh token, so always store the new one
    return r.json()["access_token"]


class RateLimited(Exception):
    pass


def _get(path, token, params=None):
    r = requests.get(STRAVA_API + path, headers={"Authorization": f"Bearer {token}"},
                     params=params or {}, timeout=30)
    if r.status_code == 429:
        raise RateLimited("Strava's rate limit was reached. The rest will load on the next sync.")
    if r.status_code == 401:
        raise PermissionError("Strava session expired. Reconnect Strava.")
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- normalize
def _r(x, n=1):
    return round(x, n) if isinstance(x, (int, float)) else None


def summary_doc(a):
    """Strava list/detail activity -> stored fields (meters, seconds)."""
    local = a.get("start_date_local") or a.get("start_date") or ""
    return {
        "id": str(a["id"]), "name": a.get("name") or "", "sport": a.get("sport_type") or a.get("type") or "",
        "date": local[:10], "start_local": local.replace("Z", ""), "start_utc": a.get("start_date"),
        "distance_m": _r(a.get("distance")), "moving_s": a.get("moving_time"), "elapsed_s": a.get("elapsed_time"),
        "elev_m": _r(a.get("total_elevation_gain")), "avg_speed": a.get("average_speed"),
        "max_speed": a.get("max_speed"), "avg_hr": _r(a.get("average_heartrate")),
        "max_hr": _r(a.get("max_heartrate")), "cadence": _r(a.get("average_cadence")),
        "workout_type": a.get("workout_type"), "polyline": (a.get("map") or {}).get("summary_polyline") or "",
        "trainer": bool(a.get("trainer")), "manual": bool(a.get("manual")),
    }


def detail_doc(a):
    return {
        "has_detail": True,
        "description": a.get("description") or "",
        "calories": _r(a.get("calories"), 0),
        "polyline_full": (a.get("map") or {}).get("polyline") or "",
        "splits": [{"n": s.get("split"), "distance_m": _r(s.get("distance")), "moving_s": s.get("moving_time"),
                    "elev_m": _r(s.get("elevation_difference")), "avg_hr": _r(s.get("average_heartrate"))}
                   for s in (a.get("splits_standard") or [])],
        "best_efforts": [{"name": b.get("name"), "elapsed_s": b.get("elapsed_time"),
                          "distance_m": _r(b.get("distance")), "pr_rank": b.get("pr_rank")}
                         for b in (a.get("best_efforts") or [])],
        "laps": [{"n": l.get("lap_index"), "distance_m": _r(l.get("distance")), "moving_s": l.get("moving_time"),
                  "avg_hr": _r(l.get("average_heartrate"))} for l in (a.get("laps") or [])][:60],
    }


# ---------------------------------------------------------------- sync
def _first_sync_start(today=None):
    today = today or date.today()
    return datetime(today.year - 1, 1, 1, tzinfo=timezone.utc)   # this year plus last year for comparison


def sync_strava(full=False):
    token = _access_token()
    ref = _integration()
    meta = ref.get().to_dict() or {}
    col = _activities()
    stored = {s.id: s.to_dict() for s in col.stream()}
    if full or not meta.get("synced_through"):
        after = _first_sync_start()
    else:   # re-read the last week so edits and late uploads show up
        after = datetime.fromtimestamp(meta["synced_through"], timezone.utc) - timedelta(days=7)

    seen, newest, note = set(), meta.get("synced_through") or 0, None
    batch, n = db.batch(), 0
    for page in range(1, 40):
        try:
            items = _get("/athlete/activities", token,
                         {"after": int(after.timestamp()), "per_page": 200, "page": page})
        except RateLimited as e:
            note = str(e); break
        if not items:
            break
        for a in items:
            doc = summary_doc(a)
            seen.add(doc["id"])
            if a.get("start_date"):
                newest = max(newest, datetime.fromisoformat(a["start_date"].replace("Z", "+00:00")).timestamp())
            batch.set(col.document(doc["id"]), doc, merge=True)   # merge keeps detail fields
            stored.setdefault(doc["id"], {}).update(doc)
            n += 1
            if n % 400 == 0:
                batch.commit(); batch = db.batch()
        if len(items) < 200:
            break
    batch.commit()

    removed = 0
    if full and note is None:   # activities deleted on Strava disappear from a full re-read
        cutoff = after.date().isoformat()
        for aid, d in list(stored.items()):
            if aid not in seen and (d.get("date") or "") >= cutoff and d.get("source") != "strava_export":
                col.document(aid).delete(); stored.pop(aid); removed += 1

    details = 0
    if note is None:
        todo = sorted((d for d in stored.values() if d.get("sport") in RUN_TYPES and not d.get("has_detail")),
                      key=lambda d: d.get("start_local") or "", reverse=True)
        for d in todo[:DETAIL_PER_SYNC]:
            try:
                full_a = _get(f"/activities/{d['id']}", token, {"include_all_efforts": "false"})
            except RateLimited as e:
                note = str(e); break
            col.document(d["id"]).set(detail_doc(full_a), merge=True)
            details += 1
        left = max(0, len(todo) - details)
        if left and note is None:
            note = f"{left} older runs still need splits and best efforts. They load over the next syncs."

    ref.set({"last_sync": firestore.SERVER_TIMESTAMP, "synced_through": newest or after.timestamp()}, merge=True)
    runs = sum(1 for d in stored.values() if d.get("sport") in RUN_TYPES)
    return {"activities": len(seen), "runs": runs, "details": details, "removed": removed, "note": note}


# ---------------------------------------------------------------- export import
def _num(v, lo=0, hi=1e7):
    if v in (None, ""):
        return None
    v = float(v)
    if not lo <= v <= hi or v != v:
        raise ValueError("number out of range")
    return v


def clean_export(a):
    """Validate one run from the browser's export parser -> stored activity doc."""
    aid = str(a["id"])
    if not aid.isdigit() or len(aid) > 20:
        raise ValueError("bad id")
    day = date.fromisoformat(str(a["date"])).isoformat()
    sport = str(a.get("sport") or "Run")
    if sport not in RUN_TYPES:
        raise ValueError("only runs are imported")
    start = str(a.get("start_local") or "")[:19]
    if start:
        datetime.fromisoformat(start)
    splits = [{"n": int(x["n"]), "distance_m": _num(x.get("distance_m"), 0, 5000), "moving_s": _num(x.get("moving_s"), 0, 36000),
               "elev_m": _num(x.get("elev_m"), -2000, 2000), "avg_hr": _num(x.get("avg_hr"), 0, 260)}
              for x in (a.get("splits") or [])[:120]]
    efforts = [{"name": x["name"], "elapsed_s": _num(x.get("elapsed_s"), 1, 400000), "distance_m": _num(x.get("distance_m"), 1, 1e6), "pr_rank": None}
               for x in (a.get("best_efforts") or []) if x.get("name") in BEST_EFFORTS]
    poly = str(a.get("polyline") or "")
    if len(poly) > 60000:
        raise ValueError("route too long")
    return {"id": aid, "source": "strava_export", "name": str(a.get("name") or "Run")[:120], "sport": sport,
            "date": day, "start_local": start or None, "distance_m": _num(a.get("distance_m"), 0, 1e6),
            "moving_s": _num(a.get("moving_s"), 0, 400000), "elapsed_s": _num(a.get("elapsed_s"), 0, 400000),
            "elev_m": _num(a.get("elev_m"), 0, 20000), "avg_hr": _num(a.get("avg_hr"), 0, 260),
            "max_hr": _num(a.get("max_hr"), 0, 260), "calories": _num(a.get("calories"), 0, 20000),
            "description": str(a.get("description") or "")[:2000], "workout_type": 1 if a.get("race") else None,
            "polyline": poly, "polyline_full": poly, "splits": splits, "best_efforts": efforts,
            "has_detail": True}


# ---------------------------------------------------------------- read helpers
def whoop_runs(start=None, end=None):
    """Runs recorded by WHOOP, shaped like Strava activities (no route or splits)."""
    q = user_ref().collection("workouts")
    if start:
        q = q.where("date", ">=", start)
    if end:
        q = q.where("date", "<=", end)
    out = []
    for s in q.stream():
        w = s.to_dict()
        if w.get("source") != "whoop" or "run" not in (w.get("type") or "").lower():
            continue
        out.append({"id": s.id, "source": "whoop", "name": w.get("type") or "Run", "sport": "Run",
                    "date": w.get("date"), "start_local": w.get("start_local"), "distance_m": w.get("distance_m"),
                    "moving_s": (w.get("duration_min") or 0) * 60 or None, "elev_m": w.get("elev_m"),
                    "avg_hr": w.get("avg_hr"), "max_hr": w.get("max_hr"), "calories": w.get("calories"),
                    "strain": w.get("strain"), "polyline": ""})
    return out


def _same_run(a, b):
    """A WHOOP run and a Strava run on the same day that start within 30 minutes are one run."""
    if a.get("date") != b.get("date"):
        return False
    try:
        ta, tb = datetime.fromisoformat(a["start_local"]), datetime.fromisoformat(b["start_local"])
    except (KeyError, TypeError, ValueError):
        return True
    return abs((ta - tb).total_seconds()) <= 1800


def list_runs(start=None, end=None):
    q = _activities()
    if start:
        q = q.where("date", ">=", start)
    if end:
        q = q.where("date", "<=", end)
    runs = [r for r in (s.to_dict() for s in q.stream()) if r.get("sport") in RUN_TYPES]
    runs += [w for w in whoop_runs(start, end) if not any(_same_run(w, r) for r in runs)]
    return sorted(runs, key=lambda r: r.get("start_local") or r.get("date") or "", reverse=True)


LITE = ("id", "source", "name", "sport", "date", "start_local", "distance_m", "moving_s", "elapsed_s", "elev_m",
        "avg_speed", "avg_hr", "max_hr", "workout_type", "polyline")


def _monday(d):
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return d - timedelta(days=d.weekday())


def _pace(runs):
    """Seconds per mile over runs that have both distance and time (WHOOP runs without GPS have no distance)."""
    timed = [r for r in runs if r.get("distance_m") and r.get("moving_s")]
    m = sum(r["distance_m"] for r in timed)
    return round(sum(r["moving_s"] for r in timed) / (m / MILE)) if m else None


def running_year(year, today=None):
    """Everything the Running tab shows for one calendar year."""
    today = today or date.today()
    runs = list_runs(f"{year}-01-01", f"{year}-12-31")
    dist = sum(r.get("distance_m") or 0 for r in runs)
    secs = sum(r.get("moving_s") or 0 for r in runs)
    weekly = defaultdict(float)
    monthly = defaultdict(float)
    for r in runs:
        weekly[_monday(r["date"]).isoformat()] += (r.get("distance_m") or 0) / MILE
        monthly[r["date"][:7]] += (r.get("distance_m") or 0) / MILE
    # every week of the year up to today (or year end), zeros included, so gaps are visible
    first = _monday(date(year, 1, 1))
    last = min(today, date(year, 12, 31))
    weeks, w = [], first
    while w <= last:
        weeks.append({"week": w.isoformat(), "miles": round(weekly.get(w.isoformat(), 0), 2)})
        w += timedelta(days=7)
    months = [{"month": f"{year}-{m:02d}", "miles": round(monthly.get(f"{year}-{m:02d}", 0), 2)}
              for m in range(1, 13) if date(year, m, 1) <= last]
    best = {}
    for r in runs:
        for b in r.get("best_efforts") or []:
            cur = best.get(b["name"])
            if b.get("elapsed_s") and (cur is None or b["elapsed_s"] < cur["elapsed_s"]):
                best[b["name"]] = {"name": b["name"], "elapsed_s": b["elapsed_s"], "run_id": r["id"], "date": r["date"]}
    longest = max(runs, key=lambda r: r.get("distance_m") or 0, default=None)
    return {
        "year": year,
        "runs": [{k: r.get(k) for k in LITE} for r in runs],
        "totals": {"runs": len(runs), "miles": round(dist / MILE, 2), "moving_s": secs,
                   "elev_m": round(sum(r.get("elev_m") or 0 for r in runs), 1),
                   "pace_s_per_mi": _pace(runs),
                   "no_distance": sum(1 for r in runs if not r.get("distance_m")),
                   "longest": {"id": longest["id"], "miles": round(longest["distance_m"] / MILE, 2), "date": longest["date"]}
                   if longest and longest.get("distance_m") else None},
        "weekly": weeks, "monthly": months,
        "bests": [best[k] for k in BEST_EFFORTS if k in best],
        "details_missing": sum(1 for r in runs if r.get("source") != "whoop" and not r.get("has_detail")),
    }


def strava_status():
    snap = _integration().get()
    d = snap.to_dict() if snap.exists else None
    ls = d.get("last_sync") if d else None
    return {"connected": bool(d and d.get("access_token")), "athlete": (d or {}).get("athlete_name"),
            "last_sync": ls.isoformat() if hasattr(ls, "isoformat") else None}


# ---------------------------------------------------------------- routes
def _register_routes():
    @bp.get("/api/running")
    @require_owner
    def running():
        try:
            year = int(request.args.get("year") or date.today().year)
        except ValueError:
            return jsonify(error="year must be a number"), 400
        years = sorted({(r.get("date") or "")[:4] for r in list_runs()} - {""}, reverse=True)
        wsnap = user_ref().collection("integrations").document("whoop").get()
        whoop_on = bool(wsnap.exists and (wsnap.to_dict() or {}).get("access_token"))
        esnap = user_ref().collection("integrations").document("strava_export").get()
        li = (esnap.to_dict() or {}).get("last_import") if esnap.exists else None
        return jsonify(running_year(year) | {"strava": strava_status(), "whoop": {"connected": whoop_on},
                                             "export": {"last_import": li.isoformat() if hasattr(li, "isoformat") else None},
                                             "years": years or [str(year)]})

    @bp.get("/api/runs")
    @require_owner
    def runs_range():
        """Lite list for a date range (the Health calendar)."""
        start, end = request.args.get("start"), request.args.get("end")
        if not start or not end:
            return jsonify(error="start and end are required"), 400
        return jsonify(runs=[{k: r.get(k) for k in LITE if k != "polyline"} for r in list_runs(start, end)])

    @bp.get("/api/running/<rid>")
    @require_owner
    def run_detail(rid):
        if rid.startswith("whoop_"):
            w = next((r for r in whoop_runs() if r["id"] == rid), None)
            return jsonify(w) if w else (jsonify(error="run not found"), 404)
        ref = _activities().document(rid)
        snap = ref.get()
        if not snap.exists:
            return jsonify(error="run not found"), 404
        d = snap.to_dict()
        if not d.get("has_detail"):
            try:
                d.update(detail_doc(_get(f"/activities/{rid}", _access_token())))
                ref.set(d, merge=True)
            except (LookupError, PermissionError, RateLimited, requests.RequestException):
                pass   # show the summary; details arrive with a later sync
        return jsonify(d)

    @bp.post("/api/strava/import")
    @require_owner
    def strava_import():
        """Runs parsed in the browser from Strava's account export (Settings > My Account > Download your data).
        The zip can be hundreds of MB, so it never comes to the server; the page sends runs in batches."""
        body = request.get_json(force=True) or {}
        acts = body.get("activities") or []
        if not isinstance(acts, list) or len(acts) > 60:
            return jsonify(error="send at most 60 activities per request"), 400
        col = _activities()
        batch, saved = db.batch(), 0
        for a in acts:
            try:
                doc = clean_export(a)
            except (TypeError, ValueError, KeyError) as e:
                return jsonify(error=f"activity {a.get('id') if isinstance(a, dict) else '?'}: {e}"), 400
            batch.set(col.document(doc["id"]), doc, merge=True)
            saved += 1
        batch.commit()
        meta = user_ref().collection("integrations").document("strava_export")
        meta.set({"last_import": firestore.SERVER_TIMESTAMP}, merge=True)
        return jsonify(ok=True, saved=saved)

    @bp.post("/api/strava/connect")
    @require_owner
    def strava_connect():
        state = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
        db.collection("oauth_states").document(state).set({"uid": g.uid, "provider": "strava", "created": time.time()})
        url = STRAVA_AUTH_URL + "?" + urlencode({
            "client_id": _cfg("STRAVA_CLIENT_ID"), "redirect_uri": _cfg("STRAVA_REDIRECT_URI"),
            "response_type": "code", "approval_prompt": "auto", "scope": STRAVA_SCOPES, "state": state,
        })
        return jsonify(url=url)

    @bp.get("/api/strava/callback")
    def strava_callback():
        """Strava sends the browser here without the app's sign-in header; the one-time state
        value proves which signed-in user started the connection."""
        front = os.environ.get("FRONTEND_URL", "https://georgegervasio1.github.io/LifeTracker/")
        back = lambda status: redirect(f"{front}#/health/connect?strava={status}")
        state, code = request.args.get("state", ""), request.args.get("code")
        if request.args.get("error") or not code or not state:
            return back("cancelled")
        sref = db.collection("oauth_states").document(state)
        snap = sref.get()
        if not snap.exists:
            return back("expired")
        st = snap.to_dict()
        sref.delete()
        if st.get("provider") != "strava" or time.time() - st.get("created", 0) > STATE_TTL:
            return back("expired")
        if "activity:read" not in (request.args.get("scope") or ""):
            return back("scope")     # the athlete unticked activity access on Strava's screen
        r = requests.post(STRAVA_TOKEN_URL, data={
            "client_id": _cfg("STRAVA_CLIENT_ID"), "client_secret": _cfg("STRAVA_CLIENT_SECRET"),
            "code": code, "grant_type": "authorization_code",
        }, timeout=20)
        if r.status_code != 200:
            return back("failed")
        tok = r.json()
        ath = tok.get("athlete") or {}
        g.uid = st["uid"]
        _save_tokens(_integration(), tok, {
            "athlete_id": ath.get("id"),
            "athlete_name": " ".join(x for x in (ath.get("firstname"), ath.get("lastname")) if x) or None,
            "scope": request.args.get("scope"), "connected_at": firestore.SERVER_TIMESTAMP})
        return back("connected")

    @bp.post("/api/strava/sync")
    @require_owner
    def strava_sync():
        try:
            return jsonify(sync_strava(full=request.args.get("full") == "1"))
        except LookupError as e:
            return jsonify(error=str(e)), 400
        except PermissionError as e:
            return jsonify(error=str(e)), 401

    @bp.delete("/api/strava")
    @require_owner
    def strava_disconnect():
        try:
            requests.post(STRAVA_DEAUTH_URL, data={"access_token": _access_token()}, timeout=15)
        except Exception:
            pass   # disconnect locally even if Strava is unreachable
        _integration().delete()
        return jsonify(ok=True)

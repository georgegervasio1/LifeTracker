"""Health: manual workout log + WHOOP integration (OAuth 2.0, API v2).

Data layout (per user, under users/{uid}/):
  integrations/whoop   -> tokens, whoop_user_id, last_sync
  health_days/{date}   -> one doc per local day: recovery, hrv, rhr, strain, sleep
  workouts/{id}        -> manual workouts (id = random) and WHOOP workouts (id = "whoop_<id>")
"""
import os
import secrets
import string
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from flask import Blueprint, g, jsonify, redirect, request

bp = Blueprint("health", __name__)

WHOOP_AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_API = "https://api.prod.whoop.com/developer"
WHOOP_SCOPES = "offline read:recovery read:cycles read:sleep read:workout read:profile"
STATE_TTL = 600  # seconds a connect link stays valid

# Wired up by main.py so this module shares its auth + Firestore setup.
db = None
firestore = None
require_owner = None
user_ref = None


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


# ---------------------------------------------------------------- time helpers
def _parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _local_date(ts, offset):
    """WHOOP gives UTC timestamps plus the member's offset, e.g. '-05:00'."""
    dt = _parse_ts(ts)
    if offset:
        sign = -1 if offset.startswith("-") else 1
        hh, mm = offset.lstrip("+-").split(":")
        dt = dt + sign * timedelta(hours=int(hh), minutes=int(mm))
    return dt.date().isoformat()


# ---------------------------------------------------------------- WHOOP OAuth
def _integration():
    return user_ref().collection("integrations").document("whoop")


def _save_tokens(ref, tok, extra=None):
    data = {
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token"),
        "expires_at": time.time() + int(tok.get("expires_in", 3600)),
        "scope": tok.get("scope"),
    }
    data.update(extra or {})
    ref.set(data, merge=True)


def _access_token():
    ref = _integration()
    snap = ref.get()
    if not snap.exists:
        raise LookupError("WHOOP is not connected")
    d = snap.to_dict()
    if d.get("expires_at", 0) > time.time() + 60:
        return d["access_token"]
    # Refresh. WHOOP rotates the refresh token on every use, so save the new one immediately.
    r = requests.post(WHOOP_TOKEN_URL, data={
        "grant_type": "refresh_token", "refresh_token": d.get("refresh_token"),
        "client_id": _cfg("WHOOP_CLIENT_ID"), "client_secret": _cfg("WHOOP_CLIENT_SECRET"),
        "scope": "offline",
    }, timeout=20)
    if r.status_code != 200:
        raise PermissionError("WHOOP session expired — reconnect WHOOP")
    _save_tokens(ref, r.json())
    return r.json()["access_token"]


def _whoop_get(path, token, params=None):
    r = requests.get(WHOOP_API + path, headers={"Authorization": f"Bearer {token}"},
                     params=params or {}, timeout=30)
    if r.status_code == 429:
        time.sleep(2)
        r = requests.get(WHOOP_API + path, headers={"Authorization": f"Bearer {token}"},
                         params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


def _whoop_collection(path, token, start_iso, max_pages=60):
    """Yield every record since start_iso, following next_token pagination (25 per page)."""
    next_token = None
    for _ in range(max_pages):
        params = {"limit": 25, "start": start_iso}
        if next_token:
            params["nextToken"] = next_token
        page = _whoop_get(path, token, params)
        yield from page.get("records", [])
        next_token = page.get("next_token")
        if not next_token:
            break


# ---------------------------------------------------------------- sync
def sync_whoop(days_back=None):
    token = _access_token()
    ref = _integration()
    last = (ref.get().to_dict() or {}).get("last_sync")
    if days_back is None:
        days_back = 3 if last else 180   # first sync pulls ~6 months of history
    start_iso = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    days = {}       # date -> fields to merge into health_days
    cycle_dates = {}
    counts = {"cycles": 0, "recoveries": 0, "sleeps": 0, "workouts": 0}

    for c in _whoop_collection("/v2/cycle", token, start_iso):
        d = _local_date(c["start"], c.get("timezone_offset"))
        cycle_dates[str(c["id"])] = d
        score = c.get("score") or {}
        if c.get("score_state") == "SCORED" and score.get("strain") is not None:
            days.setdefault(d, {})["strain"] = round(score["strain"], 1)
        counts["cycles"] += 1

    for rec in _whoop_collection("/v2/recovery", token, start_iso):
        d = cycle_dates.get(str(rec.get("cycle_id")))
        score = rec.get("score") or {}
        if not d or rec.get("score_state") != "SCORED":
            continue
        days.setdefault(d, {}).update({
            "recovery": score.get("recovery_score"),
            "hrv": round(score["hrv_rmssd_milli"], 1) if score.get("hrv_rmssd_milli") is not None else None,
            "rhr": score.get("resting_heart_rate"),
        })
        counts["recoveries"] += 1

    for s in _whoop_collection("/v2/activity/sleep", token, start_iso):
        if s.get("nap") or s.get("score_state") != "SCORED":
            continue
        d = _local_date(s["end"], s.get("timezone_offset"))   # a night's sleep belongs to the day you wake up
        score = s.get("score") or {}
        st = score.get("stage_summary") or {}
        asleep_ms = (st.get("total_in_bed_time_milli", 0) or 0) - (st.get("total_awake_time_milli", 0) or 0)
        days.setdefault(d, {}).update({
            "sleep_hours": round(asleep_ms / 3.6e6, 2) if asleep_ms > 0 else None,
            "sleep_performance": score.get("sleep_performance_percentage"),
        })
        counts["sleeps"] += 1

    wcol = user_ref().collection("workouts")
    batch, n = db.batch(), 0
    for w in _whoop_collection("/v2/activity/workout", token, start_iso):
        score = w.get("score") or {}
        start, end = _parse_ts(w["start"]), _parse_ts(w["end"])
        batch.set(wcol.document(f"whoop_{w['id']}"), {
            "source": "whoop",
            "date": _local_date(w["start"], w.get("timezone_offset")),
            "type": (w.get("sport_name") or "Activity").replace("-", " ").title(),
            "duration_min": round((end - start).total_seconds() / 60),
            "strain": round(score["strain"], 1) if score.get("strain") is not None else None,
            "avg_hr": score.get("average_heart_rate"),
            "max_hr": score.get("max_heart_rate"),
            "calories": round(score["kilojoule"] / 4.184) if score.get("kilojoule") else None,
            "exercises": [], "notes": "",
        })
        counts["workouts"] += 1
        n += 1
        if n % 400 == 0:
            batch.commit(); batch = db.batch()

    hcol = user_ref().collection("health_days")
    for d, fields in days.items():
        batch.set(hcol.document(d), {"date": d, **fields}, merge=True)
        n += 1
        if n % 400 == 0:
            batch.commit(); batch = db.batch()
    batch.commit()
    ref.set({"last_sync": firestore.SERVER_TIMESTAMP}, merge=True)
    return {**counts, "days": len(days)}


# ---------------------------------------------------------------- read helpers (also used by the assistant later)
def health_days(start, end):
    q = user_ref().collection("health_days").where("date", ">=", start).where("date", "<=", end)
    return sorted((d.to_dict() for d in q.stream()), key=lambda x: x["date"])


def list_workouts(start, end):
    q = user_ref().collection("workouts").where("date", ">=", start).where("date", "<=", end)
    out = [d.to_dict() | {"id": d.id} for d in q.stream()]
    return sorted(out, key=lambda w: w["date"], reverse=True)


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def health_summary(start, end):
    days = health_days(start, end)
    today = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    workouts_week = list_workouts(week_start, today.isoformat())
    latest = next((d for d in reversed(days) if d.get("recovery") is not None), None)
    snap = _integration().get()
    whoop = snap.to_dict() if snap.exists else None
    return {
        "start": start, "end": end, "days": days,
        "latest": latest,
        "averages": {k: _avg([d.get(k) for d in days]) for k in ("recovery", "hrv", "rhr", "strain", "sleep_hours", "sleep_performance")},
        "workouts_this_week": len(workouts_week),
        "whoop": {"connected": bool(whoop and whoop.get("access_token")),
                  "last_sync": whoop.get("last_sync").isoformat() if whoop and whoop.get("last_sync") else None},
    }


# ---------------------------------------------------------------- routes
def _register_routes():
    def range_args(default_days=30):
        end = request.args.get("end") or date.today().isoformat()
        start = request.args.get("start") or (date.today() - timedelta(days=default_days)).isoformat()
        return start, end

    @bp.get("/api/health/summary")
    @require_owner
    def summary():
        return jsonify(health_summary(*range_args()))

    @bp.get("/api/workouts")
    @require_owner
    def get_workouts():
        return jsonify(workouts=list_workouts(*range_args(60)))

    @bp.post("/api/workouts")
    @require_owner
    def add_workout():
        b = request.get_json(force=True) or {}
        try:
            d = date.fromisoformat(str(b.get("date")))
        except ValueError:
            return jsonify(error="date must be YYYY-MM-DD"), 400
        wtype = str(b.get("type") or "").strip()[:60]
        if not wtype:
            return jsonify(error="workout type is required"), 400
        exercises = []
        for e in (b.get("exercises") or [])[:40]:
            name = str(e.get("name") or "").strip()[:60]
            if not name:
                continue
            num = lambda k: (float(e[k]) if str(e.get(k, "")).strip() not in ("", "None") else None)
            try:
                exercises.append({"name": name, "sets": num("sets"), "reps": num("reps"), "weight": num("weight")})
            except ValueError:
                return jsonify(error=f"sets, reps and weight must be numbers ({name})"), 400
        try:
            duration = int(b["duration_min"]) if b.get("duration_min") not in (None, "") else None
        except ValueError:
            return jsonify(error="duration must be a whole number of minutes"), 400
        doc = user_ref().collection("workouts").document()
        doc.set({"source": "manual", "date": d.isoformat(), "type": wtype, "duration_min": duration,
                 "exercises": exercises, "notes": str(b.get("notes") or "")[:1000],
                 "created": firestore.SERVER_TIMESTAMP})
        return jsonify(ok=True, id=doc.id)

    @bp.delete("/api/workouts/<wid>")
    @require_owner
    def delete_workout(wid):
        if wid.startswith("whoop_"):
            return jsonify(error="WHOOP workouts are managed in WHOOP"), 400
        user_ref().collection("workouts").document(wid).delete()
        return jsonify(ok=True)

    # --- WHOOP connect flow -------------------------------------------------
    @bp.post("/api/whoop/connect")
    @require_owner
    def whoop_connect():
        state = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
        db.collection("oauth_states").document(state).set({"uid": g.uid, "provider": "whoop", "created": time.time()})
        url = WHOOP_AUTH_URL + "?" + urlencode({
            "response_type": "code", "client_id": _cfg("WHOOP_CLIENT_ID"),
            "redirect_uri": _cfg("WHOOP_REDIRECT_URI"), "scope": WHOOP_SCOPES, "state": state,
        })
        return jsonify(url=url)

    @bp.get("/api/whoop/callback")
    def whoop_callback():
        """WHOOP redirects the browser here (no app sign-in header), so the one-time state
        value is what proves which signed-in user started the connection."""
        front = os.environ.get("FRONTEND_URL", "https://georgegervasio1.github.io/LifeTracker/")
        back = lambda status: redirect(f"{front}#/health/connect?whoop={status}")
        state, code = request.args.get("state", ""), request.args.get("code")
        if request.args.get("error") or not code or not state:
            return back("cancelled")
        sref = db.collection("oauth_states").document(state)
        snap = sref.get()
        if not snap.exists:
            return back("expired")
        st = snap.to_dict()
        sref.delete()                                   # single use
        if st.get("provider") != "whoop" or time.time() - st.get("created", 0) > STATE_TTL:
            return back("expired")
        r = requests.post(WHOOP_TOKEN_URL, data={
            "grant_type": "authorization_code", "code": code,
            "client_id": _cfg("WHOOP_CLIENT_ID"), "client_secret": _cfg("WHOOP_CLIENT_SECRET"),
            "redirect_uri": _cfg("WHOOP_REDIRECT_URI"),
        }, timeout=20)
        if r.status_code != 200:
            return back("failed")
        g.uid = st["uid"]
        _save_tokens(_integration(), r.json(), {"connected_at": firestore.SERVER_TIMESTAMP})
        return back("connected")

    @bp.post("/api/whoop/sync")
    @require_owner
    def whoop_sync():
        try:
            return jsonify(sync_whoop())
        except LookupError as e:
            return jsonify(error=str(e)), 400
        except PermissionError as e:
            return jsonify(error=str(e)), 401

    @bp.delete("/api/whoop")
    @require_owner
    def whoop_disconnect():
        try:
            token = _access_token()
            requests.delete(WHOOP_API + "/v2/user/access", headers={"Authorization": f"Bearer {token}"}, timeout=15)
        except Exception:
            pass  # disconnect locally even if WHOOP is unreachable
        _integration().delete()
        return jsonify(ok=True)

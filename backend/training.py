"""Training: weekly workout planner (template), live-session logging, history with PRs, exercise progress.

Data (per user, under users/{uid}/):
  training/state      -> program name, maxes, cycle start, defaults, warm-ups, custom exercises
  training/template   -> {weeks: [{week, days: {mon: [workout], ...}, tray: [workout]}]}
  workouts/{id}       -> logged sessions use source "session"; they share the collection with
                         manual and WHOOP workouts so the Health calendar shows everything.

Units: "lb" (default), "plates" (plates per side), "pin" (stack pin number). A set may override
the exercise unit with "plates_side" (e.g. 45s = 45 lb plates per side). Volume and estimated
1-rep max only use pound values; PRs compare like with like.
"""
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

bp = Blueprint("training", __name__)
db = firestore = require_owner = user_ref = None

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
UNITS = {"lb", "plates", "pin", "plates_side"}
CARDIO_SPORTS = {"running", "run", "cycling", "bike", "swimming", "swim", "basketball", "walking", "hiking"}
LIMITS = {"weeks": 8, "workouts_per_day": 8, "tray": 40, "exercises": 30, "sets": 20}


def init(app, _db, _firestore, _require_owner, _user_ref):
    global db, firestore, require_owner, user_ref
    db, firestore, require_owner, user_ref = _db, _firestore, _require_owner, _user_ref
    _register_routes()
    app.register_blueprint(bp)


# ---------------------------------------------------------------- helpers
def _doc(name):
    return user_ref().collection("training").document(name)


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f"not a number: {v!r}")


def e1rm(weight, reps):
    """Epley estimated one-rep max."""
    if not weight or not reps or weight <= 0 or reps <= 0:
        return None
    return weight if reps == 1 else weight * (1 + reps / 30)


def set_unit(ex, s):
    return s.get("unit") or ex.get("unit") or "lb"


def current_week(state, today=None):
    today = today or date.today()
    try:
        start = date.fromisoformat(state.get("cycle_start"))
    except (TypeError, ValueError):
        return 1
    n = max(1, int(state.get("cycle_weeks") or 4))
    return ((today - start).days // 7) % n + 1 if today >= start else 1


# ---------------------------------------------------------------- validation
def _clean_exercise(e):
    unit = e.get("unit") or "lb"
    if unit not in UNITS - {"plates_side"}:
        raise ValueError(f"unknown unit {unit!r}")
    name = str(e.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("exercise name is required")
    out = {"id": str(e.get("id") or uuid.uuid4().hex[:10])[:20], "name": name, "unit": unit,
           "rest_sec": int(min(max(int(e.get("rest_sec") or 120), 0), 900)),
           "sets": int(min(max(int(e.get("sets") or 3), 1), LIMITS["sets"])),
           "reps": (str(e["reps"])[:20] if e.get("reps") not in (None, "") else None),
           "pct_of_max": (int(e["pct_of_max"]) if e.get("pct_of_max") not in (None, "") else None),
           "max_lift": e.get("max_lift") if e.get("max_lift") in ("bench", "squat") else None,
           "per": (str(e["per"])[:20] if e.get("per") else None),
           "notes": (str(e["notes"])[:300] if e.get("notes") else None)}
    if out["pct_of_max"] is not None and not 1 <= out["pct_of_max"] <= 110:
        raise ValueError("percent of max must be 1-110")
    return out


def _clean_workout(w):
    name = str(w.get("name") or "").strip()[:60]
    if not name:
        raise ValueError("workout name is required")
    ex = w.get("exercises") or []
    if len(ex) > LIMITS["exercises"]:
        raise ValueError("too many exercises in one workout")
    return {"id": str(w.get("id") or uuid.uuid4().hex[:10])[:20], "name": name,
            "kind": "cardio" if w.get("kind") == "cardio" else "strength",
            "program_day": w.get("program_day") if isinstance(w.get("program_day"), int) else None,
            "exercises": [_clean_exercise(e) for e in ex]}


def clean_template(t):
    weeks = (t or {}).get("weeks") or []
    if not 1 <= len(weeks) <= LIMITS["weeks"]:
        raise ValueError("template needs 1-8 weeks")
    out = []
    for i, wk in enumerate(weeks):
        days = {}
        for d in WEEKDAYS:
            ws = (wk.get("days") or {}).get(d) or []
            if len(ws) > LIMITS["workouts_per_day"]:
                raise ValueError(f"too many workouts on {d}")
            days[d] = [_clean_workout(w) for w in ws]
        tray = wk.get("tray") or []
        if len(tray) > LIMITS["tray"]:
            raise ValueError("too many unscheduled workouts")
        out.append({"week": i + 1, "days": days, "tray": [_clean_workout(w) for w in tray]})
    return {"weeks": out}


def clean_session(b):
    try:
        d = date.fromisoformat(str(b.get("date")))
    except ValueError:
        raise ValueError("date must be YYYY-MM-DD")
    name = str(b.get("name") or "Workout").strip()[:80]
    exercises = []
    for e in (b.get("exercises") or [])[:LIMITS["exercises"]]:
        ename = str(e.get("name") or "").strip()[:80]
        if not ename:
            continue
        unit = e.get("unit") or "lb"
        if unit not in UNITS:
            raise ValueError(f"unknown unit {unit!r}")
        sets = []
        for s in (e.get("sets") or [])[:LIMITS["sets"]]:
            w, r = _num(s.get("weight")), _num(s.get("reps"))
            if r is not None and not 0 <= r <= 500:
                raise ValueError("reps out of range")
            if w is not None and not -500 <= w <= 2000:
                raise ValueError("weight out of range")
            su = s.get("unit")
            if su and su not in UNITS:
                raise ValueError(f"unknown unit {su!r}")
            item = {"weight": w, "reps": int(r) if r is not None else None, "done": bool(s.get("done"))}
            if su: item["unit"] = su
            if s.get("warmup"): item["warmup"] = True
            if s.get("note"): item["note"] = str(s["note"])[:200]
            sets.append(item)
        exercises.append({"name": ename, "unit": unit, "rest_sec": int(e.get("rest_sec") or 120),
                          "note": (str(e["note"])[:300] if e.get("note") else None), "sets": sets})
    dur = b.get("duration_sec")
    return {"source": "session", "date": d.isoformat(), "name": name, "type": str(b.get("type") or name)[:40],
            "duration_sec": int(dur) if dur not in (None, "") else None,
            "duration_min": round(int(dur) / 60) if dur not in (None, "") else None,
            "started_at": b.get("started_at"), "finished_at": b.get("finished_at"),
            "exercises": exercises, "notes": str(b.get("notes") or "")[:1000],
            "imported": bool(b.get("imported"))}


# ---------------------------------------------------------------- history math
def annotate(sessions):
    """Add volume, best sets and PR counts. Sessions must be oldest-first.
    A PR = beating every earlier session on heaviest weight or estimated 1-rep max (same exercise and unit).
    An exercise's first session sets the baseline and is not a PR."""
    best_w, best_e = {}, {}   # (exercise, unit) -> best before this session
    out = []
    for s in sessions:
        volume, prs, rows = 0.0, 0, []
        session_best = {}      # key -> (top weight, top e1rm)
        for ex in s.get("exercises") or []:
            done = [x for x in ex.get("sets") or [] if not x.get("warmup") and (x.get("weight") is not None or x.get("reps") is not None)]
            best_set, best_score, keys = None, None, set()
            for x in done:
                u = set_unit(ex, x)
                w, r = x.get("weight"), x.get("reps") or 0
                if u == "lb" and w and w > 0 and r:
                    volume += w * r
                est = e1rm(w, r) if (u == "lb" and w and w > 0) else None
                score = est if est is not None else (w if w is not None else r)
                if score is None:
                    continue
                if best_score is None or score > best_score:
                    best_score, best_set = score, {**x, "unit": u}
                key = (ex["name"].lower(), u)
                keys.add(key)
                tw, te = session_best.get(key, (None, None))
                top_w = w if w is not None else r
                session_best[key] = (top_w if tw is None or (top_w is not None and top_w > tw) else tw,
                                     est if te is None or (est is not None and est > te) else te)
            is_pr = False
            for key in keys:
                tw, te = session_best[key]
                if key in best_w and ((tw is not None and best_w[key] is not None and tw > best_w[key] + 1e-9) or
                                      (te is not None and best_e.get(key) is not None and te > best_e[key] + 1e-9)):
                    is_pr = True
            if is_pr:
                prs += 1
            rows.append({"name": ex["name"], "sets": len(done), "best": best_set, "pr": is_pr})
        for key, (tw, te) in session_best.items():   # update bests after the whole session
            best_w[key] = tw if best_w.get(key) is None or (tw is not None and tw > best_w[key]) else best_w[key]
            if te is not None:
                best_e[key] = te if best_e.get(key) is None or te > best_e[key] else best_e[key]
            else:
                best_e.setdefault(key, None)
        out.append({**s, "volume": round(volume), "prs": prs, "summary": rows})
    return out


def load_sessions(start=None, end=None):
    q = user_ref().collection("workouts").where("source", "==", "session")
    items = [d.to_dict() | {"id": d.id} for d in q.stream()]
    items.sort(key=lambda s: (s["date"], s.get("started_at") or ""))
    return [s for s in items if (not start or s["date"] >= start) and (not end or s["date"] <= end)]


def last_sets_by_exercise(sessions):
    last = {}
    for s in sessions:                      # oldest-first, so later sessions overwrite
        for ex in s.get("exercises") or []:
            sets = [x for x in ex.get("sets") or [] if not x.get("warmup")]
            if any(x.get("weight") is not None or x.get("reps") is not None for x in sets):
                last[ex["name"].lower()] = {"date": s["date"], "unit": ex.get("unit"), "sets": sets}
    return last


def exercise_progress(sessions, name):
    pts = []
    for s in annotate(sessions):
        for ex in s.get("exercises") or []:
            if ex["name"].lower() != name.lower():
                continue
            lb = [x for x in ex.get("sets") or [] if set_unit(ex, x) == "lb" and x.get("weight") and x["weight"] > 0 and x.get("reps") and not x.get("warmup")]
            other = [x for x in ex.get("sets") or [] if x.get("weight") is not None and not x.get("warmup")]
            row = next((r for r in s["summary"] if r["name"] == ex["name"]), {})
            pts.append({"date": s["date"],
                        "top_weight": max((x["weight"] for x in (lb or other)), default=None),
                        "e1rm": round(max((e1rm(x["weight"], x["reps"]) for x in lb), default=0), 1) or None,
                        "volume": round(sum(x["weight"] * x["reps"] for x in lb)),
                        "unit": "lb" if lb else (set_unit(ex, other[0]) if other else ex.get("unit")),
                        "pr": row.get("pr", False)})
    return pts


# ---------------------------------------------------------------- WHOOP merge
def merge_whoop(session_ref, session):
    """Attach a same-day WHOOP workout to the logged session instead of listing it twice."""
    col = user_ref().collection("workouts")
    cands = [d.to_dict() | {"id": d.id} for d in col.where("date", "==", session["date"]).stream()]
    cands = [c for c in cands if c.get("source") == "whoop" and not c.get("merged_into")]
    if not cands:
        return None

    def overlap(c):
        try:
            a0, a1 = datetime.fromisoformat(session["started_at"]), datetime.fromisoformat(session["finished_at"])
            b0, b1 = datetime.fromisoformat(c["start"]), datetime.fromisoformat(c["end"])
            return max(timedelta(0), min(a1, b1) - max(a0, b0)).total_seconds()
        except (KeyError, TypeError, ValueError):
            return 0

    timed = sorted(cands, key=overlap, reverse=True)
    pick = timed[0] if overlap(timed[0]) > 0 else next(
        (c for c in cands if c.get("type", "").lower() not in CARDIO_SPORTS), None)
    if not pick:
        return None
    whoop = {k: pick.get(k) for k in ("strain", "avg_hr", "max_hr", "calories", "type")}
    session_ref.set({"whoop": whoop, "whoop_id": pick["id"]}, merge=True)
    col.document(pick["id"]).set({"merged_into": session_ref.id}, merge=True)
    return whoop


# ---------------------------------------------------------------- routes
def _state():
    s = _doc("state").get()
    return s.to_dict() if s.exists else {}


def _template():
    t = _doc("template").get()
    return t.to_dict() if t.exists else {"weeks": [{"week": 1, "days": {d: [] for d in WEEKDAYS}, "tray": []}]}


def _register_routes():
    @bp.get("/api/training")
    @require_owner
    def get_training():
        state, sessions = _state(), load_sessions()
        return jsonify(state=state, template=_template(), current_week=current_week(state),
                       today=date.today().isoformat(), weekday=WEEKDAYS[date.today().weekday()],
                       last_sets=last_sets_by_exercise(sessions), has_history=bool(sessions))

    @bp.put("/api/training/state")
    @require_owner
    def put_state():
        b = request.get_json(force=True) or {}
        upd = {}
        if "maxes" in b:
            upd["maxes"] = {k: (_num(v) if v not in (None, "") else None) for k, v in (b["maxes"] or {}).items() if k in ("bench", "squat")}
        for k in ("cycle_start",):
            if k in b:
                try:
                    upd[k] = date.fromisoformat(b[k]).isoformat()
                except (TypeError, ValueError):
                    return jsonify(error="cycle start must be YYYY-MM-DD"), 400
        if "default_rest_sec" in b:
            upd["default_rest_sec"] = int(b["default_rest_sec"])
        if "custom_exercises" in b and isinstance(b["custom_exercises"], list):
            upd["custom_exercises"] = [{"name": str(c.get("name"))[:80], "muscle": str(c.get("muscle") or "Other")[:30],
                                        "equipment": str(c.get("equipment") or "Other")[:30]}
                                       for c in b["custom_exercises"][:200] if c.get("name")]
        _doc("state").set(upd, merge=True)
        return jsonify(state=_state(), current_week=current_week(_state()))

    @bp.put("/api/training/template")
    @require_owner
    def put_template():
        try:
            t = clean_template(request.get_json(force=True))
        except (ValueError, TypeError) as e:
            return jsonify(error=str(e)), 400
        _doc("template").set(t)
        return jsonify(template=t)

    @bp.post("/api/training/sessions")
    @require_owner
    def save_session():
        try:
            s = clean_session(request.get_json(force=True) or {})
        except ValueError as e:
            return jsonify(error=str(e)), 400
        ref = user_ref().collection("workouts").document()
        ref.set(s)
        whoop = merge_whoop(ref, s) if s.get("started_at") and not s["imported"] else None
        return jsonify(ok=True, id=ref.id, whoop=whoop)

    @bp.delete("/api/training/sessions/<sid>")
    @require_owner
    def delete_session(sid):
        col = user_ref().collection("workouts")
        snap = col.document(sid).get()
        if not snap.exists or snap.to_dict().get("source") != "session":
            return jsonify(error="session not found"), 404
        wid = snap.to_dict().get("whoop_id")
        if wid:
            col.document(wid).set({"merged_into": None}, merge=True)   # un-hide the WHOOP workout
        col.document(sid).delete()
        return jsonify(ok=True)

    @bp.get("/api/training/history")
    @require_owner
    def history():
        start, end = request.args.get("start"), request.args.get("end")
        all_sessions = annotate(load_sessions())             # PRs need full history
        shown = [s for s in all_sessions if (not start or s["date"] >= start) and (not end or s["date"] <= end)]
        days = sorted({d.get("date") for d in user_ref().collection("workouts").stream()
                       if (not start or d.get("date") >= start) and (not end or d.get("date") <= end)})
        return jsonify(sessions=list(reversed(shown)), workout_days=days)

    @bp.get("/api/training/exercise")
    @require_owner
    def progress():
        name = request.args.get("name", "")
        return jsonify(name=name, points=exercise_progress(load_sessions(), name))

    @bp.post("/api/training/import")
    @require_owner
    def import_training():
        """Load {state, template, sessions}. Replaces previously imported sessions so it can be re-run."""
        b = request.get_json(force=True) or {}
        try:
            t = clean_template(b.get("template"))
            sessions = [clean_session({**s, "imported": True}) for s in (b.get("sessions") or [])[:500]]
        except (ValueError, TypeError) as e:
            return jsonify(error=f"Import failed: {e}"), 400
        col = user_ref().collection("workouts")
        for d in col.where("source", "==", "session").stream():
            if d.to_dict().get("imported"):
                col.document(d.id).delete()
        batch, n = db.batch(), 0
        for s in sessions:
            batch.set(col.document(), s); n += 1
            if n % 400 == 0:
                batch.commit(); batch = db.batch()
        batch.commit()
        st = {k: v for k, v in (b.get("state") or {}).items()
              if k in ("program_name", "maxes", "cycle_start", "history_start", "cycle_weeks", "default_rest_sec", "warmups", "schedule_reference")}
        _doc("state").set(st, merge=True)
        _doc("template").set(t)
        return jsonify(imported_sessions=n, weeks=len(t["weeks"]))

"""Race training: build a week-by-week running plan for a 5K, 10K, half marathon or marathon.

How a plan is built (all in build_plan, which is pure and tested offline):
  - Weeks run Monday to Sunday, from the start week to the week of the race (at most MAX_WEEKS).
  - Phases: base (easy miles + strides), build (threshold and interval work), peak (race-specific
    work), taper (volume drops, some intensity stays), then race week.
  - Weekly volume ramps from the runner's current mileage toward a peak, by at most ~10% a week,
    with a cutback week (80%) every fourth week. The long run grows the same way, capped at a
    share of the week.
  - Paces use Jack Daniels' VDOT formulas (Daniels & Gilbert oxygen-cost and drop-dead equations).
    Fitness comes from a recent race or Strava best effort; a goal time sets goal race pace.
    Without either, workouts are described by effort instead of pace.

Data (per user): race_plans/{id} -> {params, plan, created}
"""
import math
import uuid
from datetime import date, timedelta

from flask import Blueprint, jsonify, request

bp = Blueprint("raceplan", __name__)
db = firestore = require_owner = user_ref = None

MILE = 1609.344
MAX_WEEKS = 24
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# min/ideal weeks, taper weeks, peak weekly miles floor/cap, long-run peak, long-run share cap,
# weekly mileage below which the plan warns that the start is aggressive.
RACES = {
    "5k":       dict(meters=5000,    label="5K",            min_weeks=6,  ideal=8,  taper=1, floor=15, cap=40, long_peak=8,  long_share=0.35, base_mi=5),
    "10k":      dict(meters=10000,   label="10K",           min_weeks=6,  ideal=10, taper=1, floor=20, cap=45, long_peak=10, long_share=0.35, base_mi=10),
    "half":     dict(meters=21097.5, label="Half marathon", min_weeks=8,  ideal=12, taper=2, floor=25, cap=50, long_peak=12, long_share=0.40, base_mi=15),
    "marathon": dict(meters=42195,   label="Marathon",      min_weeks=12, ideal=18, taper=3, floor=35, cap=60, long_peak=20, long_share=0.50, base_mi=20),
}

# Run days by runs per week, Monday = 0, with the long run on Sunday (shifted for other long days).
# Q = quality day (first, second), E = easy, L = long.
PATTERNS = {
    3: {1: "Q", 3: "E", 6: "L"},
    4: {1: "Q", 3: "E", 5: "E", 6: "L"},
    5: {1: "Q", 2: "E", 3: "Q2", 5: "E", 6: "L"},
    6: {0: "E", 1: "Q", 2: "E", 3: "Q2", 5: "E", 6: "L"},
}
WU_CD = 3.0   # warm-up + cool-down miles around a quality session


# ---------------------------------------------------------------- VDOT (Daniels)
def _vo2(v):            # oxygen cost (ml/kg/min) at v meters per minute
    return -4.60 + 0.182258 * v + 0.000104 * v * v


def _pct(t):            # fraction of VO2max sustainable for t minutes
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t) + 0.2989558 * math.exp(-0.1932605 * t)


def vdot(meters, seconds):
    t = seconds / 60
    return _vo2(meters / t) / _pct(t)


def race_seconds(vd, meters):
    """Predicted race time for a VDOT (bisection on time)."""
    lo, hi = meters / 600 * 60, meters / 50 * 60   # 600 m/min (very fast) .. 50 m/min (walk)
    for _ in range(80):
        mid = (lo + hi) / 2
        if vdot(meters, mid) > vd:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _velocity_at(vo2):
    a, b, c = 0.000104, 0.182258, -4.60 - vo2
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)


def _sec_per_mile(v):
    return MILE / v * 60


def training_paces(vd, goal_seconds=None, race_m=None):
    """Seconds per mile. E is a range (62-70% VO2max); M is predicted marathon pace; T 88%;
    I 97.5%; R is predicted mile race pace. G is goal race pace when a goal is set."""
    p = {
        "E": [round(_sec_per_mile(_velocity_at(vd * 0.70))), round(_sec_per_mile(_velocity_at(vd * 0.62)))],
        "M": round(race_seconds(vd, 42195) / (42195 / MILE)),
        "T": round(_sec_per_mile(_velocity_at(vd * 0.88))),
        "I": round(_sec_per_mile(_velocity_at(vd * 0.975))),
        "R": round(race_seconds(vd, MILE)),
    }
    if goal_seconds and race_m:
        p["G"] = round(goal_seconds / (race_m / MILE))
    return p


def fmt_pace(s):
    return f"{int(s) // 60}:{int(round(s)) % 60:02d}"


def fmt_time(s):
    s = int(round(s))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------- workouts
def _rnd(x, step=0.5):
    return round(round(x / step) * step, 2)


def _pace_txt(paces, key):
    if not paces or key not in paces:
        return {"E": "easy, conversational", "M": "marathon effort", "T": "threshold, comfortably hard",
                "I": "hard, 5K effort", "R": "fast and relaxed, mile effort", "G": "goal race pace"}[key]
    v = paces[key]
    return f"{fmt_pace(v[0])}–{fmt_pace(v[1])}/mi" if isinstance(v, list) else f"{fmt_pace(v)}/mi"


def _quality(race, phase, idx, slot, paces):
    """One quality session: (kind, title, miles, detail). idx counts weeks within the phase."""
    P = lambda k: _pace_txt(paces, k)
    if phase == "base":
        if slot == "Q":
            return ("easy", "Easy + strides", None, f"Easy run ({P('E')}), then 6 × 20 s strides with full recovery.")
        return ("easy", "Easy + hills", None, f"Easy run ({P('E')}) with 6 × 30 s uphill at strong effort, jog down.")
    goal = "G" if paces and "G" in paces else ("M" if race == "marathon" else "T" if race == "half" else "I")
    if slot == "Q":   # threshold early, intervals once the base is in
        if phase == "build":
            T = [("Tempo", 2.5, "20 min continuous"), ("Cruise intervals", 3.5, "3 × 1 mi, 1 min jog"),
                 ("Tempo", 3.5, "25 min continuous"), ("Cruise intervals", 4.5, "4 × 1 mi, 1 min jog"),
                 ("Tempo", 4.0, "30 min continuous"), ("Cruise intervals", 5.0, "5 × 1 mi, 1 min jog")]
            name, work, rep = T[idx % len(T)]
            return ("tempo", name, work + WU_CD, f"{rep} at {P('T')}. Warm up and cool down easy.")
        if race in ("5k", "10k"):
            I = [("Intervals", 4.0, "5 × 1000 m, 400 m jog"), ("Intervals", 4.5, "4 × 1200 m, 400 m jog"),
                 ("Intervals", 4.0, "6 × 800 m, 400 m jog")]
            name, work, rep = I[idx % len(I)]
            return ("intervals", name, work + WU_CD, f"{rep} at {P('I')}.")
        if race == "half":
            return ("tempo", "Race-pace tempo", 6.0 + WU_CD, f"2 × 3 mi at {P(goal)}, 3 min jog between.")
        return ("tempo", "Threshold", 5.0 + WU_CD, f"2 × 2 mi at {P('T')}, 2 min jog.")
    # second quality day (runs >= 5)
    if race == "marathon":
        mi = min(4 + idx, 8) if phase == "build" else 8
        return ("tempo", "Marathon-pace run", mi + 2, f"{mi} mi at {P('M' if 'G' not in (paces or {}) else 'G')} after 1 mi easy, 1 mi easy after.")
    if race == "half":
        mi = min(3 + idx, 6)
        return ("tempo", "Half-pace run", mi + WU_CD, f"{mi} mi at {P(goal)}.")
    if phase == "build":
        return ("intervals", "Hill repeats", 5.0, "8 × 60 s hard uphill, jog down. Warm up and cool down easy.")
    return ("intervals", "Race-pace reps", 6.0, f"{'5 × 1000 m' if race == '5k' else '3 × 2000 m'} at {P(goal)}, 2 min jog.")


# ---------------------------------------------------------------- plan
def _monday(d):
    return d - timedelta(days=d.weekday())


def default_start(today):
    return today if today.weekday() == 0 else _monday(today) + timedelta(days=7)


def build_plan(params, today=None, fitness=None):
    """params: race (5k|10k|half|marathon), race_date, runs_per_week 3..6, long_day 0..6,
    current_weekly_mi, current_long_mi, goal_seconds (optional), start_date (optional), name.
    fitness: {"vdot", "source"} from Strava, optional."""
    today = today or date.today()
    key = params.get("race")
    if key not in RACES:
        raise ValueError("race must be 5k, 10k, half or marathon")
    R = RACES[key]
    try:
        race_day = date.fromisoformat(str(params.get("race_date")))
    except ValueError:
        raise ValueError("race date must be YYYY-MM-DD")
    if race_day <= today:
        raise ValueError("race date must be in the future")
    runs = int(params.get("runs_per_week") or 4)
    if runs not in PATTERNS:
        raise ValueError("runs per week must be 3 to 6")
    long_day = int(params.get("long_day", 6))
    if not 0 <= long_day <= 6:
        raise ValueError("long run day must be 0 (Mon) to 6 (Sun)")
    cur_w = max(0.0, float(params.get("current_weekly_mi") or 0))
    cur_l = max(0.0, float(params.get("current_long_mi") or 0))
    goal = params.get("goal_seconds")
    goal = float(goal) if goal not in (None, "", 0) else None

    start = date.fromisoformat(params["start_date"]) if params.get("start_date") else default_start(today)
    start = _monday(start)
    race_week = _monday(race_day)
    if race_week < start:
        start = race_week
    n = (race_week - start).days // 7 + 1
    notes = []
    if n > MAX_WEEKS:
        start = race_week - timedelta(weeks=MAX_WEEKS - 1)
        n = MAX_WEEKS
        notes.append(f"Plans run at most {MAX_WEEKS} weeks, so this one starts {start.strftime('%b %-d')}. Keep running easy until then.")
    if n < R["min_weeks"]:
        notes.append(f"{n} weeks is short for a {R['label'].lower()} (about {R['ideal']} is typical). The plan compresses base and build.")
    if cur_w < R["base_mi"]:
        notes.append(f"Plans for a {R['label'].lower()} usually start from about {R['base_mi']} miles a week. This one starts lower, so build patiently.")

    # ---- fitness and paces
    vd, src = None, None
    if fitness and fitness.get("vdot"):
        vd, src = fitness["vdot"], fitness.get("source")
    elif goal:
        vd, src = vdot(R["meters"], goal), "your goal time"
    paces = training_paces(vd, goal, R["meters"]) if vd else None
    predicted = round(race_seconds(vd, R["meters"])) if vd else None

    # ---- phases
    taper = min(R["taper"], max(0, n - 1))
    pre = n - 1 - taper                   # weeks before taper and race week
    if pre >= 4:
        peak_n = min(3, max(1, round(pre * 0.2)))
        base_n = max(1, round(pre * 0.35))
    else:
        peak_n, base_n = (1 if pre >= 2 else 0), (1 if pre >= 3 else 0)
    build_n = pre - base_n - peak_n
    phases = ["base"] * base_n + ["build"] * build_n + ["peak"] * peak_n + ["taper"] * taper + ["race"]

    # ---- volume
    start_w = max(cur_w, runs * 2.5)
    peak_w = cur_w if cur_w >= R["cap"] else min(max(cur_w * 1.3, R["floor"]), R["cap"])
    ramp_weeks = max(1, pre - 2)
    peak_w = max(start_w, min(peak_w, start_w * 1.1 ** ramp_weeks))
    long_start = max(cur_l, min(R["long_peak"], start_w * 0.3), 3)
    long_peak = max(long_start, min(R["long_peak"], peak_w * R["long_share"]))
    if long_peak < R["long_peak"]:
        notes.append(f"The longest run tops out at {_rnd(long_peak)} mi to stay within a safe share of weekly mileage.")

    weeks = []
    taper_f = {1: [0.6], 2: [0.75, 0.55], 3: [0.8, 0.65, 0.5]}.get(taper, [])
    phase_idx = {}
    for i in range(n):
        ph = phases[i]
        idx = phase_idx[ph] = phase_idx.get(ph, -1) + 1
        mon = start + timedelta(weeks=i)
        if i < pre:
            f = min(1.0, i / ramp_weeks) if ramp_weeks else 1.0
            vol = start_w + (peak_w - start_w) * f
            lng = long_start + (long_peak - long_start) * f
            cutback = i % 4 == 3 and i < pre - 2
            if cutback:
                vol *= 0.8; lng *= 0.75
        elif ph == "taper":
            vol = peak_w * taper_f[idx]
            lng = long_peak * (taper_f[idx] - 0.1)
            cutback = False
        else:
            vol, lng, cutback = None, None, False
        lng = max(3.0, min(lng, vol * R["long_share"] + 1)) if vol else lng
        wu_save = WU_CD - min(WU_CD, max(1.5, _rnd(vol * 0.1))) if vol else 0   # shorter warm-ups on small weeks

        days = []
        if ph == "race":
            days = _race_week(key, R, race_day, mon, runs, paces)
        else:
            pattern = {(d + long_day - 6) % 7: kind for d, kind in PATTERNS[runs].items()}
            q_phase = "base" if ph == "base" or cutback else ("peak" if ph in ("peak", "taper") else "build")
            slots = {}
            for d, kind in sorted(pattern.items()):
                if kind in ("Q", "Q2"):
                    slots[d] = _quality(key, q_phase, idx, kind, paces)
            if ph == "taper" or vol < 30:   # a second hard day only once the week can carry it
                for d in [d for d, k in pattern.items() if k == "Q2"]:
                    slots.pop(d, None); pattern[d] = "E"
            fixed = sum(max(3.0, s[2] - wu_save) for s in slots.values() if s[2]) + _rnd(lng)
            n_easy = sum(1 for d, k in pattern.items() if k == "E" or (d in slots and slots[d][2] is None))
            easy_mi = max(3.0 if vol >= 20 else 2.0, _rnd((vol - fixed) / n_easy)) if n_easy else None
            for d in range(7):
                day = mon + timedelta(days=d)
                kind = pattern.get(d)
                if kind is None:
                    days.append({"date": day.isoformat(), "kind": "rest", "title": "Rest", "miles": 0, "detail": ""})
                elif kind == "L":
                    detail = f"Steady and easy ({_pace_txt(paces, 'E')})."
                    if key == "marathon" and ph == "peak" and idx < peak_n - 1:
                        detail = f"Last {min(8, _rnd(lng * 0.4, 1))} mi at {_pace_txt(paces, 'G' if paces and 'G' in paces else 'M')}, the rest easy."
                    elif key == "half" and ph == "peak":
                        detail = f"Last 3 mi at {_pace_txt(paces, 'G' if paces and 'G' in paces else 'T')}, the rest easy."
                    days.append({"date": day.isoformat(), "kind": "long", "title": "Long run", "miles": _rnd(lng), "detail": detail})
                elif d in slots:
                    k, title, mi, detail = slots[d]
                    if mi:
                        mi = max(3.0, mi - wu_save)
                    if ph == "taper":
                        mi = _rnd(mi * 0.75) if mi else None
                    days.append({"date": day.isoformat(), "kind": k, "title": title, "miles": mi if mi else easy_mi, "detail": detail})
                else:
                    days.append({"date": day.isoformat(), "kind": "easy", "title": "Easy run", "miles": easy_mi,
                                 "detail": f"Conversational ({_pace_txt(paces, 'E')})."})
        weeks.append({"n": i + 1, "start": mon.isoformat(), "phase": ph, "cutback": cutback,
                      "miles": round(sum(d["miles"] or 0 for d in days), 1), "days": days})

    return {
        "race": key, "label": R["label"], "name": (params.get("name") or "").strip()[:80] or R["label"],
        "race_date": race_day.isoformat(), "start": start.isoformat(), "weeks": weeks,
        "goal_seconds": goal, "paces": paces,
        "fitness": {"vdot": round(vd, 1) if vd else None, "source": src, "predicted_seconds": predicted},
        "peak_miles": max(w["miles"] for w in weeks), "notes": notes,
        "params": {"race": key, "race_date": race_day.isoformat(), "runs_per_week": runs, "long_day": long_day,
                   "current_weekly_mi": cur_w, "current_long_mi": cur_l, "goal_seconds": goal},
    }


def _race_week(key, R, race_day, mon, runs, paces):
    gp = _pace_txt(paces, "G" if paces and "G" in paces else ("M" if key == "marathon" else "T"))
    plan = {race_day: ("race", R["label"], round(R["meters"] / MILE, 1), "Race day. Start controlled, finish strong.")}
    extra = [(-2, ("easy", "Easy + strides", 3.0, "Short and easy, 4 × 20 s strides.")),
             (-4, ("tempo", "Tune-up", 5.0 if key in ("half", "marathon") else 4.0,
                   f"{'2 × 1 mi' if key in ('half', 'marathon') else '4 × 400 m'} at {gp}, easy otherwise.")),
             (-5, ("easy", "Easy run", 4.0, "Conversational."))]
    for off, item in extra[:max(0, runs - 1)]:
        d = race_day + timedelta(days=off)
        if d >= mon:
            plan[d] = item
    days = []
    for i in range(7):
        d = mon + timedelta(days=i)
        if d > race_day:
            break
        k, t, mi, det = plan.get(d, ("rest", "Rest", 0, ""))
        days.append({"date": d.isoformat(), "kind": k, "title": t, "miles": mi, "detail": det})
    return days


# ---------------------------------------------------------------- fitness from Strava
EFFORT_M = {"1 mile": MILE, "2 mile": 2 * MILE, "5K": 5000, "10K": 10000, "15K": 15000, "10 mile": 10 * MILE,
            "20K": 20000, "Half-Marathon": 21097.5, "Marathon": 42195}


def fitness_from_runs(runs, today=None):
    """runs: Strava activity docs. Recent mileage, longest run and the best VDOT from the last
    10 weeks of races and best efforts (1 mile or longer)."""
    today = today or date.today()
    runs = [r for r in runs if r.get("sport") in ("Run", "TrailRun", "VirtualRun")]
    since4 = (today - timedelta(days=28)).isoformat()
    since10 = (today - timedelta(days=70)).isoformat()
    recent = [r for r in runs if (r.get("date") or "") >= since4 and (r.get("date") or "") <= today.isoformat()]
    weekly = sum(r.get("distance_m") or 0 for r in recent) / MILE / 4
    longest = max((r.get("distance_m") or 0 for r in recent), default=0) / MILE
    best = None
    for r in runs:
        if not (since10 <= (r.get("date") or "") <= today.isoformat()):
            continue
        cands = [(b["name"], EFFORT_M[b["name"]], b["elapsed_s"]) for b in r.get("best_efforts") or []
                 if b.get("name") in EFFORT_M and b.get("elapsed_s")]
        if r.get("workout_type") == 1 and r.get("distance_m") and r.get("moving_s"):
            cands.append(("race", r["distance_m"], r.get("elapsed_s") or r["moving_s"]))
        for name, m, s in cands:
            v = vdot(m, s)
            if 20 < v < 90 and (best is None or v > best[0]):
                label = f"{r.get('name') or 'race'} ({fmt_time(s)})" if name == "race" else f"{name} best effort ({fmt_time(s)})"
                best = (v, f"{label} on {date.fromisoformat(r['date']).strftime('%b %-d')}")
    return {"weekly_mi": round(weekly, 1), "long_mi": round(longest, 1), "runs_4wk": len(recent),
            "vdot": round(best[0], 1) if best else None, "source": best[1] if best else None}


def attach_actuals(plan, runs):
    """Planned vs done: Strava runs matched to plan days by date."""
    by_day = {}
    for r in runs:
        by_day.setdefault(r.get("date"), []).append(r)
    for w in plan["weeks"]:
        done = 0.0
        for d in w["days"]:
            rs = by_day.get(d["date"], [])
            if rs:
                m = sum(r.get("distance_m") or 0 for r in rs)
                s = sum(r.get("moving_s") or 0 for r in rs)
                d["actual"] = {"miles": round(m / MILE, 2), "moving_s": s, "ids": [r["id"] for r in rs],
                               "pace_s_per_mi": round(s / (m / MILE)) if m else None}
                done += m / MILE
        w["done_miles"] = round(done, 1)
    return plan


# ---------------------------------------------------------------- routes
def init(app, _db, _firestore, _require_owner, _user_ref):
    global db, firestore, require_owner, user_ref
    db, firestore, require_owner, user_ref = _db, _firestore, _require_owner, _user_ref
    _register_routes()
    app.register_blueprint(bp)


def _plans():
    return user_ref().collection("race_plans")


def _fitness():
    import strava
    since = (date.today() - timedelta(days=75)).isoformat()
    return fitness_from_runs(strava.list_runs(since, date.today().isoformat()))


def _register_routes():
    @bp.get("/api/raceplans")
    @require_owner
    def list_plans():
        plans = []
        for s in _plans().stream():
            p = s.to_dict().get("plan") or {}
            plans.append({"id": s.id, "name": p.get("name"), "label": p.get("label"), "race_date": p.get("race_date"),
                          "weeks": len(p.get("weeks") or []), "goal_seconds": p.get("goal_seconds")})
        plans.sort(key=lambda p: p.get("race_date") or "")
        return jsonify(plans=plans, fitness=_fitness())

    def _build(body):
        fit = None if body.get("ignore_strava") else _fitness()
        return build_plan(body, fitness=fit if fit and fit.get("vdot") else None)

    @bp.post("/api/raceplans/preview")
    @require_owner
    def preview():
        try:
            return jsonify(_build(request.get_json(force=True) or {}))
        except ValueError as e:
            return jsonify(error=str(e)), 400

    @bp.post("/api/raceplans")
    @require_owner
    def save():
        try:
            plan = _build(request.get_json(force=True) or {})
        except ValueError as e:
            return jsonify(error=str(e)), 400
        pid = uuid.uuid4().hex[:12]
        _plans().document(pid).set({"plan": plan, "created": firestore.SERVER_TIMESTAMP})
        return jsonify(ok=True, id=pid)

    @bp.get("/api/raceplans/<pid>")
    @require_owner
    def get_plan(pid):
        snap = _plans().document(pid).get()
        if not snap.exists:
            return jsonify(error="plan not found"), 404
        import strava
        plan = snap.to_dict()["plan"]
        runs = strava.list_runs(plan["start"], plan["race_date"])
        return jsonify(attach_actuals(plan, runs) | {"id": pid})

    @bp.delete("/api/raceplans/<pid>")
    @require_owner
    def delete_plan(pid):
        _plans().document(pid).delete()
        return jsonify(ok=True)

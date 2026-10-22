"""Goals: SMART goals that track themselves from Finances, Health and Education, a starter library, and a coach.

  GET    /api/goals                    every goal with its current value, progress, pace status and next step
  GET    /api/goals/metrics            linked metrics with their current values, and logged exercise names
  GET    /api/goals/library            the starter library (goals_library.py)
  POST   /api/goals                    create (body: a goal);  PUT /api/goals/<id> replace editable fields
  DELETE /api/goals/<id>
  POST   /api/goals/<id>/log           record a value for a goal you update yourself {value, date?, note?}
  POST   /api/goals/import             bulk add {goals: [...]} (a private file kept outside the repo)
  POST   /api/goals/coach              chat that shapes a SMART goal and a step plan; it proposes, the user saves

A goal (users/{uid}/goals/{id}):
  title, area (finance|health|education|personal), category, status (active|someday|done), done_on,
  smart {specific, measurable, achievable, relevant, time_bound}, why, library_id,
  measure {kind: number|steps, metric (linked, e.g. "fin.net_worth", "health.lift_top:Bench Press"),
           unit, start, target, current (number goals you update yourself)},
  start_date, due_date, steps [{id, title, due, done, done_on, note}], log [{date, value, note}]

Progress is never guessed: a number goal with no current value or no target shows no progress.
Pace: actual progress vs straight-line progress from start_date to due_date (number goals), or overdue steps.
"""
import json
import os
import re
from datetime import date, timedelta

from flask import Blueprint, jsonify, request

import goals_library as LIB

bp = Blueprint("goals", __name__)
db = firestore = require_owner = user_ref = None
AREAS = tuple(LIB.AREAS)
STATUSES = ("active", "someday", "done")
SMART_KEYS = ("specific", "measurable", "achievable", "relevant", "time_bound")
MAX_STEPS = 60
MAX_LOG = 400
PACE_SLACK = 0.05     # within 5 points of the straight line still counts as on track


def init(app, _db, _firestore, _require_owner, _user_ref):
    global db, firestore, require_owner, user_ref
    db, firestore, require_owner, user_ref = _db, _firestore, _require_owner, _user_ref
    _register_routes()
    app.register_blueprint(bp)


def _col():
    return user_ref().collection("goals")


# ---------------------------------------------------------------- linked metrics
# key -> (label, area, unit, takes an exercise name)
METRICS = {
    "fin.net_worth": ("Net worth", "finance", "$", False),
    "fin.invested": ("Invested assets", "finance", "$", False),
    "fin.cash": ("Cash (checking and savings)", "finance", "$", False),
    "health.lift_top": ("Heaviest set", "health", "lb", True),
    "health.lift_e1rm": ("Estimated 1-rep max", "health", "lb", True),
    "health.workouts_since_start": ("Workouts logged since the goal started", "health", "sessions", False),
    "health.run_miles_since_start": ("Miles run since the goal started", "health", "mi", False),
    "health.run_longest": ("Longest run", "health", "mi", False),
    "health.recovery_30d": ("30-day average recovery", "health", "%", False),
    "health.sleep_30d": ("30-day average sleep", "health", "hr", False),
    "edu.gpa": ("Cumulative GPA", "education", "GPA", False),
    "edu.credits": ("Credits earned", "education", "credits", False),
}
MILE = 1609.344


def split_metric(metric):
    key, _, arg = (metric or "").partition(":")
    return key, arg.strip()


def metric_label(metric):
    key, arg = split_metric(metric)
    if key not in METRICS:
        return None
    return f"{METRICS[key][0]}: {arg}" if arg else METRICS[key][0]


class Metrics:
    """Reads current values from the other sections, once per request. A value is
    {value, unit, as_of, source} or None when that section has no data yet."""

    def __init__(self):
        self._cache = {}

    def _once(self, name, fn):
        if name not in self._cache:
            try:
                self._cache[name] = fn()
            except Exception:          # a section with no data (or no setup) must not break Goals
                self._cache[name] = None
        return self._cache[name]

    def _net_worth(self):
        import finance, portfolio
        finance._ready()
        return portfolio.net_worth()

    def _sessions(self):
        import training
        return training.load_sessions()

    def _runs(self):
        import strava
        return strava.list_runs()

    def _health(self):
        import health
        today = date.today()
        return health.health_summary((today - timedelta(days=30)).isoformat(), today.isoformat())

    def _edu(self):
        import education
        return education.education_state()

    def exercises(self):
        names = {}
        for s in self._once("sessions", self._sessions) or []:
            for ex in s.get("exercises") or []:
                names.setdefault(ex["name"].lower(), ex["name"])
        return sorted(names.values(), key=str.lower)

    def value(self, metric, since=None):
        key, arg = split_metric(metric)
        if key not in METRICS:
            return None
        unit = METRICS[key][2]
        out = lambda v, as_of, source: None if v is None else {"value": round(float(v), 2), "unit": unit, "as_of": as_of, "source": source}
        today = date.today().isoformat()
        if key.startswith("fin."):
            nw = self._once("net_worth", self._net_worth)
            if not nw:
                return None
            if key == "fin.net_worth":
                return out(nw.get("net_worth") if any(a.get("balance") is not None for a in nw.get("accounts", [])) else None,
                           nw.get("as_of"), "Finances → Net Worth")
            groups = {"fin.invested": ("investment", "retirement"), "fin.cash": ("cash",)}[key]
            rows = [a for a in nw.get("accounts", []) if a.get("group") in groups and not a.get("liability") and a.get("balance") is not None]
            if key == "fin.cash":
                rows = [a for a in rows if a.get("type") in ("checking", "savings")]
            if not rows:
                return None
            return out(sum(a["balance"] for a in rows), max(a["as_of"] for a in rows if a.get("as_of")), "Finances → Net Worth")
        if key in ("health.lift_top", "health.lift_e1rm"):
            if not arg:
                return None
            import training
            sessions = self._once("sessions", self._sessions) or []
            best, when = None, None
            for s in sessions:
                for ex in s.get("exercises") or []:
                    if ex["name"].lower() != arg.lower():
                        continue
                    for x in ex.get("sets") or []:
                        w, r = x.get("weight"), x.get("reps")
                        if x.get("warmup") or not w or w <= 0 or training.set_unit(ex, x) != "lb":
                            continue
                        v = w if key == "health.lift_top" else (training.e1rm(w, r) if r else None)
                        if v is not None and (best is None or v > best):
                            best, when = v, s["date"]
            return out(best, when, f"Health → History ({arg})")
        if key == "health.workouts_since_start":
            sessions = self._once("sessions", self._sessions)
            if sessions is None:
                return None
            n = sum(1 for s in sessions if not since or s["date"] >= since)
            return out(n, today, "Health → History")
        if key in ("health.run_miles_since_start", "health.run_longest"):
            runs = self._once("runs", self._runs)
            if not runs:
                return None
            if key == "health.run_longest":
                r = max(runs, key=lambda r: r.get("distance_m") or 0)
                return out((r.get("distance_m") or 0) / MILE, r.get("date"), "Health → Running")
            m = sum(r.get("distance_m") or 0 for r in runs if not since or (r.get("date") or "") >= since)
            return out(m / MILE, today, "Health → Running")
        if key in ("health.recovery_30d", "health.sleep_30d"):
            h = self._once("health", self._health)
            v = ((h or {}).get("averages") or {}).get("recovery" if key == "health.recovery_30d" else "sleep_hours")
            return out(v, today, "Health → Overview (WHOOP, last 30 days)")
        if key in ("edu.gpa", "edu.credits"):
            e = self._once("edu", self._edu)
            if not e or not e.get("has_data"):
                return None
            c = e["computed"]
            return out(c.get("gpa_actual") if key == "edu.gpa" else (c.get("credits") or {}).get("earned"), today, "Education")
        return None

    def catalog(self):
        rows = []
        for key, (label, area, unit, takes_arg) in METRICS.items():
            v = None if takes_arg or key.endswith("_since_start") else self.value(key)
            rows.append({"key": key, "label": label, "area": area, "unit": unit, "takes_exercise": takes_arg,
                         "since_start": key.endswith("_since_start"), "current": v})
        return rows


# ---------------------------------------------------------------- cleaning
def _str(v, n=300):
    return str(v).strip()[:n] if v not in (None, "") else ""


def _num(v):
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"not a number: {v}")
    if f != f or abs(f) > 1e12:
        raise ValueError(f"not a usable number: {v}")
    return f


def _date(v):
    if v in (None, ""):
        return None
    s = str(v)[:10]
    try:
        date.fromisoformat(s)
    except ValueError:
        raise ValueError(f"not a date (YYYY-MM-DD): {v}")
    return s


def _id():
    return os.urandom(5).hex()


def clean_goal(b, existing=None):
    """Validate a goal from the browser, the coach or an import file. Raises ValueError."""
    ex = existing or {}
    title = _str(b.get("title", ex.get("title")), 140)
    if not title:
        raise ValueError("a goal needs a title")
    area = b.get("area", ex.get("area")) or "personal"
    if area not in AREAS:
        raise ValueError(f"area must be one of {', '.join(AREAS)}")
    status = b.get("status", ex.get("status")) or "active"
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    m = (b.get("measure") if "measure" in b else ex.get("measure")) or {}
    kind = m.get("kind") or ("number" if m.get("metric") or m.get("target") is not None else "steps")
    if kind not in ("number", "steps"):
        raise ValueError("measure.kind must be number or steps")
    metric = _str(m.get("metric"), 80) or None
    if metric and split_metric(metric)[0] not in METRICS:
        raise ValueError(f"unknown linked metric: {metric}")
    if metric and METRICS[split_metric(metric)[0]][3] and not split_metric(metric)[1]:
        raise ValueError("pick the exercise for a lift goal")
    measure = {"kind": kind, "metric": metric if kind == "number" else None,
               "unit": _str(m.get("unit"), 20) or (METRICS[split_metric(metric)[0]][2] if metric else ""),
               "start": _num(m.get("start")), "target": _num(m.get("target")),
               "current": None if metric else _num(m.get("current"))}
    smart_in = (b.get("smart") if "smart" in b else ex.get("smart")) or {}
    smart = {k: _str((smart_in or {}).get(k), 600) for k in SMART_KEYS}
    steps = []
    for s in (b.get("steps") if "steps" in b else ex.get("steps")) or []:
        if isinstance(s, str):
            s = {"title": s}
        t = _str(s.get("title"), 200)
        if not t:
            continue
        done = bool(s.get("done"))
        steps.append({"id": _str(s.get("id"), 20) or _id(), "title": t, "due": _date(s.get("due")), "done": done,
                      "done_on": _date(s.get("done_on")) if done else None, "note": _str(s.get("note"), 400)})
    if len(steps) > MAX_STEPS:
        raise ValueError(f"at most {MAX_STEPS} steps")
    start_date = _date(b.get("start_date", ex.get("start_date"))) or date.today().isoformat()
    due_date = _date(b.get("due_date", ex.get("due_date")))
    if due_date and due_date < start_date:
        raise ValueError("the deadline is before the start date")
    done_on = _date(b.get("done_on", ex.get("done_on"))) if status == "done" else None
    lib = _str(b.get("library_id", ex.get("library_id")), 40) or None
    return {"title": title, "area": area, "category": _str(b.get("category", ex.get("category")), 40),
            "status": status, "done_on": done_on, "why": _str(b.get("why", ex.get("why")), 600), "smart": smart,
            "measure": measure, "start_date": start_date, "due_date": due_date, "steps": steps,
            "library_id": lib if lib in LIB.BY_ID else None, "log": (ex.get("log") or [])[-MAX_LOG:]}


# ---------------------------------------------------------------- progress and pace
def evaluate(g, metrics, today=None):
    """Current value, progress (0..1 or None), expected progress on the straight line, status and next step."""
    today = today or date.today()
    m = g.get("measure") or {}
    steps = g.get("steps") or []
    cur = None
    if m.get("kind") == "number":
        if m.get("metric"):
            cur = metrics.value(m["metric"], since=g.get("start_date"))
        elif m.get("current") is not None:
            last = (g.get("log") or [{}])[-1]
            cur = {"value": m["current"], "unit": m.get("unit"), "as_of": last.get("date"), "source": "Entered by you"}
        start, target = m.get("start"), m.get("target")
        if m.get("metric") and split_metric(m["metric"])[0].endswith("_since_start") and start is None:
            start = 0.0                          # a count since the start date begins at zero by definition
        if cur is None or target is None or start is None:
            progress = None
        elif target == start:
            progress = 1.0 if cur["value"] == target else 0.0
        else:
            progress = max(0.0, min(1.0, (cur["value"] - start) / (target - start)))
    else:
        progress = (sum(1 for s in steps if s.get("done")) / len(steps)) if steps else None

    expected = None
    sd, dd = g.get("start_date"), g.get("due_date")
    if sd and dd and dd > sd:
        span = (date.fromisoformat(dd) - date.fromisoformat(sd)).days
        expected = max(0.0, min(1.0, (today - date.fromisoformat(sd)).days / span))

    open_steps = [s for s in steps if not s.get("done")]
    overdue_steps = [s for s in open_steps if s.get("due") and s["due"] < today.isoformat()]
    nxt = sorted(open_steps, key=lambda s: (s.get("due") is None, s.get("due") or "", steps.index(s)))
    status = g.get("status") or "active"
    if status in ("done", "someday"):
        pace = status
    elif progress is not None and progress >= 1:
        pace = "reached"
    elif dd and dd < today.isoformat():
        pace = "overdue"
    elif overdue_steps:
        pace = "behind"
    elif m.get("kind") == "number" and progress is not None and expected is not None:
        pace = "on_track" if progress + PACE_SLACK >= expected else "behind"
    elif m.get("kind") == "steps" and progress is not None and any(s.get("due") for s in steps):
        pace = "on_track"
    else:
        pace = "no_pace" if progress is not None else "no_data"
    return {"current": cur, "progress": None if progress is None else round(progress, 4),
            "expected": None if expected is None else round(expected, 4), "pace": pace,
            "next_step": nxt[0] if nxt else None, "overdue_steps": len(overdue_steps),
            "steps_done": len(steps) - len(open_steps), "steps_total": len(steps),
            "metric_label": metric_label(m.get("metric")),
            "days_left": (date.fromisoformat(dd) - today).days if dd else None}


def load_goals():
    return [d.to_dict() | {"id": d.id} for d in _col().stream()]


def goals_view(metrics=None):
    metrics = metrics or Metrics()
    order = {"active": 0, "someday": 1, "done": 2}
    goals = [g | {"eval": evaluate(g, metrics)} for g in load_goals()]
    goals.sort(key=lambda g: (order.get(g.get("status"), 9), g.get("due_date") or "9999", g["title"].lower()))
    counts = {s: sum(1 for g in goals if g.get("status") == s) for s in STATUSES}
    counts["behind"] = sum(1 for g in goals if g["eval"]["pace"] in ("behind", "overdue"))
    return {"goals": goals, "counts": counts, "areas": LIB.AREAS, "today": date.today().isoformat()}


def _snapshot_start(data, metrics):
    """A linked number goal with no start value starts from today's reading, so progress counts from now."""
    m = data["measure"]
    if m["kind"] == "number" and m.get("metric") and m.get("start") is None \
            and not split_metric(m["metric"])[0].endswith("_since_start"):
        v = metrics.value(m["metric"])
        if v is not None:
            m["start"] = v["value"]
            m["start_note"] = f"Reading on {date.today().isoformat()}"
    return data


# ---------------------------------------------------------------- coach (Claude)
COACH_TOOLS = [
    {"name": "get_goals", "description": "The user's current goals with progress, pace and steps.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_metrics", "description": "Numbers LifeTracker can track automatically, with today's value where the app has data "
                                          "(net worth, invested, cash, workouts, runs, WHOOP recovery and sleep, GPA, credits), plus the "
                                          "exercise names in the user's workout log (for lift goals). Use it before stating any of the user's numbers.",
     "input_schema": {"type": "object", "properties": {"exercise": {"type": "string", "description": "Optional: also return the heaviest set and e1RM for this exercise"}}}},
    {"name": "search_library", "description": "Search the 100-goal starter library by text or category.",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}, "category": {"type": "string"}}}},
    {"name": "propose_goal",
     "description": "Show the user a finished SMART goal to save (or, with id, an update to an existing goal). Nothing is saved until they press Save. "
                    "Call it only once the goal is specific, has a deadline, and has a plan of short steps.",
     "input_schema": {"type": "object", "required": ["title", "area", "smart", "measure", "steps"], "properties": {
         "id": {"type": "string", "description": "Existing goal id when revising one"},
         "title": {"type": "string"}, "area": {"type": "string", "enum": list(AREAS)}, "category": {"type": "string"},
         "library_id": {"type": "string"}, "why": {"type": "string"},
         "smart": {"type": "object", "properties": {k: {"type": "string"} for k in SMART_KEYS}},
         "measure": {"type": "object", "properties": {
             "kind": {"type": "string", "enum": ["number", "steps"]},
             "metric": {"type": "string", "description": "Linked metric key from get_metrics; lifts as health.lift_top:<Exercise>"},
             "unit": {"type": "string"}, "start": {"type": "number"}, "target": {"type": "number"},
             "current": {"type": "number"}}},
         "start_date": {"type": "string", "description": "YYYY-MM-DD"}, "due_date": {"type": "string", "description": "YYYY-MM-DD"},
         "steps": {"type": "array", "items": {"type": "object", "required": ["title"], "properties": {
             "title": {"type": "string"}, "due": {"type": "string", "description": "YYYY-MM-DD"}, "note": {"type": "string"}}}}}}},
]


def coach_prompt(goal=None):
    focus = f"\nThe user opened the coach from this goal (id {goal['id']}): {json.dumps({k: goal.get(k) for k in ('title', 'smart', 'measure', 'start_date', 'due_date', 'steps')}, default=str)[:4000]}" if goal else ""
    return f"""You are the goals coach inside LifeTracker, the user's personal dashboard for finances, health, education and goals.
Today is {date.today().isoformat()}.{focus}

Your job: turn an ambition into a SMART goal (Specific, Measurable, Achievable, Relevant, Time-bound) and break it into
short intervals the user can act on this week.

How to work:
- Ask one or two short questions at a time: where they are now (baseline), the deadline, how much time per week, constraints.
- Never invent the user's numbers. Use get_metrics for anything LifeTracker tracks; otherwise ask. Leave unknowns blank.
- Prefer a linked metric when one fits (a lift, net worth, GPA, miles run), so progress updates on its own.
- Plan in short intervals: the next 4 weeks as weekly steps, then monthly steps, then the final milestones. Each step is
  one concrete action or checkpoint with a date. Keep it to about 6–14 steps.
- For physically demanding goals (combat sports, altitude, Ironman, big weight changes), add a step to get medical
  clearance or a qualified coach, once, without lecturing.
- When the goal is ready, call propose_goal. The user saves it themselves. Then summarize the plan in two or three lines.
- Be brief and direct. Plain text, short lists. No emoji."""


def run_coach(messages, goal=None):
    import assistant
    convo = [{"role": m["role"], "content": str(m["content"])[:6000]}
             for m in messages[-24:] if m.get("role") in ("user", "assistant") and m.get("content")]
    if not convo or convo[-1]["role"] != "user":
        raise ValueError("last message must be from the user")
    metrics = Metrics()

    def get_metrics(exercise=None):
        out = {"metrics": metrics.catalog(), "exercises": metrics.exercises()}
        if exercise:
            out["lift"] = {"heaviest_set": metrics.value(f"health.lift_top:{exercise}"),
                           "e1rm": metrics.value(f"health.lift_e1rm:{exercise}")}
        return out

    def search_library(text=None, category=None):
        t = (text or "").lower()
        return [g for g in LIB.LIBRARY if (not t or t in g["title"].lower()) and (not category or g["category"].lower() == category.lower())][:30]

    tools = {"get_goals": lambda: [{k: g.get(k) for k in ("id", "title", "status", "due_date", "measure")} | {"eval": g["eval"]}
                                   for g in goals_view(metrics)["goals"]],
             "get_metrics": get_metrics, "search_library": search_library}
    proposal = None
    for _ in range(6):
        resp = assistant.client().messages.create(model=assistant.MODEL, max_tokens=2500, system=coach_prompt(goal),
                                                  tools=COACH_TOOLS, messages=convo)
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            return text, proposal
        convo.append({"role": "assistant", "content": resp.content})
        results = []
        for blk in resp.content:
            if blk.type != "tool_use":
                continue
            try:
                if blk.name == "propose_goal":
                    inp = dict(blk.input or {})
                    gid = inp.pop("id", None)
                    existing = None
                    if gid:
                        snap = _col().document(gid).get()
                        existing = snap.to_dict() if snap.exists else None
                    proposal = clean_goal(inp, existing) | ({"id": gid} if existing else {})
                    proposal.pop("log", None)
                    content = "Shown to the user with a Save button. Not saved yet."
                else:
                    content = json.dumps(tools[blk.name](**(blk.input or {})), default=str)[:40000]
                results.append({"type": "tool_result", "tool_use_id": blk.id, "content": content})
            except Exception as e:
                results.append({"type": "tool_result", "tool_use_id": blk.id, "content": f"Error: {e}", "is_error": True})
        convo.append({"role": "user", "content": results})
    return "That took more steps than I'm allowed in one turn. Try again with a narrower ask.", proposal


# ---------------------------------------------------------------- routes
def _register_routes():
    @bp.get("/api/goals")
    @require_owner
    def get_goals():
        return jsonify(goals_view())

    @bp.get("/api/goals/metrics")
    @require_owner
    def get_metrics():
        m = Metrics()
        out = {"metrics": m.catalog(), "exercises": m.exercises()}
        ex = request.args.get("exercise")
        if ex:
            out["lift"] = {"health.lift_top": m.value(f"health.lift_top:{ex}"), "health.lift_e1rm": m.value(f"health.lift_e1rm:{ex}")}
        return jsonify(out)

    @bp.get("/api/goals/library")
    @require_owner
    def get_library():
        return jsonify(library=LIB.LIBRARY, categories=LIB.CATEGORIES, areas=LIB.AREAS)

    @bp.post("/api/goals")
    @require_owner
    def create_goal():
        try:
            data = clean_goal(request.get_json(force=True) or {})
        except ValueError as e:
            return jsonify(error=str(e)), 400
        data = _snapshot_start(data, Metrics())
        ref = _col().document()
        ref.set(data | {"created": date.today().isoformat()})
        return jsonify(goals_view() | {"id": ref.id})

    @bp.put("/api/goals/<gid>")
    @require_owner
    def put_goal(gid):
        ref = _col().document(gid)
        snap = ref.get()
        if not snap.exists:
            return jsonify(error="goal not found"), 404
        cur = snap.to_dict()
        try:
            data = clean_goal(request.get_json(force=True) or {}, cur)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        old = cur.get("measure") or {}
        if old.get("start_note") and (old.get("metric"), old.get("start")) == (data["measure"]["metric"], data["measure"]["start"]):
            data["measure"]["start_note"] = old["start_note"]
        if data["status"] == "done" and not data["done_on"]:
            data["done_on"] = date.today().isoformat()
        ref.set(_snapshot_start(data, Metrics()) | {"created": cur.get("created")})
        return jsonify(goals_view())

    @bp.delete("/api/goals/<gid>")
    @require_owner
    def delete_goal(gid):
        _col().document(gid).delete()
        return jsonify(goals_view())

    @bp.post("/api/goals/<gid>/log")
    @require_owner
    def log_value(gid):
        ref = _col().document(gid)
        snap = ref.get()
        if not snap.exists:
            return jsonify(error="goal not found"), 404
        g = snap.to_dict()
        b = request.get_json(force=True) or {}
        try:
            v = _num(b.get("value"))
            d = _date(b.get("date")) or date.today().isoformat()
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if v is None:
            return jsonify(error="enter a value"), 400
        if (g.get("measure") or {}).get("metric"):
            return jsonify(error="this goal reads its value from your data"), 400
        log = sorted((g.get("log") or []) + [{"date": d, "value": v, "note": _str(b.get("note"), 200)}], key=lambda x: x["date"])[-MAX_LOG:]
        measure = (g.get("measure") or {}) | {"current": log[-1]["value"]}
        ref.set({"log": log, "measure": measure}, merge=True)
        return jsonify(goals_view())

    @bp.post("/api/goals/import")
    @require_owner
    def import_goals():
        b = request.get_json(force=True) or {}
        items = b.get("goals") if isinstance(b, dict) else b
        if not isinstance(items, list) or not items:
            return jsonify(error="the file has no goals list"), 400
        existing = {(g.get("title") or "").lower(): g["id"] for g in load_goals()}
        cleaned = []
        try:
            for i, g in enumerate(items[:200]):
                cleaned.append(clean_goal(g))
        except ValueError as e:
            return jsonify(error=f"goal {i + 1}: {e}"), 400
        metrics, added, updated = Metrics(), 0, 0
        for data in cleaned:
            gid = existing.get(data["title"].lower())
            ref = _col().document(gid) if gid else _col().document()
            ref.set(_snapshot_start(data, metrics) | {"created": date.today().isoformat()}, merge=bool(gid))
            updated += bool(gid)
            added += not gid
        return jsonify(goals_view() | {"added": added, "updated": updated})

    @bp.post("/api/goals/coach")
    @require_owner
    def coach():
        b = request.get_json(force=True) or {}
        goal = None
        if b.get("goal_id"):
            snap = _col().document(str(b["goal_id"])[:40]).get()
            goal = snap.to_dict() | {"id": snap.id} if snap.exists else None
        try:
            reply, proposal = run_coach(b.get("messages") or [], goal)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(reply=reply or ("Here's the goal. Save it if it looks right." if proposal else ""), proposal=proposal)

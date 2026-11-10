"""Developer dashboard data: aggregate app usage, Claude usage, and the developer's task / idea list.

Usage is counted, never content. The site pings POST /api/usage/ping once a minute while a signed-in tab is visible
and in use, naming only the section on screen. That adds up, per day (top-level usage_days/{YYYY-MM-DD}):
  {date, minutes, sessions, users: {uid: minutes}, sections: {section: minutes},
   claude: {feature: {calls, input, output}}, models: {model: calls}}
and per account in signups/{uid}: minutes, sessions, days (active days), last_day, claude_calls.
The demo and the developer account are not counted. Claude calls are counted in assistant.client() from the
token totals the API returns; the request text and answers are never stored here.

Tasks (top-level dev_tasks/{id}): {title, notes, kind, area, priority, status, created, updated}, editable by the
owner and the developer account only.
"""
import datetime as _dt
import time
from zoneinfo import ZoneInfo
from flask import g, has_request_context, jsonify, request

import access

real_db = None
TZ = ZoneInfo("America/New_York")
PING_GAP = 45                 # seconds: two tabs (or a fast timer) can't count one minute twice
NEW_SESSION_AFTER = 30 * 60   # a ping after this long without one starts a new session
_last_ping = {}               # uid -> time of the last counted ping (per instance)

SECTIONS = {"home", "finances", "health", "education", "goals", "sports", "travel", "social", "golf", "movies",
            "recipes", "chess", "settings", "intro"}
# Claude calls are labelled by the API route that made them
FEATURES = (("/api/chat", "Finance chat"), ("/api/goals/coach", "Goals coach"), ("/api/edu/syllabi", "Syllabus reader"),
            ("/api/edu/plan", "Study planner"), ("/api/edu/gmail", "School inbox"),
            ("/api/edu", "Education"))

KINDS = ("idea", "task", "bug")
STATUSES = ("idea", "todo", "doing", "done")
PRIORITIES = ("high", "medium", "low")
AREAS = ("general", "finances", "health", "education", "goals", "sports", "travel", "social", "golf", "movies",
         "recipes", "chess", "developer", "design", "backend")
# "Start with the add-ons" fills an empty list with the sections already marked in development in the app
STARTERS = [
    ("Golf add-on", "golf", "Rounds and a WHS handicap index; 18Birdies and GHIN have no public API, so rounds are entered and the official index typed in."),
    ("Movies add-on", "movies", "Letterboxd diary from the profile RSS feed and the data export."),
    ("Recipes add-on", "recipes", "Save recipes from schema.org Recipe pages and log a serving to Food."),
    ("Chess add-on", "chess", "Chess.com ratings, history and record from the public API."),
    ("Sports betting", "sports", "Betting area of Sports, plan only today."),
]


def today(now=None):
    return _dt.datetime.fromtimestamp(now or time.time(), TZ).date().isoformat()


def _day(date):
    return real_db.collection("usage_days").document(date)


def _read(ref):
    try:
        snap = ref.get()
        return (snap.to_dict() or {}) if snap.exists else {}
    except Exception:
        return {}


def _counted():
    """Only real signed-in people are counted: not the demo, the developer account or a request with no uid."""
    return (real_db is not None and has_request_context() and g.get("demo_db") is None and g.get("uid")
            and getattr(g, "role", None) not in ("admin", "pending"))


def ping(uid, section, start=False, now=None):
    """One visible, active minute (or the start of a session). Returns what was counted."""
    now = now or time.time()
    section = section if section in SECTIONS else "home"
    last = _last_ping.get(uid, 0)
    new_session = bool(start) or now - last > NEW_SESSION_AFTER
    minute = not start and now - last >= PING_GAP
    if not minute and not new_session:
        return {"minute": False, "session": False}
    _last_ping[uid] = now
    date = today(now)
    ref = _day(date)
    d = _read(ref)
    users, sections = dict(d.get("users") or {}), dict(d.get("sections") or {})
    users[uid] = users.get(uid, 0) + (1 if minute else 0)
    if minute:
        sections[section] = sections.get(section, 0) + 1
    ref.set({"date": date, "minutes": (d.get("minutes") or 0) + (1 if minute else 0),
             "sessions": (d.get("sessions") or 0) + (1 if new_session else 0), "users": users, "sections": sections},
            merge=True)
    sref = real_db.collection("signups").document(uid)
    s = _read(sref)
    row = {"minutes": (s.get("minutes") or 0) + (1 if minute else 0),
           "sessions": (s.get("sessions") or 0) + (1 if new_session else 0)}
    if s.get("last_day") != date:
        row.update(days=(s.get("days") or 0) + 1, last_day=date)
    sref.set(row, merge=True)
    return {"minute": minute, "session": new_session}


def feature_for(path):
    for prefix, name in FEATURES:
        if path == prefix or path.startswith(prefix + "/"):
            return name
    return "Other"


def record_claude(resp, model=None):
    """Count one Claude API call (tokens from the response's usage block). Never raises."""
    try:
        if not _counted():
            return
        u = getattr(resp, "usage", None)
        tin = int(getattr(u, "input_tokens", 0) or 0) + int(getattr(u, "cache_read_input_tokens", 0) or 0) \
            + int(getattr(u, "cache_creation_input_tokens", 0) or 0)
        tout = int(getattr(u, "output_tokens", 0) or 0)
        model = str(getattr(resp, "model", None) or model or "unknown")
        feat = feature_for(request.path)
        date = today()
        ref = _day(date)
        d = _read(ref)
        claude, models = dict(d.get("claude") or {}), dict(d.get("models") or {})
        f = dict(claude.get(feat) or {})
        claude[feat] = {"calls": (f.get("calls") or 0) + 1, "input": (f.get("input") or 0) + tin,
                        "output": (f.get("output") or 0) + tout}
        models[model] = (models.get(model) or 0) + 1
        ref.set({"date": date, "claude": claude, "models": models}, merge=True)
        sref = real_db.collection("signups").document(g.uid)
        sref.set({"claude_calls": (_read(sref).get("claude_calls") or 0) + 1}, merge=True)
    except Exception:
        pass                                   # counting must never break the feature


def summary(days=30, now=None):
    """The dashboard's usage block: one row per day (oldest first) and totals over the window."""
    days = max(1, min(int(days or 30), 120))
    end = _dt.datetime.fromtimestamp(now or time.time(), TZ).date()
    dates = [(end - _dt.timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    rows, active, sections, claude, models = [], set(), {}, {}, {}
    week = set(dates[-7:])
    active7 = set()
    for date in dates:
        d = _read(_day(date))
        users = {u for u, _m in (d.get("users") or {}).items()}
        active |= users
        if date in week:
            active7 |= users
        for k, v in (d.get("sections") or {}).items():
            sections[k] = sections.get(k, 0) + (v or 0)
        calls = 0
        for k, v in (d.get("claude") or {}).items():
            c = claude.setdefault(k, {"calls": 0, "input": 0, "output": 0})
            for f in c:
                c[f] += (v or {}).get(f) or 0
            calls += (v or {}).get("calls") or 0
        for k, v in (d.get("models") or {}).items():
            models[k] = models.get(k, 0) + (v or 0)
        rows.append({"date": date, "active": len(users), "minutes": d.get("minutes") or 0,
                     "sessions": d.get("sessions") or 0, "claude_calls": calls})
    totals = {"active_today": rows[-1]["active"], "active_7": len(active7), "active_window": len(active),
              "minutes": sum(r["minutes"] for r in rows), "sessions": sum(r["sessions"] for r in rows),
              "claude_calls": sum(c["calls"] for c in claude.values()),
              "claude_input": sum(c["input"] for c in claude.values()),
              "claude_output": sum(c["output"] for c in claude.values())}
    return {"days": days, "series": rows, "totals": totals,
            "sections": sorted(({"id": k, "minutes": v} for k, v in sections.items()), key=lambda r: -r["minutes"]),
            "claude": sorted(({"feature": k, **v} for k, v in claude.items()), key=lambda r: -r["calls"]),
            "models": sorted(({"model": k, "calls": v} for k, v in models.items()), key=lambda r: -r["calls"]),
            "tracking_since": _tracking_since()}


def _tracking_since():
    try:
        first = next(iter(real_db.collection("usage_days").order_by("date").limit(1).stream()), None)
        return first.id if first else None
    except Exception:
        try:
            ids = [d.id for d in real_db.collection("usage_days").stream()]
            return min(ids) if ids else None
        except Exception:
            return None


def clean_task(b, old=None):
    """A task from the browser: known values only, short text. Returns (task, error)."""
    old = old or {}
    t = dict(old)
    if "title" in b or not old:
        title = str(b.get("title") or "").strip()[:200]
        if not title:
            return None, "Give it a title"
        t["title"] = title
    if "notes" in b:
        t["notes"] = str(b.get("notes") or "").strip()[:4000]
    for key, allowed, default in (("kind", KINDS, "idea"), ("status", STATUSES, None), ("priority", PRIORITIES, "medium"),
                                  ("area", AREAS, "general")):
        if key in b or key not in t:
            v = b.get(key)
            if key == "status" and v not in allowed:
                v = t.get("status") or ("idea" if t.get("kind", b.get("kind")) == "idea" else "todo")
            t[key] = v if v in allowed else (t.get(key) or default)
    t.setdefault("notes", "")
    return t, None


def _tasks():
    return real_db.collection("dev_tasks")


def list_tasks():
    rank = {s: i for i, s in enumerate(PRIORITIES)}
    rows = [{"id": d.id, **(d.to_dict() or {})} for d in _tasks().stream()]
    rows.sort(key=lambda t: (rank.get(t.get("priority"), 1), -(t.get("created") or 0)))
    return rows


def init(app, _real_db, require_owner):
    global real_db
    real_db = _real_db

    @app.post("/api/usage/ping")
    @require_owner
    def usage_ping():
        """{section, start}: one visible minute on the site (or the start of a session). Counts only, no content."""
        if not _counted():
            return jsonify(counted=False)
        b = request.get_json(force=True, silent=True) or {}
        return jsonify(counted=True, **ping(g.uid, str(b.get("section") or ""), bool(b.get("start"))))

    def guard():
        _, err = access.admin_claims()
        return err

    @app.get("/api/admin/usage")
    def admin_usage():
        err = guard()
        if err:
            return err
        return jsonify(summary(request.args.get("days", 30)))

    @app.get("/api/admin/tasks")
    def admin_tasks():
        err = guard()
        if err:
            return err
        return jsonify(tasks=list_tasks(), kinds=KINDS, statuses=STATUSES, priorities=PRIORITIES, areas=AREAS)

    @app.post("/api/admin/tasks")
    def admin_task_add():
        err = guard()
        if err:
            return err
        t, bad = clean_task(request.get_json(force=True, silent=True) or {})
        if bad:
            return jsonify(error=bad), 400
        now = time.time()
        ref = _tasks().document()
        ref.set({**t, "created": now, "updated": now})
        return jsonify(task={"id": ref.id, **t, "created": now, "updated": now}, tasks=list_tasks())

    @app.put("/api/admin/tasks/<tid>")
    def admin_task_edit(tid):
        err = guard()
        if err:
            return err
        ref = _tasks().document(tid)
        old = _read(ref)
        if not old:
            return jsonify(error="not found"), 404
        t, bad = clean_task(request.get_json(force=True, silent=True) or {}, old)
        if bad:
            return jsonify(error=bad), 400
        t["updated"] = time.time()
        if t.get("status") == "done" and old.get("status") != "done":
            t["done_at"] = t["updated"]
        ref.set(t)
        return jsonify(tasks=list_tasks())

    @app.delete("/api/admin/tasks/<tid>")
    def admin_task_delete(tid):
        err = guard()
        if err:
            return err
        _tasks().document(tid).delete()
        return jsonify(tasks=list_tasks())

    @app.post("/api/admin/tasks/starters")
    def admin_task_starters():
        """Add the in-development add-ons as ideas, once (skips titles already on the list)."""
        err = guard()
        if err:
            return err
        have = {t.get("title") for t in list_tasks()}
        now = time.time()
        for i, (title, area, notes) in enumerate(STARTERS):
            if title not in have:
                _tasks().document().set({"title": title, "notes": notes, "kind": "idea", "status": "idea",
                                         "priority": "medium", "area": area, "created": now - i, "updated": now})
        return jsonify(tasks=list_tasks())

"""Demo profile: anyone can try LifeTracker with made-up data, without a Google sign-in.

How it stays separate from real data:
  * The browser sends `Authorization: Demo <session id>` (random, made in the browser) instead of a Firebase token.
  * Every demo request runs as uid "demo" against an in-memory store (demo_store.py). `Router` sits where the
    Firestore client was, so module code is unchanged and a demo request can never reach real Firestore.
  * Each visitor gets a private copy of the seeded demo (demo_seed.py), so edits are theirs only, and are lost
    when the session ends or the server restarts. At most MAX_SESSIONS copies are kept (least recently used go).
  * Features that cost money or touch real accounts (Plaid, WHOOP/Strava/Gmail connections, the AI assistant
    and coach, syllabus reading with Claude) answer 403 in the demo.
The one real thing a demo can read: the owner's course schedule without grades, copied on purpose with
POST /api/demo/schedule (owner only) to Firestore demo/education. DELETE removes it.

Turn the demo off with env DEMO_MODE=0.
"""
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from datetime import date, datetime, timezone

from flask import g, has_request_context, jsonify, request

import demo_store

ENABLED = os.environ.get("DEMO_MODE", "1") != "0"
MAX_SESSIONS = 40
SESSION_TTL = 6 * 3600
TEMPLATE_TTL = 3600          # rebuild hourly (and on a new day) so a newly shared schedule shows up
MAX_BODY = 4 * 1024 * 1024
SID_RE = re.compile(r"[a-f0-9]{16,64}")
_SEED_SID = "seed-" + secrets.token_hex(16)    # internal only; never matches SID_RE

# (method or "*", path regex) the demo answers with 403
BLOCKED = [
    ("*", r"/api/plaid/.*"), ("*", r"/api/sync"), ("*", r"/api/chat"), ("*", r"/api/goals/coach"),
    ("*", r"/api/edu/gmail.*"), ("POST", r"/api/edu/plan"), ("POST", r"/api/edu/syllabi/upload"),
    ("PATCH", r"/api/edu/syllabi/[^/]+"),
    ("*", r"/api/whoop(/.*)?"), ("*", r"/api/strava/(connect|sync)"), ("DELETE", r"/api/strava"),
    ("*", r"/api/demo/.*"),
]
_BLOCKED = [(m, re.compile(p + r"/?")) for m, p in BLOCKED]
NOT_IN_DEMO = ("This is turned off in the demo: it connects real accounts or uses the AI assistant. "
               "Sign in with Google to use it.")

_lock = threading.RLock()
_sessions = OrderedDict()         # sid -> [client, last_used]
_template = {"client": None, "day": None, "built": 0.0}
_seeding = {"client": None}
real_db = None
firestore = None
_app = None


class Blocked(Exception):
    pass


def init(app, _real_db, _firestore, require_owner, user_ref):
    """Register the owner-only schedule endpoints. Call after the feature modules."""
    global real_db, firestore, _app
    real_db, firestore, _app = _real_db, _firestore, app
    demo_store.SENTINEL = _firestore.SERVER_TIMESTAMP

    @app.get("/api/demo/schedule")
    @require_owner
    def demo_schedule_status():
        snap = real_db.collection("demo").document("education").get()
        d = snap.to_dict() if snap.exists else None
        return jsonify(shared=bool(d), courses=len((d or {}).get("courses") or []), updated=(d or {}).get("updated"),
                       enabled=ENABLED)

    @app.post("/api/demo/schedule")
    @require_owner
    def demo_schedule_share():
        """Copy the owner's courses (no grades, notes or P/F choices) and syllabi (no files or text) for the demo."""
        import education
        keep = {"term", "title", "code", "credits", "graded", "status", "tags", "experiential_credits", "professor",
                "meetings_text", "exam_type", "category", "exam_date", "exam_time", "pf_eligible", "pf_mandatory"}
        courses = []
        for d in user_ref().collection("edu_courses").stream():
            courses.append({k: v for k, v in (d.to_dict() or {}).items() if k in keep})
        prof = education._profile_ref().get().to_dict() or {}
        drop = {"text", "course_id", "updated_at", "created_at", "file_name", "file_type", "has_file", "size"}
        syllabi = [{k: v for k, v in (d.to_dict() or {}).items() if k not in drop}
                   for d in user_ref().collection("edu_syllabi").stream()]
        syllabi = [s for s in syllabi if s.get("course_title")]
        real_db.collection("demo").document("education").set({
            "courses": courses, "syllabi": syllabi,
            "profile": {k: prof.get(k) for k in ("school", "program", "terms", "grad_term") if prof.get(k)},
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        _reset_template()
        return jsonify(shared=True, courses=len(courses), syllabi=len(syllabi))

    @app.delete("/api/demo/schedule")
    @require_owner
    def demo_schedule_remove():
        real_db.collection("demo").document("education").delete()
        _reset_template()
        return jsonify(shared=False)


def _reset_template():
    with _lock:
        _template.update(client=None, day=None, built=0.0)
        _sessions.clear()


# ---------------------------------------------------------------- requests
def is_demo():
    return has_request_context() and g.get("demo_db") is not None


def check_auth(header):
    """For an `Authorization: Demo <sid>` header: set up the demo request, or return an error response."""
    sid = header.removeprefix("Demo ").strip()
    if sid == _SEED_SID and _seeding["client"] is not None:
        g.uid, g.demo_db, g.demo_sid = "demo", _seeding["client"], sid
        return None
    if not ENABLED:
        return jsonify(error="The demo is turned off."), 403
    if not SID_RE.fullmatch(sid):
        return jsonify(error="bad demo session"), 401
    path, method = request.path, request.method
    if any((m == "*" or m == method) and rx.fullmatch(path) for m, rx in _BLOCKED):
        return jsonify(error=NOT_IN_DEMO, demo=True), 403
    if (request.content_length or 0) > MAX_BODY:
        return jsonify(error="That file is too big for the demo."), 413
    g.uid, g.demo_db, g.demo_sid = "demo", session_client(sid), sid
    return None


def guard():
    """Call before any paid or external service; raises in the demo."""
    if is_demo():
        raise Blocked(NOT_IN_DEMO)


def session_client(sid):
    with _lock:
        now = time.time()
        for k in [k for k, (_, used) in _sessions.items() if now - used > SESSION_TTL]:
            _sessions.pop(k, None)
        if sid in _sessions:
            _sessions[sid][1] = now
            _sessions.move_to_end(sid)
            return _sessions[sid][0]
        client = template().copy()
        _sessions[sid] = [client, now]
        while len(_sessions) > MAX_SESSIONS:
            _sessions.popitem(last=False)
        return client


def template():
    with _lock:
        today = date.today()
        if _template["client"] is None or _template["day"] != today or time.time() - _template["built"] > TEMPLATE_TTL:
            _template.update(client=_build(today), day=today, built=time.time())
        return _template["client"]


def _shared_schedule():
    try:
        snap = real_db.collection("demo").document("education").get()
        return snap.to_dict() if snap.exists else None
    except Exception:
        return None


def _build(today):
    """Seed a fresh store in a separate thread so the seeding requests get their own Flask contexts."""
    import demo_seed
    app = _app
    out, err = {}, {}
    schedule = _shared_schedule()

    def run():
        store = demo_store.Client()
        _seeding["client"] = store
        try:
            out["store"] = demo_seed.build(app, store, schedule=schedule, today=today)
        except Exception as e:   # surfaced below
            err["e"] = e
        finally:
            _seeding["client"] = None

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join()
    if err:
        raise err["e"]
    return out["store"]


# ---------------------------------------------------------------- seeding helpers (demo_seed.py)
class _SeedClient:
    """The Flask test client as the seed session; any failed call stops the build."""
    def __init__(self, app):
        self.c, self.h = app.test_client(), {"Authorization": f"Demo {_SEED_SID}"}

    def _call(self, method, path, **kw):
        r = getattr(self.c, method)(path, headers=self.h, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"demo seed {method.upper()} {path}: {r.status_code} {r.get_data(as_text=True)[:300]}")
        return r

    def get(self, path, **kw): return self._call("get", path, **kw)
    def post(self, path, **kw): return self._call("post", path, **kw)
    def put(self, path, **kw): return self._call("put", path, **kw)
    def patch(self, path, **kw): return self._call("patch", path, **kw)


def seed_client(app):
    return _SeedClient(app)


@contextmanager
def seed_context(app):
    with app.test_request_context("/"):
        g.uid, g.demo_db, g.demo_sid = "demo", _seeding["client"], _SEED_SID
        yield


# ---------------------------------------------------------------- the Firestore stand-in
class Router:
    """Looks like the Firestore client; sends demo requests to that session's in-memory store."""
    def __init__(self, real):
        self._real = real

    def _target(self):
        if has_request_context():
            d = g.get("demo_db")
            if d is not None:
                return d
        return self._real

    def collection(self, *a, **k):
        return self._target().collection(*a, **k)

    def document(self, *a, **k):
        return self._target().document(*a, **k)

    def batch(self, *a, **k):
        return self._target().batch(*a, **k)

    def __getattr__(self, name):
        return getattr(self._target(), name)

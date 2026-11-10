"""Which parts of LifeTracker each person uses (the sign-up service picker and Settings → Your services).

The first time someone signs in they pick their services on an intro page; the app then shows only those sections,
and they can change the list later in Settings. Stored per user in users/{uid}/settings/services:
  {picked: ["health", "goals", ...], onboarded: true, updated}

Core services are the built sections. Add-ons are in development: picking one adds its section with the plan for
it, and nothing else yet. Finances can only be picked by the owner (it's owner-only on the server too, see access.py).

No saved choice yet: the owner (and the demo) see every core service and skip the intro; everyone else gets the intro.
This is a display preference only: it never grants access to anything.
"""
import time
from flask import g, jsonify, request

CATALOG = [
    # id, title, blurb, kind
    ("finances",  "Finances",  "Net worth, spending, investments and taxes from your bank accounts", "core"),
    ("health",    "Health",    "Workouts, running, sleep and recovery, and a food diary", "core"),
    ("education", "Education", "Courses, deadlines, finals, grades and a degree audit", "core"),
    ("goals",     "Goals",     "SMART goals with step plans, tied to the rest of the app", "core"),
    ("sports",    "Sports",    "Your teams' schedules and standings, and ESPN fantasy football", "core"),
    ("travel",    "Travel",    "Apartment search, commute by bikeshare and Metro, and trips", "core"),
    ("social",    "Social",    "Your Partiful events in one list", "core"),
    ("golf",      "Golf",      "Rounds and a handicap index, from 18Birdies and GHIN (CDGA)", "addon"),
    ("movies",    "Movies",    "Films you've watched and rated on Letterboxd", "addon"),
    ("recipes",   "Recipes",   "Saved recipes that log straight to your food diary", "addon"),
    ("chess",     "Chess",     "Your Chess.com ratings and games", "addon"),
]
IDS = [c[0] for c in CATALOG]
CORE = [c[0] for c in CATALOG if c[3] == "core"]
OWNER_ONLY = {"finances"}


def can_finance():
    """Owner (and the made-up demo and local dev) may pick Finances; members and the developer account can't."""
    return getattr(g, "role", None) in (None, "owner") or g.get("demo_db") is not None


def catalog():
    fin = can_finance()
    return [{"id": i, "title": t, "blurb": b, "kind": k, "status": "live" if k == "core" else "in_development"}
            for i, t, b, k in CATALOG if fin or i not in OWNER_ONLY]


def clean(picked, fin=True):
    """Known ids only, in catalog order, without Finances for accounts that can't use it."""
    want = {str(p) for p in (picked or []) if isinstance(p, str)}
    return [i for i in IDS if i in want and (fin or i not in OWNER_ONLY)]


def state(doc, fin=True):
    """The answer for GET /api/services from the stored doc (or None)."""
    if doc and doc.get("onboarded"):
        return {"picked": clean(doc.get("picked"), fin), "onboarded": True}
    if fin:                                    # the owner (and the demo) already use everything: no intro
        return {"picked": list(CORE), "onboarded": True}
    return {"picked": [], "onboarded": False}


def init(app, db, firestore, require_owner, user_ref):
    def ref():
        return user_ref().collection("settings").document("services")

    @app.get("/api/services")
    @require_owner
    def services_get():
        snap = ref().get()
        return jsonify(catalog=catalog(), **state(snap.to_dict() if snap.exists else None, can_finance()))

    @app.put("/api/services")
    @require_owner
    def services_put():
        b = request.get_json(force=True, silent=True) or {}
        if not isinstance(b.get("picked"), list):
            return jsonify(error="picked must be a list"), 400
        picked = clean(b["picked"], can_finance())
        if not picked:
            return jsonify(error="Pick at least one service."), 400
        ref().set({"picked": picked, "onboarded": True, "updated": time.time()})
        return jsonify(catalog=catalog(), picked=picked, onboarded=True)

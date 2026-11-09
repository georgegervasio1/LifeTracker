"""Trips: vacations and travel. A foundation to build on: trips with dates and places, bookings you add, and search
links that open Google Flights, airline sites and Airbnb already filled in.

  GET    /api/trips                 trips (upcoming first) + settings
  POST   /api/trips                 create {name, start, end, places[], travelers?, notes?, bookings?}
  PUT    /api/trips/<id>            replace editable fields
  DELETE /api/trips/<id>
  PUT    /api/trips/settings        {home_airport, airlines[]}

A trip (users/{uid}/trips/{id}): name, start, end, places [str], travelers, notes,
bookings [{id, kind: flight|stay|car|other, title, date, end_date, airline, flight_no, from, to, confirmation, link, cost}].
Airline and Google Flights have no public booking APIs for personal apps, so links open their sites with the trip filled in
where the site accepts it. Nothing here books or pays for anything.
"""
import os
import re
from datetime import date
from urllib.parse import quote, urlencode

from flask import Blueprint, jsonify, request

bp = Blueprint("trips", __name__)
db = firestore = require_owner = user_ref = None
KINDS = ("flight", "stay", "car", "other")
AIRLINES = {  # name: (manage trips page, home page)
    "United": ("https://www.united.com/en/us/manageres/mytrips", "https://www.united.com/"),
    "American": ("https://www.aa.com/reservation/view/find-your-trip", "https://www.aa.com/"),
    "Delta": ("https://www.delta.com/my-trips/search", "https://www.delta.com/"),
    "Southwest": ("https://www.southwest.com/air/manage-reservation/", "https://www.southwest.com/"),
    "JetBlue": ("https://www.jetblue.com/manage-trips", "https://www.jetblue.com/"),
    "Alaska": ("https://www.alaskaair.com/booking/reservation-lookup", "https://www.alaskaair.com/"),
}
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def init(app, _db, _firestore, _require_owner, _user_ref):
    global db, firestore, require_owner, user_ref
    db, firestore, require_owner, user_ref = _db, _firestore, _require_owner, _user_ref
    _register_routes()
    app.register_blueprint(bp)


def _col():
    return user_ref().collection("trips")


def _settings_ref():
    return user_ref().collection("travel").document("trip_settings")


def _s(v, n=200):
    return str(v or "").strip()[:n]


def clean_trip(b, partial=False):
    out = {}
    if "name" in b or not partial:
        out["name"] = _s(b.get("name"), 120)
        if not out["name"]:
            return None, "Give the trip a name"
    for k in ("start", "end"):
        if k in b or not partial:
            v = _s(b.get(k), 10)
            if v and not _ISO.match(v):
                return None, f"{k} must be YYYY-MM-DD"
            out[k] = v
    if out.get("start") and out.get("end") and out["end"] < out["start"]:
        return None, "The trip ends before it starts"
    if "places" in b or not partial:
        pl = b.get("places") or []
        if isinstance(pl, str):
            pl = [p for p in re.split(r"[;\n]", pl)]
        out["places"] = [_s(p, 80) for p in pl if _s(p)][:12]
    if "travelers" in b:
        try:
            out["travelers"] = max(1, min(16, int(b.get("travelers") or 1)))
        except (TypeError, ValueError):
            return None, "travelers must be a number"
    if "notes" in b:
        out["notes"] = _s(b.get("notes"), 4000)
    if "bookings" in b:
        bk = []
        for i, x in enumerate((b.get("bookings") or [])[:40]):
            if not isinstance(x, dict) or not _s(x.get("title") or x.get("flight_no") or x.get("kind")):
                continue
            row = {"id": _s(x.get("id"), 20) or f"b{i}{os.urandom(3).hex()}",
                   "kind": x.get("kind") if x.get("kind") in KINDS else "other"}
            for k, n in (("title", 120), ("airline", 40), ("flight_no", 12), ("from", 40), ("to", 40),
                         ("confirmation", 20), ("link", 500), ("date", 10), ("end_date", 10), ("time", 8)):
                if _s(x.get(k)):
                    row[k] = _s(x.get(k), n)
            for k in ("date", "end_date"):
                if row.get(k) and not _ISO.match(row[k]):
                    row.pop(k)
            if row.get("link") and not re.match(r"^https://", row["link"]):
                row.pop("link")
            try:
                if x.get("cost") not in (None, ""):
                    row["cost"] = round(float(str(x["cost"]).replace(",", "").replace("$", "")), 2)
            except ValueError:
                pass
            bk.append(row)
        out["bookings"] = sorted(bk, key=lambda r: (r.get("date") or "9999", r.get("time") or ""))
    return out, None


def search_links(t, settings):
    """Pre-filled searches. Google Flights and Airbnb accept the trip's place and dates in the URL."""
    place = (t.get("places") or [""])[0]
    home = settings.get("home_airport") or ""
    links = []
    if place:
        q = f"Flights to {place}" + (f" from {home}" if home else "")
        if t.get("start"):
            q += f" on {t['start']}"
        if t.get("end"):
            q += f" through {t['end']}"
        links.append({"label": "Google Flights", "url": "https://www.google.com/travel/flights?" + urlencode({"q": q})})
        params = {"adults": t.get("travelers") or 1}
        if t.get("start"):
            params["checkin"] = t["start"]
        if t.get("end"):
            params["checkout"] = t["end"]
        links.append({"label": "Airbnb", "url": f"https://www.airbnb.com/s/{quote(place)}/homes?" + urlencode(params)})
        hq = {"q": place} | ({"dates": f"{t['start']}_{t['end']}"} if t.get("start") and t.get("end") else {})
        links.append({"label": "Google Hotels", "url": "https://www.google.com/travel/search?" + urlencode(hq)})
    for name in settings.get("airlines") or []:
        if name in AIRLINES:
            links.append({"label": name, "url": AIRLINES[name][1], "kind": "airline"})
    return links


def trips_view():
    today = date.today().isoformat()
    s = _settings_ref().get()
    settings = (s.to_dict() if s.exists else None) or {}
    rows = [d.to_dict() | {"id": d.id} for d in _col().stream()]
    for t in rows:
        t["when"] = "past" if t.get("end") and t["end"] < today else "now" if t.get("start") and t["start"] <= today else \
            "upcoming" if t.get("start") else "someday"
        t["days_until"] = (date.fromisoformat(t["start"]) - date.today()).days if t["when"] == "upcoming" else None
        t["nights"] = (date.fromisoformat(t["end"]) - date.fromisoformat(t["start"])).days if t.get("start") and t.get("end") else None
        t["links"] = search_links(t, settings)
        for b in t.get("bookings") or []:
            if b["kind"] == "flight" and b.get("airline") in AIRLINES:
                b["manage"] = AIRLINES[b["airline"]][0]
        t["cost"] = round(sum(b.get("cost") or 0 for b in t.get("bookings") or []), 2) or None
    order = {"now": 0, "upcoming": 1, "someday": 2}
    live = sorted((t for t in rows if t["when"] != "past"), key=lambda t: (order[t["when"]], t.get("start") or "", t.get("name") or ""))
    past = sorted((t for t in rows if t["when"] == "past"), key=lambda t: t.get("start") or "", reverse=True)
    rows = live + past
    return {"trips": rows, "settings": settings, "airlines": list(AIRLINES)}


def _register_routes():
    @bp.get("/api/trips")
    @require_owner
    def trips():
        return jsonify(trips_view())

    @bp.post("/api/trips")
    @require_owner
    def trip_create():
        t, err = clean_trip(request.get_json(silent=True) or {})
        if err:
            return jsonify(error=err), 400
        ref = _col().document()
        ref.set(t)
        return jsonify(trips_view() | {"id": ref.id})

    @bp.put("/api/trips/<tid>")
    @require_owner
    def trip_update(tid):
        ref = _col().document(tid)
        snap = ref.get()
        if not snap.exists:
            return jsonify(error="not found"), 404
        t, err = clean_trip(request.get_json(silent=True) or {}, partial=True)
        if err:
            return jsonify(error=err), 400
        merged = snap.to_dict() | t
        if merged.get("start") and merged.get("end") and merged["end"] < merged["start"]:
            return jsonify(error="The trip ends before it starts"), 400
        ref.set(merged)
        return jsonify(trips_view())

    @bp.delete("/api/trips/<tid>")
    @require_owner
    def trip_delete(tid):
        _col().document(tid).delete()
        return jsonify(trips_view())

    @bp.put("/api/trips/settings")
    @require_owner
    def trip_settings():
        b = request.get_json(silent=True) or {}
        home = _s(b.get("home_airport"), 40).upper() if re.fullmatch(r"[A-Za-z]{3}", _s(b.get("home_airport"))) else _s(b.get("home_airport"), 40)
        al = [a for a in (b.get("airlines") or []) if a in AIRLINES]
        _settings_ref().set({"home_airport": home, "airlines": al}, merge=True)
        return jsonify(trips_view())

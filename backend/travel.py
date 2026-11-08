"""Travel: Capital Bikeshare at your saved stations, and an apartment search with a wishlist.

Bikeshare (live data from the system's public GBFS feed, no key):
  GET  /api/travel/bike                    saved stations with live bikes / e-bikes / docks, plus bikeshare spend
  GET  /api/travel/bike/stations?q=&lat=&lon=   find stations by name, or the closest ones to a point
  PUT  /api/travel/bike/saved              {stations: [{id, label}]}  the stations you check (up to 12)

Bikeshare rides (from the "Ride Receipt" emails the system sends after each ride; it has no API for other apps):
  GET    /api/travel/bike/rides            rides, stats, membership payoff
  POST   /api/travel/bike/rides/scan       {access_token}  read receipts from Gmail (read-only; the token isn't kept)
  POST   /api/travel/bike/rides/import     {rides: [...]}  rides from a file (and the demo seed)
  PUT    /api/travel/bike/membership       {price, start, end}  what you paid and when; blank = not set
  DELETE /api/travel/bike/rides            forget every imported ride
Miles are an estimate: the straight line from the start station to the end station (receipts carry no route).

Apartments (listings arrive from the weekly digest, or a file you import):
  GET    /api/apartments                   listings, criteria, digests
  POST   /api/apartments/import            {digest_date?, listings: [...]}  owner sign-in, or X-Ingest-Key
  PATCH  /api/apartments/<id>              {status?, notes?, rank?}
  DELETE /api/apartments/<id>
  PUT    /api/apartments/criteria          {city, max_rent, stretch_rent, min_beds, move_in, big_sqft}
  PUT    /api/apartments/order             {ids: [...]}  wishlist order

Firestore (users/{uid}/...): travel/settings {bike_stations, apt_criteria, membership, bike_gmail}, bike_rides/{id},
apartments/{id}, apartment_digests/{date}.
A listing keeps what you added (status, notes, rank) when the digest sends it again. Nothing is guessed:
a field the listing didn't give stays blank.

The digest can post without a browser sign-in by sending header X-Ingest-Key equal to env APARTMENTS_INGEST_KEY
(Secret Manager: apartments-ingest-key). Those writes go to the ALLOWED_EMAIL owner's data. No key set = off.
"""
import base64
import hashlib
import hmac
import html
import math
import os
import re
import threading
import time
from collections import Counter
from datetime import date, datetime

import requests
from flask import Blueprint, g, jsonify, request

bp = Blueprint("travel", __name__)
db = firestore = require_owner = user_ref = None

GBFS_BASE = os.environ.get("BIKESHARE_GBFS", "https://gbfs.lyft.com/gbfs/2.3/dca-cabi/en")
INFO_TTL = 6 * 3600
STATUS_TTL = 60
MAX_SAVED = 12
BIKE_MERCHANT = re.compile(r"CAPITAL BIKESHARE|CAPITALBIKESHARE|\bCABI\b|BIKESHARE", re.I)

STATUSES = ("new", "saved", "touring", "applied", "passed")
WISHLIST = ("saved", "touring", "applied")
LISTING_NUM = ("rent", "sqft", "beds", "baths", "lat", "lon", "stop_mi", "score")
LISTING_TXT = ("title", "address", "neighborhood", "available", "nearest_stop", "source", "summary", "unit")
CRITERIA_NUM = ("max_rent", "stretch_rent", "min_beds", "big_sqft")
MAX_IMPORT = 300


def init(app, _db, _firestore, _require_owner, _user_ref):
    global db, firestore, require_owner, user_ref
    db, firestore, require_owner, user_ref = _db, _firestore, _require_owner, _user_ref
    _register_routes()
    app.register_blueprint(bp)


def _settings_ref():
    return user_ref().collection("travel").document("settings")


def _settings():
    s = _settings_ref().get()
    return (s.to_dict() if s.exists else None) or {}


# ---------------------------------------------------------------- GBFS feed (cached)
_cache = {"info": (0.0, None), "status": (0.0, None)}
_lock = threading.Lock()


def fetch_json(url):
    """One GET of a public feed file. Tests replace this."""
    r = requests.get(url, timeout=10, headers={"User-Agent": "LifeTracker"})
    r.raise_for_status()
    return r.json()


def _feed(name, ttl):
    with _lock:
        at, data = _cache[name]
        if data is not None and time.time() - at < ttl:
            return data, at
    file = "station_information.json" if name == "info" else "station_status.json"
    js = fetch_json(f"{GBFS_BASE}/{file}")
    rows = {str(s["station_id"]): s for s in (js.get("data") or {}).get("stations", [])}
    with _lock:
        _cache[name] = (time.time(), rows)
    return rows, time.time()


def _miles(a_lat, a_lon, b_lat, b_lon):
    r = 3958.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def station_view(info, status):
    """A station with live counts. Classic bikes = all bikes minus e-bikes, when the feed reports e-bikes."""
    out = {"id": str(info.get("station_id")), "name": info.get("name") or "", "lat": info.get("lat"),
           "lon": info.get("lon"), "capacity": info.get("capacity"), "live": False}
    if status:
        bikes, eb = status.get("num_bikes_available"), status.get("num_ebikes_available")
        out.update(live=True, docks=status.get("num_docks_available"),
                   ebikes=eb, bikes=(bikes - eb) if bikes is not None and eb is not None else bikes,
                   renting=bool(status.get("is_renting", 1)), returning=bool(status.get("is_returning", 1)),
                   reported=status.get("last_reported"))
    return out


def bike_spend(today=None):
    """Bikeshare charges in your bank and card data over the last 12 months (memberships and ride fees)."""
    import ledger
    today = today or date.today()
    y, m = (today.year, today.month - 11) if today.month > 11 else (today.year - 1, today.month + 1)
    start = date(y, m, 1).isoformat()
    txs = [t for t in ledger.load_transactions(start, today.isoformat())
           if BIKE_MERCHANT.search(f"{t.get('merchant') or ''} {t.get('name') or ''}") and (t.get("amount") or 0) != 0]
    by_month = {}
    for t in txs:
        by_month[t["date"][:7]] = round(by_month.get(t["date"][:7], 0) + t["amount"], 2)
    year = [t for t in txs if t["date"][:4] == str(today.year)]
    return {"since": start, "charges": len(txs), "total": round(sum(t["amount"] for t in txs), 2),
            "year_total": round(sum(t["amount"] for t in year), 2), "by_month": dict(sorted(by_month.items())),
            "recent": [{"date": t["date"], "amount": t["amount"], "merchant": t.get("merchant") or t.get("name") or ""}
                       for t in sorted(txs, key=lambda t: t["date"], reverse=True)[:8]]}


# ---------------------------------------------------------------- ride receipts
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
RECEIPT_QUERY = 'from:updates.capitalbikeshare.com subject:"Ride Receipt"'
MAX_SCAN = 400
_MONEY = r"\$\s?(\d[\d,]*\.\d{2})"


def receipt_tokens(text):
    """Receipt text (HTML or plain) as a list of non-empty cells."""
    t = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", text or "")
    t = re.sub(r"(?i)<br\s*/?>|</(td|th|tr|p|div|table|h\d|li)>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    cells = []
    for line in re.split(r"[\n|]", t):
        line = re.sub(r"\s+", " ", line).strip()
        if line and not re.fullmatch(r"[\s\u034f\u200c\u00a0-]*", line):
            cells.append(line)
    return cells


def _clock(day, hm):
    m = re.match(r"(\d{1,2}):(\d{2})\s*([ap])\.?m", hm.strip(), re.I)
    if not m:
        return None
    h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    return datetime(day.year, day.month, day.day, h, int(m.group(2)))


def parse_receipt(text):
    """One ride receipt -> dict, or None if this isn't a ride receipt. Fields the receipt doesn't show stay out."""
    cells = receipt_tokens(text)
    flat = " | ".join(cells)
    m = re.search(r"([A-Z][a-z]+|[A-Z]+) (\d{1,2}), (\d{4}) AT (\d{1,2}:\d{2} ?[AP]M)", flat, re.I)
    if not m:
        return None
    try:
        day = datetime.strptime(f"{m.group(1).title()} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
    except ValueError:
        return None
    ride = {"date": day.isoformat()}
    t = re.search(r"ride total:?\s*" + _MONEY, flat, re.I)
    if t:
        ride["total"] = float(t.group(1).replace(",", ""))
    sv = re.search(r"Saved this trip\s*\|?\s*" + _MONEY, flat, re.I)
    if sv:
        ride["saved"] = float(sv.group(1).replace(",", ""))
    rc = re.search(r"Receipt\s*#\s*(\d{6,})", flat)
    if rc:
        ride["receipt"] = rc.group(1)
    for i, c in enumerate(cells):
        st = re.fullmatch(r"(Start|End) (\d{1,2}:\d{2} ?[ap]\.?m)", c, re.I)
        if st and i:
            k = st.group(1).lower()
            ride[f"{k}_station"] = cells[i - 1][:120]
            ride[f"{k}_time"] = st.group(2).lower().replace(" ", "")
    a, b = _clock(day, ride.get("start_time", "")), _clock(day, ride.get("end_time", ""))
    if a and b:
        mins = (b - a).total_seconds() / 60
        ride["minutes"] = round(mins + (1440 if mins < 0 else 0))
    items = []
    for i, c in enumerate(cells[:-1]):
        nxt = re.fullmatch(_MONEY, cells[i + 1])
        if nxt and re.search(r"[a-z]", c) and not re.search(r"total|saved|visa|mastercard|amex|discover|billing|\*\d{4}", c, re.I):
            items.append({"label": c[:80], "amount": float(nxt.group(1).replace(",", ""))})
    if items:
        ride["items"] = items[:10]
    if re.search(r"e-?bike", flat, re.I):
        ride["bike"] = "ebike"
    elif re.search(r"classic bike", flat, re.I):
        ride["bike"] = "classic"
    return ride if ("start_station" in ride or "total" in ride) else None


def _gmail_get(token, path, params=None):
    r = requests.get(f"{GMAIL}/{path}", headers={"Authorization": f"Bearer {token}"}, params=params or {}, timeout=20)
    if r.status_code == 401:
        raise PermissionError("Gmail access expired")
    if r.status_code == 403:
        raise PermissionError("Gmail access denied")
    r.raise_for_status()
    return r.json()


def _payload_text(p):
    out = []
    def walk(x):
        data = (x.get("body") or {}).get("data")
        if data and x.get("mimeType") in ("text/html", "text/plain"):
            out.append((x["mimeType"], base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")))
        for c in x.get("parts") or []:
            walk(c)
    walk(p)
    htm = [t for mt, t in out if mt == "text/html"]
    return "\n".join(htm or [t for _, t in out])


def fetch_receipts(token, skip=()):
    ids, page = [], None
    while len(ids) < MAX_SCAN:
        js = _gmail_get(token, "messages", {"q": RECEIPT_QUERY, "maxResults": 100, **({"pageToken": page} if page else {})})
        ids += [m["id"] for m in js.get("messages") or []]
        page = js.get("nextPageToken")
        if not page:
            break
    out = []
    for mid in ids[:MAX_SCAN]:
        if mid in skip:
            continue
        msg = _gmail_get(token, f"messages/{mid}", {"format": "full"})
        out.append((mid, _payload_text(msg.get("payload") or {})))
    return out


def _norm_station(n):
    return re.sub(r"[^a-z0-9]+", " ", (n or "").lower().replace("&", " and ")).strip()


def locate_rides(rides):
    """Station coordinates from the live feed, matched by name; addresses (parked outside a dock) stay unplaced."""
    if all(r.get(f"{k}_lat") is not None for r in rides for k in ("start", "end")):
        return rides
    try:
        info, _ = _feed("info", INFO_TTL)
    except Exception:
        return rides
    by_name = {_norm_station(s.get("name")): s for s in info.values()}
    for r in rides:
        for k in ("start", "end"):
            if r.get(f"{k}_lat") is None and (s := by_name.get(_norm_station(r.get(f"{k}_station")))):
                r[f"{k}_lat"], r[f"{k}_lon"] = s.get("lat"), s.get("lon")
    return rides


def ride_id(r):
    if r.get("receipt"):
        return f"r{r['receipt']}"
    key = "|".join(str(r.get(k) or "") for k in ("date", "start_time", "start_station", "end_station", "total"))
    return "h" + hashlib.sha1(key.encode()).hexdigest()[:16]


def save_rides(rides):
    col = user_ref().collection("bike_rides")
    added = 0
    for r in locate_rides(rides):
        if all(r.get(k) is not None for k in ("start_lat", "start_lon", "end_lat", "end_lon")):
            r["miles"] = round(_miles(r["start_lat"], r["start_lon"], r["end_lat"], r["end_lon"]), 2)
        ref = col.document(ride_id(r))
        if not ref.get().exists:
            added += 1
        ref.set(r)
    return added


def clean_ride(x):
    if not isinstance(x, dict) or not re.match(r"^\d{4}-\d{2}-\d{2}$", str(x.get("date") or "")):
        return None
    r = {"date": x["date"]}
    for k in ("total", "saved", "minutes", "start_lat", "start_lon", "end_lat", "end_lon"):
        v = _num(x.get(k))
        if v is not None:
            r[k] = round(v, 6) if k.endswith(("lat", "lon")) else round(v, 2)
    for k in ("start_station", "end_station", "start_time", "end_time", "receipt", "bike"):
        if x.get(k):
            r[k] = str(x[k]).strip()[:120]
    return r


def membership_view(m, rides):
    """Savings from receipts during the membership vs what it cost. Nothing is assumed: no price, no verdict."""
    m = m or {}
    price, start, end = m.get("price"), m.get("start") or "", m.get("end") or ""
    inside = [r for r in rides if (not start or r["date"] >= start) and (not end or r["date"] <= end)]
    saved = round(sum(r.get("saved") or 0 for r in inside), 2)
    out = {"price": price, "start": start or None, "end": end or None, "rides": len(inside), "saved": saved,
           "with_savings": sum(1 for r in inside if r.get("saved") is not None)}
    if price is not None:
        out["net"] = round(saved - price, 2)
        out["paid_off"] = saved >= price
        per = [r.get("saved") for r in inside if r.get("saved")]
        if not out["paid_off"] and per:
            avg = sum(per) / len(per)
            out["rides_to_go"] = math.ceil((price - saved) / avg) if avg > 0 else None
    return out


def rides_view():
    rides = sorted((d.to_dict() | {"id": d.id} for d in user_ref().collection("bike_rides").stream()),
                   key=lambda r: (r["date"], r.get("start_time") or ""), reverse=True)
    st = _settings()
    months = {}
    for r in rides:
        mo = months.setdefault(r["date"][:7], {"rides": 0, "minutes": 0, "miles": 0.0, "spent": 0.0, "saved": 0.0})
        mo["rides"] += 1
        mo["minutes"] += r.get("minutes") or 0
        mo["miles"] = round(mo["miles"] + (r.get("miles") or 0), 2)
        mo["spent"] = round(mo["spent"] + (r.get("total") or 0), 2)
        mo["saved"] = round(mo["saved"] + (r.get("saved") or 0), 2)
    stations = Counter()
    for r in rides:
        for k in ("start_station", "end_station"):
            if r.get(k):
                stations[r[k]] += 1
    trips = Counter((r["start_station"], r["end_station"]) for r in rides if r.get("start_station") and r.get("end_station"))
    placed = [r for r in rides if r.get("miles") is not None]
    totals = {"rides": len(rides), "minutes": sum(r.get("minutes") or 0 for r in rides),
              "spent": round(sum(r.get("total") or 0 for r in rides), 2), "saved": round(sum(r.get("saved") or 0 for r in rides), 2),
              "miles": round(sum(r["miles"] for r in placed), 1), "miles_rides": len(placed),
              "longest": max((r["miles"] for r in placed), default=None),
              "first": rides[-1]["date"] if rides else None, "last": rides[0]["date"] if rides else None}
    return {"rides": rides[:300], "totals": totals, "by_month": dict(sorted(months.items())),
            "top_stations": [{"name": n, "visits": c} for n, c in stations.most_common(6)],
            "top_trips": [{"from": a, "to": b, "rides": c} for (a, b), c in trips.most_common(5)],
            "membership": membership_view(st.get("membership"), rides), "gmail": st.get("bike_gmail") or {}}


# ---------------------------------------------------------------- apartments
def listing_id(url):
    u = re.sub(r"[?#].*$", "", (url or "").strip().lower()).rstrip("/")
    u = re.sub(r"^https?://(www\.)?", "", u)
    return hashlib.sha1(u.encode()).hexdigest()[:16]


def _num(v):
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v))
    return float(m.group(0).replace(",", "")) if m else None


def clean_listing(x):
    """Validate one listing from the digest. Returns (listing, error)."""
    if not isinstance(x, dict):
        return None, "not an object"
    url = str(x.get("url") or "").strip()
    if not re.match(r"^https?://", url) or len(url) > 1000:
        return None, "needs an http(s) url"
    out = {"url": url}
    for k in LISTING_NUM:
        v = _num(x.get(k))
        if v is not None:
            out[k] = int(v) if k in ("rent", "sqft") else round(v, 6 if k in ("lat", "lon") else 2)
    for k in LISTING_TXT:
        v = str(x.get(k) or "").strip()
        if v:
            out[k] = v[:300]
    am = x.get("amenities") or []
    if isinstance(am, str):
        am = [a.strip() for a in am.split(",")]
    out["amenities"] = [str(a).strip()[:40] for a in am if str(a).strip()][:12]
    if "lat" in out and not -90 <= out["lat"] <= 90 or "lon" in out and not -180 <= out["lon"] <= 180:
        out.pop("lat", None), out.pop("lon", None)
    if not (out.get("title") or out.get("address")):
        out["title"] = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    return out, None


def import_listings(items, digest_date=None):
    digest_date = digest_date or date.today().isoformat()
    col = user_ref().collection("apartments")
    added, updated, bad = 0, 0, []
    for i, x in enumerate(items[:MAX_IMPORT]):
        lst, err = clean_listing(x)
        if err:
            bad.append({"index": i, "error": err})
            continue
        ref = col.document(listing_id(lst["url"]))
        snap = ref.get()
        old = (snap.to_dict() if snap.exists else None) or {}
        seen = sorted(set(old.get("seen_in") or []) | {digest_date})
        keep = {k: old[k] for k in ("status", "notes", "rank", "first_seen") if k in old}
        doc = {**lst, "status": "new", "notes": "", "first_seen": digest_date, **keep,
               "last_seen": max(seen), "seen_in": seen[-26:]}
        ref.set(doc)
        added, updated = (added, updated + 1) if old else (added + 1, updated)
    user_ref().collection("apartment_digests").document(digest_date).set(
        {"date": digest_date, "new": added, "updated": updated, "received": len(items)}, merge=True)
    return {"added": added, "updated": updated, "skipped": bad}


def apartments_view():
    crit = _settings().get("apt_criteria") or {}
    rows = [d.to_dict() | {"id": d.id} for d in user_ref().collection("apartments").stream()]
    digests = sorted((d.to_dict() for d in user_ref().collection("apartment_digests").stream()),
                     key=lambda d: d.get("date") or "", reverse=True)
    latest = digests[0]["date"] if digests else None
    mx, st, big = crit.get("max_rent"), crit.get("stretch_rent"), crit.get("big_sqft")
    for r in rows:
        rent = r.get("rent")
        r["fit"] = None if rent is None or mx is None else "in_budget" if rent <= mx else \
            "stretch" if st is not None and rent <= st else "over"
        r["big"] = bool(big and (r.get("sqft") or 0) >= big)
        r["is_new"] = bool(latest and r.get("first_seen") == latest)
    rows.sort(key=lambda r: (r.get("last_seen") or "", r.get("sqft") or 0), reverse=True)
    wish = sorted((r for r in rows if r.get("status") in WISHLIST), key=lambda r: (r.get("rank") is None, r.get("rank") or 0))
    return {"listings": rows, "wishlist": [r["id"] for r in wish], "criteria": crit, "digests": digests[:12],
            "latest_digest": latest, "statuses": list(STATUSES)}


def _ingest_ok():
    key = os.environ.get("APARTMENTS_INGEST_KEY", "")
    got = request.headers.get("X-Ingest-Key", "")
    return bool(key) and len(key) >= 24 and hmac.compare_digest(key, got)


def owner_uid():
    """The uid of the one allowed account, for writes that arrive with the ingest key."""
    from firebase_admin import auth as fb_auth
    return fb_auth.get_user_by_email(os.environ.get("ALLOWED_EMAIL", "")).uid


# ---------------------------------------------------------------- routes
def _register_routes():
    @bp.get("/api/travel/bike")
    @require_owner
    def bike():
        saved = _settings().get("bike_stations") or []
        out = {"stations": [], "feed": "ok", "updated": None}
        try:
            info, _ = _feed("info", INFO_TTL)
            status, at = _feed("status", STATUS_TTL)
            out["updated"] = int(at)
            for s in saved:
                i = info.get(str(s.get("id")))
                if i:
                    out["stations"].append(station_view(i, status.get(str(s["id"]))) | {"label": s.get("label") or ""})
                else:
                    out["stations"].append({"id": s.get("id"), "label": s.get("label") or "", "name": s.get("name") or "",
                                            "live": False, "gone": True})
        except Exception:
            out["feed"] = "down"
            out["stations"] = [{"id": s.get("id"), "label": s.get("label") or "", "name": s.get("name") or "", "live": False}
                               for s in saved]
        out["spend"] = bike_spend()
        return jsonify(out)

    @bp.get("/api/travel/bike/stations")
    @require_owner
    def stations():
        q = (request.args.get("q") or "").strip().lower()
        lat, lon = _num(request.args.get("lat")), _num(request.args.get("lon"))
        try:
            info, _ = _feed("info", INFO_TTL)
            status, _ = _feed("status", STATUS_TTL)
        except Exception:
            return jsonify(error="The bikeshare feed isn't answering right now. Try again in a minute."), 502
        rows = list(info.values())
        if q:
            words = q.split()
            rows = [s for s in rows if all(w in (s.get("name") or "").lower() for w in words)]
        if lat is not None and lon is not None:
            for s in rows:
                s["_mi"] = _miles(lat, lon, s.get("lat") or 0, s.get("lon") or 0)
            rows.sort(key=lambda s: s["_mi"])
        else:
            rows.sort(key=lambda s: s.get("name") or "")
        out = []
        for s in rows[:20]:
            v = station_view(s, status.get(str(s["station_id"])))
            if "_mi" in s:
                v["mi"] = round(s["_mi"], 2)
            out.append(v)
        return jsonify(stations=out)

    @bp.put("/api/travel/bike/saved")
    @require_owner
    def save_stations():
        items = (request.get_json(silent=True) or {}).get("stations")
        if not isinstance(items, list):
            return jsonify(error="stations must be a list"), 400
        clean, seen = [], set()
        for s in items[:MAX_SAVED]:
            sid = str((s or {}).get("id") or "").strip()[:64]
            if sid and sid not in seen:
                seen.add(sid)
                clean.append({"id": sid, "label": str(s.get("label") or "").strip()[:30],
                              "name": str(s.get("name") or "").strip()[:120]})
        _settings_ref().set({"bike_stations": clean}, merge=True)
        return bike()

    @bp.get("/api/travel/bike/rides")
    @require_owner
    def rides():
        return jsonify(rides_view())

    @bp.post("/api/travel/bike/rides/scan")
    @require_owner
    def rides_scan():
        token = (request.get_json(silent=True) or {}).get("access_token")
        if not token:
            return jsonify(error="access_token required"), 400
        have = {d.to_dict().get("message_id") for d in user_ref().collection("bike_rides").stream()}
        try:
            mails = fetch_receipts(token, skip=have)
        except PermissionError as e:
            return jsonify(error=str(e)), 401
        except Exception:
            return jsonify(error="Couldn't read Gmail right now. Try again in a minute."), 502
        parsed = []
        for mid, text in mails:
            r = parse_receipt(text)
            if r:
                parsed.append(r | {"message_id": mid})
        added = save_rides(parsed)
        _settings_ref().set({"bike_gmail": {"last_scan": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                                            "read": len(mails)}}, merge=True)
        return jsonify(rides_view() | {"read": len(mails), "added": added})

    @bp.post("/api/travel/bike/rides/import")
    @require_owner
    def rides_import():
        items = (request.get_json(silent=True) or {}).get("rides")
        if not isinstance(items, list):
            return jsonify(error="rides must be a list"), 400
        clean = [r for r in (clean_ride(x) for x in items[:2000]) if r]
        added = save_rides(clean)
        return jsonify(rides_view() | {"added": added, "skipped": len(items) - len(clean)})

    @bp.delete("/api/travel/bike/rides")
    @require_owner
    def rides_forget():
        for d in list(user_ref().collection("bike_rides").stream()):
            user_ref().collection("bike_rides").document(d.id).delete()
        _settings_ref().set({"bike_gmail": {"last_scan": None, "read": 0}}, merge=True)
        return jsonify(rides_view())

    @bp.put("/api/travel/bike/membership")
    @require_owner
    def membership():
        b = request.get_json(silent=True) or {}
        price = _num(b.get("price"))
        if price is not None and price < 0:
            return jsonify(error="price can't be negative"), 400
        dates = {k: str(b.get(k) or "")[:10] for k in ("start", "end")}
        if any(v and not re.match(r"^\d{4}-\d{2}-\d{2}$", v) for v in dates.values()):
            return jsonify(error="dates must be YYYY-MM-DD"), 400
        if dates["start"] and dates["end"] and dates["end"] < dates["start"]:
            return jsonify(error="end is before start"), 400
        _settings_ref().set({"membership": {"price": price, **dates}}, merge=True)
        return jsonify(rides_view())

    @bp.get("/api/apartments")
    @require_owner
    def apartments():
        return jsonify(apartments_view())

    @bp.post("/api/apartments/import")
    def apartments_import():
        body = request.get_json(silent=True) or {}
        items = body.get("listings")
        if not isinstance(items, list):
            return jsonify(error="listings must be a list"), 400
        dd = str(body.get("digest_date") or "")[:10] or None
        if dd and not re.match(r"^\d{4}-\d{2}-\d{2}$", dd):
            return jsonify(error="digest_date must be YYYY-MM-DD"), 400
        if request.headers.get("X-Ingest-Key"):
            if not _ingest_ok():
                return jsonify(error="forbidden"), 403
            try:
                g.uid = owner_uid()
            except Exception:
                return jsonify(error="owner account not found"), 500
            return jsonify(import_listings(items, dd))
        return require_owner(lambda: jsonify(import_listings(items, dd) | apartments_view()))()

    @bp.patch("/api/apartments/<aid>")
    @require_owner
    def apartment_patch(aid):
        ref = user_ref().collection("apartments").document(aid)
        if not ref.get().exists:
            return jsonify(error="not found"), 404
        b = request.get_json(silent=True) or {}
        upd = {}
        if "status" in b:
            if b["status"] not in STATUSES:
                return jsonify(error="unknown status"), 400
            upd["status"] = b["status"]
        if "notes" in b:
            upd["notes"] = str(b["notes"] or "")[:2000]
        if "rank" in b:
            upd["rank"] = None if b["rank"] is None else int(_num(b["rank"]) or 0)
        if upd:
            ref.set(upd, merge=True)
        return jsonify(apartments_view())

    @bp.delete("/api/apartments/<aid>")
    @require_owner
    def apartment_delete(aid):
        user_ref().collection("apartments").document(aid).delete()
        return jsonify(apartments_view())

    @bp.put("/api/apartments/order")
    @require_owner
    def apartment_order():
        ids = (request.get_json(silent=True) or {}).get("ids")
        if not isinstance(ids, list):
            return jsonify(error="ids must be a list"), 400
        col = user_ref().collection("apartments")
        for n, aid in enumerate(ids[:200]):
            ref = col.document(str(aid))
            if ref.get().exists:
                ref.set({"rank": n}, merge=True)
        return jsonify(apartments_view())

    @bp.put("/api/apartments/criteria")
    @require_owner
    def apartment_criteria():
        b = request.get_json(silent=True) or {}
        crit = {k: _num(b.get(k)) for k in CRITERIA_NUM}
        crit = {k: (int(v) if v is not None else None) for k, v in crit.items()}
        crit["city"] = str(b.get("city") or "").strip()[:60]
        mv = str(b.get("move_in") or "").strip()[:10]
        crit["move_in"] = mv if re.match(r"^\d{4}-\d{2}-\d{2}$", mv) else ""
        _settings_ref().set({"apt_criteria": crit}, merge=True)
        return jsonify(apartments_view())

"""Travel → Commute: Capital Bikeshare (live stations, rides from receipts) and Metro (SmarTrip trips, transit benefit).

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

Metro (SmarTrip has no API; trips come from the card's usage history CSV, and fares from bank charges):
  GET    /api/travel/metro                 trips, monthly fares vs the transit benefit, bank charges
  POST   /api/travel/metro/import          {csv: "<SmarTrip usage history>"} or {trips: [...]}
  PUT    /api/travel/metro/benefit         {monthly, start, end, label}  e.g. a school or employer SmartBenefits amount
  DELETE /api/travel/metro                 forget imported Metro trips
  GET    /api/travel/commute               both modes by month (rides and money), for the Commute overview

Firestore (users/{uid}/...): travel/settings {bike_stations, membership, bike_gmail, transit_benefit, apt_criteria},
bike_rides/{id}, metro_trips/{id}.
"""
import base64
import csv
import io
import hashlib
import html
import math
import os
import re
import threading
import time
from collections import Counter
from datetime import date, datetime

import requests
from flask import Blueprint, jsonify, request

bp = Blueprint("travel", __name__)
db = firestore = require_owner = user_ref = None

GBFS_BASE = os.environ.get("BIKESHARE_GBFS", "https://gbfs.lyft.com/gbfs/2.3/dca-cabi/en")
INFO_TTL = 6 * 3600
STATUS_TTL = 60
MAX_SAVED = 12
BIKE_MERCHANT = re.compile(r"CAPITAL BIKESHARE|CAPITALBIKESHARE|\bCABI\b|BIKESHARE", re.I)



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


def _num(v):
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v))
    return float(m.group(0).replace(",", "")) if m else None



# ---------------------------------------------------------------- Metro (SmarTrip usage history)
METRO_BANK = re.compile(r"WMATA|SMARTRIP|SMART TRIP|WASHINGTON METRO", re.I)
_COLS = {"time": ("time", "date", "transaction date"), "desc": ("description", "transaction", "type"),
         "operator": ("operator", "agency"), "entry": ("entry", "bus route", "location", "route"), "exit": ("exit",),
         "change": ("change", "amount", "fare", "value"), "balance": ("balance",)}


def _col(headers, key):
    hs = [h.lower().strip() for h in headers]
    for want in _COLS[key]:
        for i, h in enumerate(hs):
            if want in h and not (key == "entry" and "exit" in h) and not (key == "change" and "balance" in h):
                return i
    return None


def _when(v):
    v = (v or "").strip()
    for fmt in ("%m/%d/%y %I:%M %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%y %H:%M", "%m/%d/%Y %H:%M",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def metro_kind(desc, change, entry, exit_):
    d = (desc or "").lower()
    if re.search(r"benefit", d):
        return "benefit"
    if (change or 0) > 0 or re.search(r"load|add value|purchase|reload|autoload|value added", d):
        return "load"
    if re.search(r"bus", d) or (entry and re.match(r"^[A-Z]?\d{1,3}[A-Z]?$", entry.strip()) and not exit_):
        return "bus"
    if re.search(r"exit", d) or exit_:
        return "rail"
    if re.search(r"entry|entr", d):
        return "entry"
    return "other"


def parse_smartrip_csv(text):
    """SmarTrip card usage history -> trip dicts. Column names vary, so they're matched by keyword."""
    rows = list(csv.reader(io.StringIO(text or "")))
    head_i = next((i for i, r in enumerate(rows[:15]) if r and _col(r, "time") is not None and _col(r, "change") is not None), None)
    if head_i is None:
        return [], "Couldn't find the date and amount columns. Export the card's usage history as CSV and try again."
    h = rows[head_i]
    ci = {k: _col(h, k) for k in _COLS}
    out = []
    for r in rows[head_i + 1:]:
        if not r or all(not c.strip() for c in r):
            continue
        get = lambda k: (r[ci[k]].strip() if ci[k] is not None and ci[k] < len(r) else "")
        w = _when(get("time"))
        if not w:
            continue
        change = _num(get("change"))
        if change is not None and "(" in get("change"):
            change = -abs(change)
        t = {"date": w.date().isoformat(), "time": w.strftime("%H:%M"), "desc": get("desc")[:80], "operator": get("operator")[:40],
             "entry": get("entry")[:80], "exit": get("exit")[:80], "change": change, "balance": _num(get("balance"))}
        t["kind"] = metro_kind(t["desc"], change, t["entry"], t["exit"])
        out.append(t)
    return out, None


def _metro_id(t):
    key = "|".join(str(t.get(k) or "") for k in ("date", "time", "desc", "entry", "exit", "change"))
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def clean_metro(x):
    if not isinstance(x, dict) or not re.match(r"^\d{4}-\d{2}-\d{2}$", str(x.get("date") or "")):
        return None
    t = {"date": x["date"], "time": str(x.get("time") or "")[:5]}
    for k in ("desc", "operator", "entry", "exit"):
        t[k] = str(x.get(k) or "")[:80]
    for k in ("change", "balance"):
        v = _num(x.get(k))
        t[k] = None if v is None else round(v, 2)
    t["kind"] = x.get("kind") if x.get("kind") in ("rail", "bus", "entry", "load", "benefit", "other") else \
        metro_kind(t["desc"], t["change"], t["entry"], t["exit"])
    return t


def metro_view(today=None):
    import ledger
    today = today or date.today()
    trips = sorted((d.to_dict() | {"id": d.id} for d in user_ref().collection("metro_trips").stream()),
                   key=lambda t: (t["date"], t.get("time") or ""), reverse=True)
    ben = _settings().get("transit_benefit") or {}
    months = {}
    for t in trips:
        m = months.setdefault(t["date"][:7], {"rides": 0, "rail": 0, "bus": 0, "fares": 0.0, "loaded": 0.0, "benefit_loaded": 0.0})
        ch = t.get("change") or 0
        if t["kind"] in ("rail", "bus"):
            m["rides"] += 1
            m[t["kind"]] += 1
            m["fares"] = round(m["fares"] + max(0.0, -ch), 2)
        elif t["kind"] == "load":
            m["loaded"] = round(m["loaded"] + max(0.0, ch), 2)
        elif t["kind"] == "benefit":
            m["benefit_loaded"] = round(m["benefit_loaded"] + max(0.0, ch), 2)
    start = date(today.year - 1, today.month, 1).isoformat()
    bank = [t for t in ledger.load_transactions(start, today.isoformat())
            if METRO_BANK.search(f"{t.get('merchant') or ''} {t.get('name') or ''}") and (t.get("amount") or 0) > 0]
    bank_by_month = {}
    for t in bank:
        bank_by_month[t["date"][:7]] = round(bank_by_month.get(t["date"][:7], 0) + t["amount"], 2)
    amt, b0, b1 = _num(ben.get("monthly")), ben.get("start") or "", ben.get("end") or ""
    cur = today.isoformat()[:7]
    keys = sorted(set(months) | set(bank_by_month) | ({cur} if amt else set()))
    for k in keys:
        m = months.setdefault(k, {"rides": 0, "rail": 0, "bus": 0, "fares": 0.0, "loaded": 0.0, "benefit_loaded": 0.0})
        m["bank"] = bank_by_month.get(k, 0.0)
        active = amt is not None and (not b0 or k >= b0[:7]) and (not b1 or k <= b1[:7])
        m["benefit"] = amt if active else None
        if active:
            m["benefit_left"] = round(amt - m["fares"], 2)
    latest_balance = next((t["balance"] for t in trips if t.get("balance") is not None), None)
    return {"trips": trips[:200], "by_month": dict(sorted(months.items())), "benefit": ben or {},
            "balance": latest_balance, "balance_date": next((t["date"] for t in trips if t.get("balance") is not None), None),
            "totals": {"rides": sum(m["rides"] for m in months.values()), "fares": round(sum(m["fares"] for m in months.values()), 2),
                       "first": trips[-1]["date"] if trips else None, "last": trips[0]["date"] if trips else None},
            "top_stations": [{"name": n, "visits": c} for n, c in Counter(
                s for t in trips if t["kind"] in ("rail", "entry") for s in (t.get("entry"), t.get("exit")) if s).most_common(6)],
            "bank_total": round(sum(t["amount"] for t in bank), 2)}


def commute_view():
    m = metro_view()
    r = rides_view()
    months = sorted(set(m["by_month"]) | set(r["by_month"]))[-12:]
    return {"months": [{"month": k, "metro_rides": m["by_month"].get(k, {}).get("rides", 0),
                        "metro_fares": m["by_month"].get(k, {}).get("fares", 0.0),
                        "benefit": m["by_month"].get(k, {}).get("benefit"),
                        "bike_rides": r["by_month"].get(k, {}).get("rides", 0),
                        "bike_spent": r["by_month"].get(k, {}).get("spent", 0.0),
                        "bike_miles": r["by_month"].get(k, {}).get("miles", 0.0)} for k in months],
            "metro": {"totals": m["totals"], "benefit": m["benefit"], "balance": m["balance"]},
            "bike": {"totals": r["totals"], "membership": r["membership"]}}


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

    @bp.get("/api/travel/metro")
    @require_owner
    def metro():
        return jsonify(metro_view())

    @bp.post("/api/travel/metro/import")
    @require_owner
    def metro_import():
        b = request.get_json(silent=True) or {}
        if isinstance(b.get("csv"), str):
            trips, err = parse_smartrip_csv(b["csv"][:2_000_000])
            if err:
                return jsonify(error=err), 400
        elif isinstance(b.get("trips"), list):
            trips = [t for t in (clean_metro(x) for x in b["trips"][:5000]) if t]
        else:
            return jsonify(error="send csv text or a trips list"), 400
        col, added = user_ref().collection("metro_trips"), 0
        for t in trips:
            ref = col.document(_metro_id(t))
            if not ref.get().exists:
                added += 1
            ref.set(t)
        return jsonify(metro_view() | {"added": added, "read": len(trips)})

    @bp.put("/api/travel/metro/benefit")
    @require_owner
    def metro_benefit():
        b = request.get_json(silent=True) or {}
        amt = _num(b.get("monthly"))
        if amt is not None and amt < 0:
            return jsonify(error="amount can't be negative"), 400
        dates = {k: str(b.get(k) or "")[:10] for k in ("start", "end")}
        if any(v and not re.match(r"^\d{4}-\d{2}-\d{2}$", v) for v in dates.values()):
            return jsonify(error="dates must be YYYY-MM-DD"), 400
        _settings_ref().set({"transit_benefit": {"monthly": amt, **dates, "label": str(b.get("label") or "").strip()[:60]}}, merge=True)
        return jsonify(metro_view())

    @bp.delete("/api/travel/metro")
    @require_owner
    def metro_forget():
        for d in list(user_ref().collection("metro_trips").stream()):
            user_ref().collection("metro_trips").document(d.id).delete()
        return jsonify(metro_view())

    @bp.get("/api/travel/commute")
    @require_owner
    def commute():
        return jsonify(commute_view())

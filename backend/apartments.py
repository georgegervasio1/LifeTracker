"""Apartments: listings from the weekly apartment digest (or a file), Zillow-style cards with saved photo copies,
saved spots with star ratings and notes, price history, the digest emails themselves, and a map export.

  GET    /api/apartments                      listings, criteria, digests, wishlist order
  POST   /api/apartments/import               {digest_date?, digest?: {subject, body, kind}, listings: [...], save_photos?}
                                              owner sign-in, or header X-Ingest-Key (= env APARTMENTS_INGEST_KEY)
  PATCH  /api/apartments/<id>                 {status?, notes?, rank?, rating?}
  DELETE /api/apartments/<id>
  PUT    /api/apartments/order                {ids: [...]}  wishlist order
  PUT    /api/apartments/criteria             {city, max_rent, stretch_rent, min_beds, move_in, big_sqft}
  GET    /api/apartments/photo/<pid>          a saved photo copy (image bytes)
  POST   /api/apartments/<id>/photos/save     download copies of the listing's photos now
  GET    /api/apartments/export.kml?scope=saved|all   spots for Google My Maps (private listings never included)

A listing (users/{uid}/apartments/{id}): url?, address/title, rent, sqft, beds, baths, lat, lon, neighborhood,
nearest_stop, stop_mi, available, amenities[], photos[] (source URLs), photo_copies[] (saved copy ids, same order),
description, fees, zpid, source, pick (the search suggested it), in_zone, private, context, price_history[{date, rent}],
plus what the user adds: status, notes, rank, rating (1-5). User fields survive later digests; a rent change on a
later digest is added to price_history. Nothing is guessed: what a listing didn't give stays blank.
Digest docs (apartment_digests/{date}) keep the subject and text of the email and which listings it carried.
"""
import base64
import hashlib
import hmac
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from xml.sax.saxutils import escape

import requests
from flask import Blueprint, Response, g, jsonify, request

bp = Blueprint("apartments", __name__)
db = firestore = require_owner = user_ref = None

STATUSES = ("new", "saved", "touring", "applied", "passed")
WISHLIST = ("saved", "touring", "applied")
LISTING_NUM = ("rent", "sqft", "beds", "baths", "lat", "lon", "stop_mi", "score")
LISTING_TXT = ("title", "address", "neighborhood", "available", "nearest_stop", "source", "summary", "unit", "zpid",
               "fees", "context")
CRITERIA_NUM = ("max_rent", "stretch_rent", "min_beds", "big_sqft")
MAX_IMPORT = 300
MAX_PHOTOS = 8
MAX_PHOTO_BYTES = 700_000


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


def _num(v):
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v))
    return float(m.group(0).replace(",", "")) if m else None


def _norm_addr(a):
    return re.sub(r"[^a-z0-9]+", " ", (a or "").lower()).strip()


def listing_id(url=None, address=None):
    """Same listing = same URL without query string (or, for a listing with no link, the same address)."""
    if url:
        u = re.sub(r"[?#].*$", "", url.strip().lower()).rstrip("/")
        key = re.sub(r"^https?://(www\.)?", "", u)
    else:
        key = "addr:" + _norm_addr(address)
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def clean_listing(x):
    """Validate one listing. Returns (listing, error)."""
    if not isinstance(x, dict):
        return None, "not an object"
    url = str(x.get("url") or "").strip()
    if url and (not re.match(r"^https?://", url) or len(url) > 1000):
        return None, "url must be http(s)"
    if not url and not str(x.get("address") or "").strip():
        return None, "needs a url or an address"
    out = {"url": url} if url else {}
    for k in LISTING_NUM:
        v = _num(x.get(k))
        if v is not None:
            out[k] = int(v) if k in ("rent", "sqft") else round(v, 6 if k in ("lat", "lon") else 2)
    for k in LISTING_TXT:
        v = str(x.get(k) or "").strip()
        if v:
            out[k] = v[:300]
    if x.get("description"):
        out["description"] = str(x["description"]).strip()[:1500]
    am = x.get("amenities") or []
    if isinstance(am, str):
        am = [a.strip() for a in am.split(",")]
    out["amenities"] = [str(a).strip()[:40] for a in am if str(a).strip()][:16]
    ph = x.get("photos") or []
    out["photos"] = [str(p).strip() for p in ph if re.match(r"^https://", str(p).strip()) and len(str(p)) < 600][:MAX_PHOTOS]
    for k in ("pick", "in_zone", "private"):
        if isinstance(x.get(k), bool):
            out[k] = x[k]
    hist = []
    for h in x.get("price_history") or []:
        if isinstance(h, dict) and re.match(r"^\d{4}-\d{2}-\d{2}$", str(h.get("date") or "")) and _num(h.get("rent")) is not None:
            hist.append({"date": h["date"], "rent": int(_num(h["rent"]))})
    if hist:
        out["price_history"] = sorted(hist, key=lambda h: h["date"])[-20:]
    if "lat" in out and not -90 <= out["lat"] <= 90 or "lon" in out and not -180 <= out["lon"] <= 180:
        out.pop("lat", None), out.pop("lon", None)
    if not (out.get("title") or out.get("address")):
        out["title"] = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    return out, None


# ---------------------------------------------------------------- photo copies
def fetch_bytes(url):
    """Download one photo. Tests replace this."""
    r = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0 LifeTracker"})
    r.raise_for_status()
    return r.content, (r.headers.get("Content-Type") or "image/jpeg").split(";")[0]


def _copy_url(u):
    """Store a mid-size copy where the host offers sizes (Zillow: -cc_ft_1536 -> -cc_ft_960)."""
    return re.sub(r"-cc_ft_1536\.", "-cc_ft_960.", u)


def save_photo_copies(aid, photos, have=None):
    """Download each photo once; returns copy ids in the same order (None where a download failed)."""
    have = list(have or [])
    col = user_ref().collection("apartment_photos")
    todo = [(n, u) for n, u in enumerate(photos) if not (n < len(have) and have[n])]

    def one(item):
        n, u = item
        try:
            data, ctype = fetch_bytes(_copy_url(u))
            if not ctype.startswith("image/") or len(data) > MAX_PHOTO_BYTES or not data:
                return n, None, None
            return n, data, ctype
        except Exception:
            return n, None, None

    out = (have + [None] * len(photos))[:len(photos)]
    if todo:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for n, data, ctype in ex.map(one, todo):
                if data:
                    pid = f"{aid}_{hashlib.sha1(photos[n].encode()).hexdigest()[:10]}"
                    col.document(pid).set({"listing": aid, "url": photos[n], "type": ctype,
                                           "data": base64.b64encode(data).decode(), "bytes": len(data)})
                    out[n] = pid
    return out


# ---------------------------------------------------------------- import + view
def import_listings(items, digest_date=None, digest=None, save_photos=True):
    digest_date = digest_date or date.today().isoformat()
    col = user_ref().collection("apartments")
    added, updated, bad, ids, drops = 0, 0, [], [], 0
    for i, x in enumerate(items[:MAX_IMPORT]):
        lst, err = clean_listing(x)
        if err:
            bad.append({"index": i, "error": err})
            continue
        aid = listing_id(lst.get("url"), lst.get("address"))
        ids.append(aid)
        ref = col.document(aid)
        snap = ref.get()
        old = (snap.to_dict() if snap.exists else None) or {}
        seen = sorted(set(old.get("seen_in") or []) | {digest_date})
        hist = {h["date"]: h for h in (old.get("price_history") or []) + (lst.get("price_history") or [])}
        if old.get("rent") is not None and lst.get("rent") is not None and old["rent"] != lst["rent"]:
            hist.setdefault(old.get("last_seen") or digest_date, {"date": old.get("last_seen") or digest_date, "rent": old["rent"]})
            if lst["rent"] < old["rent"]:
                drops += 1
        keep = {k: old[k] for k in ("status", "notes", "rank", "rating", "first_seen") if k in old}
        if old.get("private"):
            keep["private"] = True                      # once private, a later digest can't make it public
        doc = {**old, **lst, "status": "new", "notes": "", "first_seen": digest_date, **keep,
               "last_seen": max(seen), "seen_in": seen[-26:]}
        if hist:
            doc["price_history"] = sorted(hist.values(), key=lambda h: h["date"])[-20:]
        if not lst.get("photos") and old.get("photos"):
            doc["photos"], doc["photo_copies"] = old["photos"], old.get("photo_copies") or []
        elif lst.get("photos"):
            same = old.get("photos") == lst["photos"]
            doc["photo_copies"] = old.get("photo_copies") if same else []
            if save_photos:
                doc["photo_copies"] = save_photo_copies(aid, lst["photos"], doc["photo_copies"])
        ref.set(doc)
        added, updated = (added, updated + 1) if old else (added + 1, updated)
    dg = {"date": digest_date, "new": added, "updated": updated, "received": len(items), "listing_ids": ids[:MAX_IMPORT],
          "price_drops": drops}
    if isinstance(digest, dict):
        for k, n in (("subject", 300), ("body", 20000), ("kind", 30)):
            if digest.get(k):
                dg[k] = str(digest[k])[:n]
    user_ref().collection("apartment_digests").document(digest_date).set(dg, merge=True)
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
        ph = r.get("price_history") or []
        if ph and rent is not None and ph[-1]["rent"] != rent:
            r["price_change"] = rent - ph[-1]["rent"]
    rows.sort(key=lambda r: (r.get("last_seen") or "", r.get("sqft") or 0), reverse=True)
    wish = sorted((r for r in rows if r.get("status") in WISHLIST), key=lambda r: (r.get("rank") is None, r.get("rank") or 0))
    return {"listings": rows, "wishlist": [r["id"] for r in wish], "criteria": crit, "digests": digests[:30],
            "latest_digest": latest, "statuses": list(STATUSES)}


def gmaps_link(r):
    if r.get("lat") is not None and r.get("lon") is not None:
        return f"https://www.google.com/maps/search/?api=1&query={r['lat']},{r['lon']}"
    q = r.get("address") or r.get("title") or ""
    return "https://www.google.com/maps/search/?api=1&query=" + requests.utils.quote(q)


def to_kml(rows, name="Apartment search"):
    marks = []
    for r in rows:
        if r.get("private") or r.get("lat") is None or r.get("lon") is None:
            continue
        stars = ("★" * int(r["rating"]) + "☆" * (5 - int(r["rating"]))) if r.get("rating") else "Not rated"
        facts = " · ".join(x for x in [f"${r['rent']:,}/mo" if r.get("rent") else "", f"{r['sqft']:,} sq ft" if r.get("sqft") else "",
                                       f"{r['beds']:g} bd" if r.get("beds") is not None else "", f"{r['baths']:g} ba" if r.get("baths") is not None else ""] if x)
        desc = "\n".join(x for x in [stars, facts, r.get("notes") or "", r.get("url") or ""] if x)
        marks.append(f"<Placemark><name>{escape(r.get('address') or r.get('title') or '')}</name>"
                     f"<description>{escape(desc)}</description>"
                     f"<Point><coordinates>{r['lon']},{r['lat']},0</coordinates></Point></Placemark>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            f"<name>{escape(name)}</name>{''.join(marks)}</Document></kml>")


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
    @bp.get("/api/maps/config")
    @require_owner
    def maps_config():
        """The Google Maps browser key (env GOOGLE_MAPS_KEY, from Secret Manager), kept out of the public repo and the demo."""
        key = "" if getattr(g, "uid", "") == "demo" else os.environ.get("GOOGLE_MAPS_KEY", "").strip()
        return jsonify(google_maps_key=key)

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
        save = body.get("save_photos", True) is not False
        run = lambda: import_listings(items, dd, body.get("digest"), save)
        if request.headers.get("X-Ingest-Key"):
            if not _ingest_ok():
                return jsonify(error="forbidden"), 403
            try:
                g.uid = owner_uid()
            except Exception:
                return jsonify(error="owner account not found"), 500
            return jsonify(run())
        return require_owner(lambda: jsonify(run() | apartments_view()))()

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
        if "rating" in b:
            v = _num(b["rating"])
            if v is not None and not 1 <= v <= 5:
                return jsonify(error="rating is 1 to 5"), 400
            upd["rating"] = None if v is None else int(round(v))
        if upd:
            ref.set(upd, merge=True)
        return jsonify(apartments_view())

    @bp.delete("/api/apartments/<aid>")
    @require_owner
    def apartment_delete(aid):
        ref = user_ref().collection("apartments").document(aid)
        snap = ref.get()
        for pid in ((snap.to_dict() or {}).get("photo_copies") or []) if snap.exists else []:
            if pid:
                user_ref().collection("apartment_photos").document(pid).delete()
        ref.delete()
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

    @bp.get("/api/apartments/photo/<pid>")
    @require_owner
    def apartment_photo(pid):
        s = user_ref().collection("apartment_photos").document(pid).get()
        if not s.exists:
            return jsonify(error="not found"), 404
        d = s.to_dict()
        return Response(base64.b64decode(d["data"]), mimetype=d.get("type") or "image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400"})

    @bp.post("/api/apartments/<aid>/photos/save")
    @require_owner
    def apartment_photos_save(aid):
        ref = user_ref().collection("apartments").document(aid)
        snap = ref.get()
        if not snap.exists:
            return jsonify(error="not found"), 404
        d = snap.to_dict()
        copies = save_photo_copies(aid, d.get("photos") or [], d.get("photo_copies"))
        ref.set({"photo_copies": copies}, merge=True)
        return jsonify(apartments_view() | {"saved": sum(1 for c in copies if c)})

    @bp.get("/api/apartments/export.kml")
    @require_owner
    def apartments_kml():
        scope = request.args.get("scope", "saved")
        rows = [d.to_dict() | {"id": d.id} for d in user_ref().collection("apartments").stream()]
        if scope == "saved":
            rows = [r for r in rows if r.get("status") in WISHLIST]
        else:
            rows = [r for r in rows if r.get("status") != "passed"]
        crit = _settings().get("apt_criteria") or {}
        name = f"Apartments{(' · ' + crit['city']) if crit.get('city') else ''}"
        return Response(to_kml(rows, name), mimetype="application/vnd.google-earth.kml+xml",
                        headers={"Content-Disposition": 'attachment; filename="apartments.kml"'})

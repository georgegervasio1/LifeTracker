"""Social → Events: the user's Partiful events, read from Partiful's calendar sync link.

Partiful has no public API. What it does offer is a private calendar link per person (Partiful → profile → Settings →
"Calendar sync preferences", or an event's calendar icon → Google Calendar → Copy Link / Apple Calendar → Subscribe).
Calendar apps subscribe to it; this module reads the same link on the server, so the events show up here too.
Partiful says Google Calendar refreshes it roughly once a day; we read it whenever the user syncs (and at most every
SYNC_EVERY seconds on page load). Single events can also be added from an .ics file (an event's "Add this event only").

  GET    /api/social/events            {connected, last_sync, error, upcoming[], past[], hidden}
  POST   /api/social/partiful          {url}  save the calendar link and sync it
  POST   /api/social/sync              read the link again
  DELETE /api/social/partiful          forget the link (?events=1 also removes the events it brought in)
  POST   /api/social/import            {ics} add the events in an .ics file
  PATCH  /api/social/events/<id>       {hidden, notes}

The link is a secret (anyone with it can see the user's events), so it is stored in integrations/partiful and never sent
back to the browser. Events are kept in users/{uid}/social_events so past events stay after they leave the feed.
RSVP status is shown only when the feed carries it (ATTENDEE PARTSTAT); nothing is guessed.
"""
import hashlib
import ipaddress
import re
import socket
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from flask import Blueprint, jsonify, request

bp = Blueprint("social", __name__)
db = firestore = require_owner = user_ref = None
DEFAULT_TZ = "America/New_York"      # for times in a feed that carry no zone ("floating" times)
SYNC_EVERY = 3 * 3600
MAX_ICS = 3 * 1024 * 1024
LINK_RE = re.compile(r"https://(?:www\.)?partiful\.com/e/[A-Za-z0-9_-]+")
PARTSTAT = {"ACCEPTED": "going", "TENTATIVE": "maybe", "DECLINED": "can't go", "NEEDS-ACTION": "invited"}
# Common Windows zone names some calendar writers use in TZID
WINDOWS_TZ = {"Eastern Standard Time": "America/New_York", "Central Standard Time": "America/Chicago",
              "Mountain Standard Time": "America/Denver", "Pacific Standard Time": "America/Los_Angeles"}


def init(app, _db, _firestore, _require_owner, _user_ref):
    global db, firestore, require_owner, user_ref
    db, firestore, require_owner, user_ref = _db, _firestore, _require_owner, _user_ref
    _register_routes()
    app.register_blueprint(bp)


def _col():
    return user_ref().collection("social_events")


def _conn_ref():
    return user_ref().collection("integrations").document("partiful")


# ---------------------------------------------------------------- iCalendar parsing (pure)
def _unfold(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n[ \t]", "", text).split("\n")


def _unescape(v):
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def _prop(line):
    """'DTSTART;TZID=America/New_York:20261114T190000' -> ('DTSTART', {'TZID': ...}, '20261114T190000')."""
    m = re.match(r'^([A-Za-z0-9-]+)((?:;[^:;"]+=(?:"[^"]*"|[^:;]*))*):(.*)$', line)
    if not m:
        return None, {}, ""
    params = {}
    for k, v in re.findall(r';([^=;]+)=("[^"]*"|[^;]*)', m.group(2)):
        params[k.upper()] = v.strip('"')
    return m.group(1).upper(), params, m.group(3)


def _zone(name):
    name = WINDOWS_TZ.get(name, name)
    try:
        return ZoneInfo(name)
    except Exception:
        return None


def parse_dt(value, params, default_tz=DEFAULT_TZ):
    """-> (datetime aware, all_day). All-day dates come back as midnight in default_tz with all_day True."""
    v = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", v):
        try:
            d = datetime.strptime(v[:8], "%Y%m%d")
        except ValueError:
            return None, False
        return d.replace(tzinfo=ZoneInfo(default_tz)), True
    m = re.fullmatch(r"(\d{8}T\d{4})(\d{2})?(Z)?", v)
    if not m:
        return None, False
    try:
        d = datetime.strptime(m.group(1) + (m.group(2) or "00"), "%Y%m%dT%H%M%S")
    except ValueError:
        return None, False
    if m.group(3):
        return d.replace(tzinfo=timezone.utc), False
    tz = _zone(params.get("TZID", "")) if params.get("TZID") else None
    return d.replace(tzinfo=tz or ZoneInfo(default_tz)), False


def _event_link(*texts):
    for t in texts:
        m = LINK_RE.search(t or "")
        if m:
            return m.group(0)
    return ""


def parse_ics(text, default_tz=DEFAULT_TZ):
    """VEVENTs in an iCalendar text -> list of plain dicts. Unknown properties are ignored; no network."""
    events, cur, depth, cal_name = [], None, 0, ""
    for line in _unfold(text or ""):
        if not line.strip():
            continue
        name, params, value = _prop(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            cur, depth = {"attendees": []}, 0
            continue
        if cur is None:
            if name == "X-WR-CALNAME":
                cal_name = _unescape(value)
            continue
        if name == "BEGIN":          # VALARM etc. inside an event
            depth += 1
            continue
        if name == "END" and value.upper() == "VEVENT":
            ev = _finish(cur, default_tz)
            if ev:
                events.append(ev)
            cur = None
            continue
        if name == "END":
            depth -= 1
            continue
        if depth:
            continue
        if name in ("DTSTART", "DTEND"):
            cur[name] = (value, params)
        elif name == "ATTENDEE":
            cur["attendees"].append(params.get("PARTSTAT", "").upper())
        elif name == "ORGANIZER":
            cur["host"] = params.get("CN", "") or re.sub(r"^mailto:", "", value, flags=re.I)
        elif name in ("UID", "SUMMARY", "LOCATION", "DESCRIPTION", "URL", "STATUS", "GEO", "DURATION", "SEQUENCE"):
            cur[name] = _unescape(value)
    for e in events:
        e["calendar"] = cal_name
    return events


def _duration(v):
    m = re.fullmatch(r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", (v or "").strip())
    if not m:
        return None
    w, d, h, mi, s = (int(x or 0) for x in m.groups())
    return timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def _finish(cur, default_tz):
    if "DTSTART" not in cur:
        return None
    start, all_day = parse_dt(*cur["DTSTART"], default_tz=default_tz)
    if not start:
        return None
    end = parse_dt(*cur["DTEND"], default_tz=default_tz)[0] if "DTEND" in cur else None
    if not end and cur.get("DURATION"):
        dur = _duration(cur["DURATION"])
        end = start + dur if dur else None
    title = (cur.get("SUMMARY") or "").strip() or "Untitled event"
    uid = (cur.get("UID") or "").strip() or f"{title}|{start.isoformat()}"
    desc = (cur.get("DESCRIPTION") or "").strip()
    out = {
        "uid": uid[:300], "title": title[:200], "all_day": all_day,
        "start": start.date().isoformat() if all_day else start.isoformat(),
        "end": (end.date().isoformat() if all_day else end.isoformat()) if end else "",
        "start_ts": int(start.timestamp()), "end_ts": int(end.timestamp()) if end else None,
        "location": (cur.get("LOCATION") or "").strip()[:300],
        "description": desc[:4000],
        "url": "", "status": (cur.get("STATUS") or "").strip().lower()[:20], "host": (cur.get("host") or "")[:120],
        "rsvp": "",
    }
    url = (cur.get("URL") or "").strip()
    out["url"] = _event_link(url, desc) or (url if url.startswith("https://") else "")
    if cur.get("GEO"):
        try:
            lat, lon = (float(x) for x in cur["GEO"].replace(",", ";").split(";")[:2])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                out["lat"], out["lon"] = lat, lon
        except ValueError:
            pass
    # Only when the feed names one attendee (the person whose calendar it is) can we tell their RSVP.
    stats = [a for a in cur.get("attendees", []) if a]
    if len(stats) == 1 and stats[0] in PARTSTAT:
        out["rsvp"] = PARTSTAT[stats[0]]
    return out


def event_id(uid):
    return hashlib.sha1(uid.encode("utf-8")).hexdigest()[:20]


# ---------------------------------------------------------------- reading the link
def normalize_url(raw):
    """webcal:// -> https://. Only https links to public hosts (the server fetches this URL)."""
    u = (raw or "").strip()
    if u.lower().startswith("webcal://"):
        u = "https://" + u[9:]
    p = urlparse(u)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
        return None, "Paste the https:// or webcal:// calendar link from Partiful"
    host = p.hostname.lower()
    if host == "localhost" or host.endswith((".internal", ".local")) or "." not in host:
        return None, "That link doesn't point to a public website"
    try:
        ipaddress.ip_address(host)
        return None, "That link doesn't point to a public website"
    except ValueError:
        pass
    return u, None


def _public_host(host):
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    return all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


def fetch_ics(url):
    """GET the calendar text. Replaced in tests."""
    if not _public_host(urlparse(url).hostname):
        raise ValueError("That link doesn't point to a public website")
    r = requests.get(url, timeout=20, allow_redirects=False, headers={"User-Agent": "LifeTracker calendar reader"},
                     stream=True)
    if r.status_code in (301, 302, 303, 307, 308):
        nxt, err = normalize_url(r.headers.get("Location", ""))
        if err or not _public_host(urlparse(nxt).hostname):
            raise ValueError("The calendar link redirected somewhere unexpected")
        r = requests.get(nxt, timeout=20, allow_redirects=False, stream=True)
    if r.status_code != 200:
        raise ValueError(f"Partiful answered {r.status_code}. The link may have been reset; copy it again.")
    body = r.raw.read(MAX_ICS + 1, decode_content=True)
    if len(body) > MAX_ICS:
        raise ValueError("That calendar is too large to read")
    return body.decode("utf-8", errors="replace")


def _store(events, source, now):
    """Upsert parsed events. Returns (added, updated). User fields (hidden, notes) are kept."""
    added = updated = 0
    col = _col()
    for e in events:
        ref = col.document(event_id(e["uid"]))
        snap = ref.get()
        row = dict(e, source=source, last_seen=now)
        if source == "partiful_feed":
            row["in_feed"] = True
        if snap.exists:
            updated += 1
            ref.set(row, merge=True)
        else:
            added += 1
            ref.set(row | {"first_seen": now, "hidden": False, "notes": ""})
    return added, updated


def sync(conn, text=None):
    """Read the saved link (or `text` already read from it); mark feed events that dropped out (kept, labeled)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        text = text if text is not None else fetch_ics(conn["url"])
        if "BEGIN:VCALENDAR" not in text[:2000].upper():
            raise ValueError("That link didn't return a calendar")
        events = parse_ics(text)
    except (requests.RequestException, ValueError) as e:
        _conn_ref().set({"last_error": str(e)[:300], "last_attempt": now}, merge=True)
        return {"ok": False, "error": str(e)[:300]}
    added, updated = _store(events, "partiful_feed", now)
    seen = {event_id(e["uid"]) for e in events}
    dropped = 0
    for d in _col().stream():
        x = d.to_dict()
        if x.get("source") == "partiful_feed" and x.get("in_feed") and d.id not in seen:
            _col().document(d.id).set({"in_feed": False}, merge=True)
            dropped += 1
    _conn_ref().set({"last_sync": now, "last_attempt": now, "last_error": "", "last_count": len(events)}, merge=True)
    return {"ok": True, "events": len(events), "added": added, "updated": updated, "dropped": dropped}


def _public(d):
    x = d.to_dict() | {"id": d.id}
    x.pop("uid", None)
    return x


def events_view():
    snap = _conn_ref().get()
    conn = snap.to_dict() if snap.exists else {}
    now = time.time()
    rows = [_public(d) for d in _col().stream()]
    up, past, hidden = [], [], 0
    for e in rows:
        if e.get("hidden"):
            hidden += 1
        end = e.get("end_ts") or (e["start_ts"] + (86400 if e.get("all_day") else 3 * 3600))
        (up if end >= now else past).append(e)
    up.sort(key=lambda e: e["start_ts"])
    past.sort(key=lambda e: -e["start_ts"])
    return {"connected": bool(conn.get("url")), "last_sync": conn.get("last_sync", ""), "error": conn.get("last_error", ""),
            "last_count": conn.get("last_count"), "upcoming": up, "past": past, "hidden": hidden, "default_tz": DEFAULT_TZ}


def _register_routes():
    @bp.get("/api/social/events")
    @require_owner
    def social_events():
        snap = _conn_ref().get()
        conn = snap.to_dict() if snap.exists else {}
        if conn.get("url") and request.args.get("refresh") != "0":
            last = conn.get("last_attempt") or ""
            try:
                age = time.time() - datetime.fromisoformat(last).timestamp() if last else 1e9
            except ValueError:
                age = 1e9
            if age > SYNC_EVERY:
                sync(conn)
        return jsonify(events_view())

    @bp.post("/api/social/partiful")
    @require_owner
    def social_connect():
        url, err = normalize_url((request.get_json(silent=True) or {}).get("url"))
        if err:
            return jsonify(error=err), 400
        try:
            text = fetch_ics(url)
        except (requests.RequestException, ValueError) as e:
            return jsonify(error=f"Couldn't read that link: {str(e)[:200]}"), 400
        if "BEGIN:VCALENDAR" not in text[:2000].upper():
            return jsonify(error="That link didn't return a calendar. Copy the calendar link from Partiful again."), 400
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _conn_ref().set({"url": url, "connected_at": now})
        res = sync({"url": url}, text)
        return jsonify(events_view() | {"sync": res})

    @bp.post("/api/social/sync")
    @require_owner
    def social_sync():
        snap = _conn_ref().get()
        conn = snap.to_dict() if snap.exists else {}
        if not conn.get("url"):
            return jsonify(error="Paste your Partiful calendar link first"), 400
        res = sync(conn)
        return jsonify(events_view() | {"sync": res}), (200 if res["ok"] else 502)

    @bp.delete("/api/social/partiful")
    @require_owner
    def social_disconnect():
        _conn_ref().delete()
        removed = 0
        if request.args.get("events") == "1":
            for d in _col().stream():
                if d.to_dict().get("source") == "partiful_feed":
                    _col().document(d.id).delete()
                    removed += 1
        return jsonify(events_view() | {"removed": removed})

    @bp.post("/api/social/import")
    @require_owner
    def social_import():
        text = str((request.get_json(silent=True) or {}).get("ics") or "")
        if len(text) > MAX_ICS:
            return jsonify(error="That file is too large"), 400
        events = parse_ics(text)
        if not events:
            return jsonify(error="No events found in that file. Use the .ics file from an event's Add to calendar."), 400
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        added, updated = _store(events, "ics_import", now)
        return jsonify(events_view() | {"added": added, "updated": updated})

    @bp.patch("/api/social/events/<eid>")
    @require_owner
    def social_patch(eid):
        b = request.get_json(silent=True) or {}
        ref = _col().document(eid)
        if not ref.get().exists:
            return jsonify(error="not found"), 404
        upd = {}
        if "hidden" in b:
            upd["hidden"] = bool(b["hidden"])
        if "notes" in b:
            upd["notes"] = str(b.get("notes") or "")[:2000]
        if upd:
            ref.set(upd, merge=True)
        return jsonify(_public(ref.get()))

    @bp.delete("/api/social/events/<eid>")
    @require_owner
    def social_delete(eid):
        _col().document(eid).delete()
        return jsonify(events_view())

"""Offline test of social.py (Social → Events from a Partiful calendar link): parser + routes with a fake feed (made up)."""
import os as _os
HERE = _os.environ.get("LT_TESTS") or _os.path.dirname(_os.path.abspath(__file__))
exec(open(_os.path.join(HERE, "test_health.py")).read().split("# ---- fake WHOOP HTTP")[0])

import social
from datetime import datetime, timedelta, timezone
c = main.app.test_client()

# ---- parser: folding, escapes, TZID / UTC / floating / all-day times, DURATION, GEO, RSVP, event link, VALARM skipped
now = datetime.now(timezone.utc)
f = lambda d: d.strftime("%Y%m%dT%H%M%SZ")
soon, later, before = now + timedelta(days=3), now + timedelta(days=20), now - timedelta(days=30)
FEED = "\r\n".join([
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Sample//Calendar//EN", "X-WR-CALNAME:My events",
    "BEGIN:VEVENT", "UID:evt-1@sample", f"DTSTART:{f(soon)}", f"DTEND:{f(soon + timedelta(hours=3))}",
    "SUMMARY:Rooftop party\\, part 2", "LOCATION:1 Sample St NW\\, Washington\\, DC",
    "DESCRIPTION:Bring a friend.\\nRSVP at https://partiful.com/e/AbC123xyz", "  more text folded",
    "ORGANIZER;CN=\"Sample Host\":mailto:host@example.com", "ATTENDEE;CN=Me;PARTSTAT=TENTATIVE:mailto:me@example.com",
    "GEO:38.9;-77.03", "BEGIN:VALARM", "TRIGGER:-PT1H", "DESCRIPTION:alarm text", "END:VALARM", "END:VEVENT",
    "BEGIN:VEVENT", "UID:evt-2@sample", "DTSTART;TZID=America/Chicago:" + later.strftime("%Y%m%d") + "T200000", "DURATION:PT2H30M",
    "SUMMARY:Out of town", "LOCATION:Sample Hall, Chicago, IL", "URL:https://partiful.com/e/Zz9", "END:VEVENT",
    "BEGIN:VEVENT", "UID:evt-3@sample", "DTSTART;VALUE=DATE:" + before.strftime("%Y%m%d"), "SUMMARY:Picnic", "STATUS:CONFIRMED", "END:VEVENT",
    "BEGIN:VEVENT", "UID:evt-4@sample", "DTSTART:20261201T190000", "SUMMARY:Floating time", "ATTENDEE;PARTSTAT=ACCEPTED:a", "ATTENDEE;PARTSTAT=DECLINED:b", "END:VEVENT",
    "BEGIN:VEVENT", "SUMMARY:No start", "END:VEVENT",
    "END:VCALENDAR", ""])
ev = social.parse_ics(FEED)
assert len(ev) == 4, ev
a, b, p, fl = ev
assert a["title"] == "Rooftop party, part 2" and a["location"] == "1 Sample St NW, Washington, DC"
assert a["description"].startswith("Bring a friend.\nRSVP") and "more text folded" in a["description"] and "alarm" not in a["description"]
assert a["url"] == "https://partiful.com/e/AbC123xyz" and a["host"] == "Sample Host" and a["rsvp"] == "maybe"
assert (a["lat"], a["lon"]) == (38.9, -77.03) and a["calendar"] == "My events" and a["end_ts"] - a["start_ts"] == 3 * 3600
assert b["start"].endswith(("-06:00", "-05:00")) and b["end_ts"] - b["start_ts"] == 9000 and b["url"] == "https://partiful.com/e/Zz9"
assert p["all_day"] and len(p["start"]) == 10 and p["status"] == "confirmed" and p["rsvp"] == ""
assert fl["start"].startswith("2026-12-01T19:00:00-05:00") and fl["rsvp"] == ""     # two attendees: RSVP unknown, not guessed
assert social.parse_ics("not a calendar") == []

# ---- link checks: webcal becomes https; private hosts, IPs, http refused
assert social.normalize_url("webcal://feeds.example.com/cal/abc.ics")[0] == "https://feeds.example.com/cal/abc.ics"
for bad in ["http://example.com/x.ics", "https://localhost/x", "https://10.0.0.1/x", "https://metadata.google.internal/x",
            "https://user:pw@example.com/x", "https://example.com:8443/x", "ftp://example.com", ""]:
    assert social.normalize_url(bad)[1], bad

# ---- routes with a fake feed
served = {"text": FEED, "n": 0}
def fake_fetch(url):
    served["n"] += 1
    if "broken" in url: raise ValueError("Partiful answered 404. The link may have been reset; copy it again.")
    return served["text"]
social.fetch_ics = fake_fetch
r = c.get("/api/social/events").get_json()
assert not r["connected"] and r["upcoming"] == [] and served["n"] == 0
assert c.post("/api/social/sync").status_code == 400
assert c.post("/api/social/partiful", json={"url": "http://x.example.com/a"}).status_code == 400
assert c.post("/api/social/partiful", json={"url": "https://broken.example.com/a"}).status_code == 400
served["text"] = "<html>sign in</html>"
assert c.post("/api/social/partiful", json={"url": "https://feed.example.com/a"}).status_code == 400
served["text"] = FEED
r = c.post("/api/social/partiful", json={"url": "webcal://feed.example.com/secret-token"})
assert r.status_code == 200, r.get_json()
r = r.get_json()
assert r["connected"] and r["sync"]["added"] == 4 and served["n"] == 3      # one read on connect
assert [e["title"] for e in r["upcoming"]] == ["Rooftop party, part 2", "Floating time", "Out of town"] or \
       [e["title"] for e in r["upcoming"]][0] == "Rooftop party, part 2"
assert [e["title"] for e in r["past"]] == ["Picnic"]
assert "secret-token" not in str(r) and all("uid" not in e for e in r["upcoming"])   # the link is never sent back
assert r["upcoming"][0]["in_feed"] and r["upcoming"][0]["source"] == "partiful_feed"

# a page load within SYNC_EVERY doesn't re-read; notes/hidden survive a sync; an event that leaves the feed is kept, labeled
n = served["n"]; c.get("/api/social/events"); assert served["n"] == n
eid = r["upcoming"][0]["id"]
c.patch(f"/api/social/events/{eid}", json={"notes": "Bring snacks", "hidden": True})
served["text"] = FEED.replace("SUMMARY:Out of town", "SUMMARY:Out of town (moved)").replace("UID:evt-3@sample", "UID:evt-3b@sample")
r = c.post("/api/social/sync").get_json()
assert r["sync"]["ok"] and r["sync"]["added"] == 1 and r["sync"]["dropped"] == 1
e1 = next(e for e in r["upcoming"] if e["id"] == eid)
assert e1["notes"] == "Bring snacks" and e1["hidden"] and r["hidden"] == 1
assert any(e["title"] == "Out of town (moved)" for e in r["upcoming"])
old = next(e for e in r["past"] if e["title"] == "Picnic" and not e["in_feed"])
assert old
# a failed sync keeps the events and reports the error
DATA[next(k for k in DATA if k.endswith("/integrations/partiful"))]["url"] = "https://broken.example.com/a"
rr = c.post("/api/social/sync")
assert rr.status_code == 502 and "404" in rr.get_json()["error"] and len(rr.get_json()["upcoming"]) >= 3

# ---- .ics import (single event); disconnect keeps imports, removes feed events only when asked
ONE = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:single@sample\r\nDTSTART:" + f(later) + "\r\nSUMMARY:Book club\r\nEND:VEVENT\r\nEND:VCALENDAR"
assert c.post("/api/social/import", json={"ics": "nothing"}).status_code == 400
r = c.post("/api/social/import", json={"ics": ONE}).get_json()
assert r["added"] == 1 and any(e["title"] == "Book club" and e["source"] == "ics_import" for e in r["upcoming"])
r = c.delete("/api/social/partiful").get_json()
assert not r["connected"] and r["removed"] == 0 and len(r["upcoming"]) >= 4
r = c.delete("/api/social/partiful?events=1").get_json()
assert [e["title"] for e in r["upcoming"]] == ["Book club"] and r["past"] == []
print("social: ok")

"""Offline test of travel.py: bikeshare stations from a fake GBFS feed, bikeshare spend, apartment import and wishlist."""
import os as _os
HERE = _os.environ.get("LT_TESTS") or _os.path.dirname(_os.path.abspath(__file__))
exec(open(_os.path.join(HERE, "test_health.py")).read().split("# ---- fake WHOOP HTTP")[0])

import travel
from datetime import date

c = main.app.test_client()

# ---- fake GBFS feed
INFO = {"data": {"stations": [
    {"station_id": "1", "name": "Elm St & 3rd Ave", "lat": 38.900, "lon": -77.040, "capacity": 15},
    {"station_id": "2", "name": "Oak Plaza", "lat": 38.910, "lon": -77.020, "capacity": 19},
    {"station_id": "3", "name": "Elm St & 9th Ave", "lat": 38.950, "lon": -77.100, "capacity": 11}]}}
STATUS = {"data": {"stations": [
    {"station_id": "1", "num_bikes_available": 7, "num_ebikes_available": 2, "num_docks_available": 8,
     "is_renting": 1, "is_returning": 1, "last_reported": 1700000000},
    {"station_id": "2", "num_bikes_available": 0, "num_docks_available": 19, "is_renting": 1, "is_returning": 1}]}}
fetched = []
def fake_fetch(url):
    fetched.append(url)
    if "information" in url: return INFO
    if "status" in url: return STATUS
    raise AssertionError(url)
travel.fetch_json = fake_fetch

r = c.get("/api/travel/bike").get_json()
assert r["stations"] == [] and r["feed"] == "ok" and r["spend"]["charges"] == 0

r = c.get("/api/travel/bike/stations?q=elm st").get_json()["stations"]
assert [s["id"] for s in r] == ["1", "3"]
assert r[0]["bikes"] == 5 and r[0]["ebikes"] == 2 and r[0]["docks"] == 8 and r[0]["live"]
assert r[1]["live"] is False            # no status row: shown without counts, never zero-filled

r = c.get("/api/travel/bike/stations?lat=38.909&lon=-77.021").get_json()["stations"]
assert r[0]["id"] == "2" and r[0]["mi"] < 0.1 and r[0]["ebikes"] is None and r[0]["bikes"] == 0

assert c.put("/api/travel/bike/saved", json={"stations": "x"}).status_code == 400
r = c.put("/api/travel/bike/saved", json={"stations": [{"id": "2", "label": "Home"}, {"id": "1", "label": "Campus"},
                                                       {"id": "1"}, {"id": "99", "name": "Removed station"}]}).get_json()
assert [s["id"] for s in r["stations"]] == ["2", "1", "99"] and r["stations"][0]["label"] == "Home"
assert r["stations"][2].get("gone") and r["stations"][1]["bikes"] == 5
n = len(fetched)
c.get("/api/travel/bike")
assert len(fetched) == n                # cached

# feed down: saved stations still listed, marked not live
travel._cache.update(info=(0.0, None), status=(0.0, None))
travel.fetch_json = lambda url: (_ for _ in ()).throw(Exception("down"))
r = c.get("/api/travel/bike").get_json()
assert r["feed"] == "down" and len(r["stations"]) == 3 and not r["stations"][0]["live"]
assert c.get("/api/travel/bike/stations?q=elm").status_code == 502
travel.fetch_json = fake_fetch

# ---- bikeshare spend from the ledger
today = date.today()
m = today.isoformat()[:7]
DATA[f"users/dev/transactions/t1"] = {"date": f"{m}-01", "amount": 95.0, "merchant": "CAPITAL BIKESHARE", "account": "card"}
DATA[f"users/dev/transactions/t2"] = {"date": f"{m}-01", "amount": 2.4, "merchant": "Capital Bikeshare ride", "account": "card"}
DATA[f"users/dev/transactions/t3"] = {"date": f"{m}-01", "amount": 18.0, "merchant": "LYFT RIDE", "account": "card"}
DATA[f"users/dev/transactions/t4"] = {"date": "2001-01-01", "amount": 5.0, "merchant": "CAPITAL BIKESHARE", "account": "card"}
s = c.get("/api/travel/bike").get_json()["spend"]
assert s["charges"] == 2 and s["total"] == 97.4 and s["by_month"] == {m: 97.4}, s

# ---- apartments
r = c.get("/api/apartments").get_json()
assert r["listings"] == [] and r["latest_digest"] is None and r["criteria"] == {}

L = [{"url": "https://example.com/a/1?utm=x", "address": "100 Sample Ave", "rent": "$4,800", "sqft": "1,650", "beds": 3,
      "baths": 2, "lat": 41.9, "lon": -87.64, "nearest_stop": "Sample", "stop_mi": 0.4, "amenities": "gym, pool"},
     {"url": "https://example.com/a/2", "title": "Big loft", "rent": 5400, "sqft": 1200, "beds": 3},
     {"url": "ftp://bad"}, "nope"]
r = c.post("/api/apartments/import", json={"digest_date": "2026-11-01", "listings": L}).get_json()
assert r["added"] == 2 and r["updated"] == 0 and len(r["skipped"]) == 2
a1 = next(x for x in r["listings"] if x["address"] == "100 Sample Ave")
assert a1["rent"] == 4800 and a1["sqft"] == 1650 and a1["amenities"] == ["gym", "pool"] and a1["status"] == "new"
assert a1["fit"] is None and a1["is_new"]                              # no budget set: no fit label
assert c.post("/api/apartments/import", json={"listings": L, "digest_date": "Nov 1"}).status_code == 400

r = c.put("/api/apartments/criteria", json={"city": "Sample City", "max_rent": "5000", "stretch_rent": 5500,
                                             "min_beds": 3, "big_sqft": 1500, "move_in": "2027-06-01"}).get_json()
fits = {x["url"].split("?")[0][-1]: (x["fit"], x["big"]) for x in r["listings"]}
assert fits == {"1": ("in_budget", True), "2": ("stretch", False)}, fits

# user fields survive the next digest; a URL with a different query string is the same listing
r = c.patch(f"/api/apartments/{a1['id']}", json={"status": "saved", "notes": "Call Tuesday"}).get_json()
assert r["wishlist"] == [a1["id"]]
assert c.patch(f"/api/apartments/{a1['id']}", json={"status": "bogus"}).status_code == 400
assert c.patch("/api/apartments/missing", json={"status": "saved"}).status_code == 404
r = c.post("/api/apartments/import", json={"digest_date": "2026-11-08",
           "listings": [{"url": "https://www.example.com/a/1", "address": "100 Sample Ave", "rent": 4700},
                        {"url": "https://example.com/a/3", "address": "3 Test St", "rent": 3900}]}).get_json()
assert r["added"] == 1 and r["updated"] == 1 and r["latest_digest"] == "2026-11-08"
a1 = next(x for x in r["listings"] if x["id"] == a1["id"])
assert a1["status"] == "saved" and a1["notes"] == "Call Tuesday" and a1["rent"] == 4700 and a1["first_seen"] == "2026-11-01"
assert a1["seen_in"] == ["2026-11-01", "2026-11-08"] and not a1["is_new"]
assert [d["date"] for d in r["digests"]] == ["2026-11-08", "2026-11-01"]

a3 = next(x for x in r["listings"] if x["address"] == "3 Test St")
c.patch(f"/api/apartments/{a3['id']}", json={"status": "touring"})
r = c.put("/api/apartments/order", json={"ids": [a3["id"], a1["id"]]}).get_json()
assert r["wishlist"] == [a3["id"], a1["id"]]
r = c.delete(f"/api/apartments/{a3['id']}").get_json()
assert r["wishlist"] == [a1["id"]] and len(r["listings"]) == 2

# ---- ingest key: off unless set; wrong key refused; right key writes to the owner's data
main.DEV_SKIP_AUTH = False
hdr = {"X-Ingest-Key": "k" * 32}
assert c.post("/api/apartments/import", json={"listings": []}).status_code == 401      # no sign-in, no key
assert c.post("/api/apartments/import", json={"listings": []}, headers=hdr).status_code == 403
os.environ["APARTMENTS_INGEST_KEY"] = "k" * 32
travel.owner_uid = lambda: "owner1"
assert c.post("/api/apartments/import", json={"listings": []}, headers={"X-Ingest-Key": "x" * 32}).status_code == 403
r = c.post("/api/apartments/import", json={"digest_date": "2026-11-08", "listings": [L[0]]}, headers=hdr)
assert r.status_code == 200 and r.get_json()["added"] == 1
assert any(k.startswith("users/owner1/apartments/") for k in DATA)
assert c.get("/api/apartments", headers=hdr).status_code == 401                    # the key only opens import
main.DEV_SKIP_AUTH = True

# ---- ride receipts (made-up text in the shape of the real emails: plain text tables, and HTML)
PLAIN = """ Lyft
MARCH 6, 2026 AT 9:35 AM Bike ride total: $3.95 Card *0000 - Details inside
| |
| MARCH 6, 2026 AT 9:35 AM |
| Thanks for riding with us! | |
| Reservation ($0.15 per min for 1 min) | $0.15 |
| Free unlock | $0.00 |
| $0.15 per min (12 min) | $1.80 |
| Parking fee (out of station) | $2.00 |
| | Card *0000 Next billing date: 03/13/2026 | $3.95 |
| Member savings |
| Saved this trip | $4.75 |
| Your Trip |
| | Elm St & 3rd Ave | Start 9:35 am |
| | 12 Sample Rd, Washington, DC 20002, United States | End 9:47 am |
| Receipt # 1111222233334444 |"""
r = travel.parse_receipt(PLAIN)
assert r["date"] == "2026-03-06" and r["total"] == 3.95 and r["saved"] == 4.75 and r["receipt"] == "1111222233334444", r
assert r["start_station"] == "Elm St & 3rd Ave" and r["end_station"].startswith("12 Sample Rd") and r["minutes"] == 12
assert [i["amount"] for i in r["items"]] == [0.15, 0.0, 1.8, 2.0], r["items"]
HTML = """<html><body><table><tr><td>APRIL 2, 2026 AT 11:50 PM</td></tr><tr><td>Bike ride total: $0.00</td></tr>
<tr><td>Classic bike</td></tr><tr><td>Saved this trip</td><td>$2.30</td></tr>
<tr><td>Oak Plaza</td><td>Start 11:50 pm</td></tr><tr><td>Elm St &amp; 9th Ave</td><td>End 12:05 am</td></tr>
<tr><td>Receipt # 5555666677778888</td></tr></table></body></html>"""
r2 = travel.parse_receipt(HTML)
assert r2["minutes"] == 15 and r2["bike"] == "classic" and r2["end_station"] == "Elm St & 9th Ave" and r2["total"] == 0.0
assert travel.parse_receipt("Your weekly newsletter") is None

# scan through a fake Gmail; already-read messages are skipped
B64 = lambda t: base64.urlsafe_b64encode(t.encode()).decode()
import base64
class R:
    def __init__(s, code, js): s.status_code, s._js = code, js
    def json(s): return s._js
    def raise_for_status(s):
        if s.status_code >= 400: raise Exception(f"HTTP {s.status_code}")
MAIL = {"m1": PLAIN, "m2": HTML, "m3": "Promo, no ride here"}
gm_calls = []
def fake_get(url, headers=None, params=None, timeout=None):
    gm_calls.append(url)
    assert headers["Authorization"] == "Bearer TOK"
    if url.endswith("/messages"):
        assert "capitalbikeshare" in params["q"]
        return R(200, {"messages": [{"id": k} for k in MAIL]})
    mid = url.rsplit("/", 1)[1]
    return R(200, {"id": mid, "payload": {"mimeType": "text/html" if "<html" in MAIL[mid] else "text/plain", "body": {"data": B64(MAIL[mid])}}})
travel.requests = types.SimpleNamespace(get=fake_get)
assert c.post("/api/travel/bike/rides/scan", json={}).status_code == 400
rr = c.post("/api/travel/bike/rides/scan", json={"access_token": "TOK"}); r = rr.get_json(); assert rr.status_code == 200, r
assert r["read"] == 3 and r["added"] == 2 and r["totals"]["rides"] == 2, r["totals"]
T = r["totals"]
assert T["spent"] == 3.95 and T["saved"] == 7.05 and T["minutes"] == 27
assert T["miles_rides"] == 1 and T["miles"] > 0      # only Oak Plaza -> Elm St & 9th Ave are both docks in the feed
assert r["membership"]["price"] is None and "paid_off" not in r["membership"]     # no price entered: no verdict
rr = c.post("/api/travel/bike/rides/scan", json={"access_token": "TOK"}); r = rr.get_json(); assert rr.status_code == 200, r
assert r["read"] == 1 and r["added"] == 0 and r["totals"]["rides"] == 2           # m1, m2 skipped
def expired(url, **k): return R(401, {})
travel.requests = types.SimpleNamespace(get=expired)
assert c.post("/api/travel/bike/rides/scan", json={"access_token": "TOK"}).status_code == 401

# membership payoff
assert c.put("/api/travel/bike/membership", json={"price": -1}).status_code == 400
assert c.put("/api/travel/bike/membership", json={"price": 10, "start": "2026-05-01", "end": "2026-01-01"}).status_code == 400
M = c.put("/api/travel/bike/membership", json={"price": "10", "start": "2026-01-01", "end": "2026-12-31"}).get_json()["membership"]
assert M["saved"] == 7.05 and M["net"] == -2.95 and not M["paid_off"] and M["rides_to_go"] == 1, M
M = c.put("/api/travel/bike/membership", json={"price": 5, "start": "2026-03-01", "end": ""}).get_json()["membership"]
assert M["paid_off"] and M["net"] == 2.05

# import from a file, then forget
r = c.post("/api/travel/bike/rides/import", json={"rides": [{"date": "2026-05-01", "start_station": "A", "end_station": "B",
        "start_lat": 38.9, "start_lon": -77.0, "end_lat": 38.91, "end_lon": -77.0, "minutes": 9, "total": 0, "saved": 1.5}, {"date": "bad"}]}).get_json()
assert r["added"] == 1 and r["skipped"] == 1 and r["totals"]["rides"] == 3 and abs(next(x for x in r["rides"] if x["date"] == "2026-05-01")["miles"] - 0.69) < 0.02
assert r["top_trips"][0]["rides"] == 1 and r["by_month"]["2026-05"]["rides"] == 1
r = c.delete("/api/travel/bike/rides").get_json()
assert r["totals"]["rides"] == 0
print("travel ok")

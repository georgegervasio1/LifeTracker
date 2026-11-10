"""Offline test of apartments.py extras (photos, ratings, price history, digests, KML), Metro in travel.py, and trips.py."""
import os as _os
HERE = _os.environ.get("LT_TESTS") or _os.path.dirname(_os.path.abspath(__file__))
exec(open(_os.path.join(HERE, "test_health.py")).read().split("# ---- fake WHOOP HTTP")[0])

import apartments, travel, trips
from datetime import date, timedelta
c = main.app.test_client()

# ---- apartments: photo copies, address-only listings, ratings, price history, digest text, KML
got = []
def fake_bytes(url):
    got.append(url)
    if "broken" in url: raise Exception("404")
    return b"\xff\xd8JPEGDATA" + url.encode(), "image/jpeg"
apartments.fetch_bytes = fake_bytes
L = [{"url": "https://example.com/a/1", "address": "1 Sample St #2", "rent": 4800, "sqft": 1600, "beds": 3, "baths": 2,
      "lat": 41.92, "lon": -87.64, "pick": True, "in_zone": True,
      "photos": ["https://img.example.com/p1-cc_ft_1536.jpg", "https://img.example.com/broken.jpg", "http://insecure/x.jpg"]},
     {"address": "9 No Link Ave", "rent": 3900, "beds": 3, "private": True, "lat": 41.93, "lon": -87.65}]
r = c.post("/api/apartments/import", json={"digest_date": "2026-11-01", "listings": L,
           "digest": {"subject": "Apartment digest: week of Nov 2", "body": "Hi all, ...", "kind": "weekly"}}).get_json()
assert r["added"] == 2 and not r["skipped"], r
a1 = next(x for x in r["listings"] if x["address"] == "1 Sample St #2")
assert len(a1["photos"]) == 2 and a1["photo_copies"][0] and a1["photo_copies"][1] is None
assert got[0].endswith("p1-cc_ft_960.jpg")                     # mid-size copy
img = c.get(f"/api/apartments/photo/{a1['photo_copies'][0]}")
assert img.status_code == 200 and img.mimetype == "image/jpeg" and img.data.startswith(b"\xff\xd8")
assert r["digests"][0]["subject"].startswith("Apartment digest") and len(r["digests"][0]["listing_ids"]) == 2
n = len(got)
r = c.post("/api/apartments/import", json={"digest_date": "2026-11-08", "listings": [dict(L[0], rent=4600), dict(L[1], private=False)]}).get_json()
assert len(got) == n + 1                                         # only the failed photo is retried
a1 = next(x for x in r["listings"] if x["id"] == a1["id"])
assert a1["price_history"] == [{"date": "2026-11-01", "rent": 4800}] and a1["price_change"] == -200 and r["digests"][0]["price_drops"] == 1
a2 = next(x for x in r["listings"] if x["address"] == "9 No Link Ave")
assert a2["private"] is True                                    # a later digest can't make a private spot public
assert c.patch(f"/api/apartments/{a1['id']}", json={"rating": 7}).status_code == 400
r = c.patch(f"/api/apartments/{a1['id']}", json={"rating": 4, "status": "saved"}).get_json()
c.patch(f"/api/apartments/{a2['id']}", json={"status": "saved", "rating": 5})
kml = c.get("/api/apartments/export.kml?scope=saved")
assert kml.status_code == 200 and b"1 Sample St #2" in kml.data and b"No Link" not in kml.data and "★★★★☆" in kml.data.decode()
r = c.delete(f"/api/apartments/{a1['id']}").get_json()
assert c.get(f"/api/apartments/photo/{a1['photo_copies'][0]}").status_code == 404
assert apartments.gmaps_link({"lat": 1, "lon": 2}).endswith("query=1,2")

# ---- Metro: SmarTrip CSV, benefit, bank charges
CSV = """Card usage history
Seq.,Time,Description,Operator,Entry Location/ Bus Route,Exit Location,Product,Change (+/-),Balance
1,10/02/26 08:01 AM,Entry,Metrorail,Sample Station A,,Stored Value,$0.00,$50.00
2,10/02/26 08:20 AM,Exit,Metrorail,Sample Station A,Sample Station B,Stored Value,($2.25),$47.75
3,10/02/26 05:40 PM,Bus,Metrobus,D6,,Stored Value,-2.25,$45.50
4,10/01/26 12:00 AM,SmartBenefits Pickup,WMATA,,,SmartBenefits,$50.00,$50.00
5,11/03/26 09:00 AM,Exit,Metrorail,Sample Station B,Sample Station A,Stored Value,($6.00),$39.50
"""
assert c.post("/api/travel/metro/import", json={"csv": "a,b\n1,2"}).status_code == 400
r = c.post("/api/travel/metro/import", json={"csv": CSV}).get_json()
assert r["added"] == 5 and r["totals"]["rides"] == 3 and r["totals"]["fares"] == 10.5, r["totals"]
oct_ = r["by_month"]["2026-10"]
assert oct_["rail"] == 1 and oct_["bus"] == 1 and oct_["benefit_loaded"] == 50 and oct_["benefit"] is None   # no benefit set yet
assert r["balance"] == 39.5
assert c.post("/api/travel/metro/import", json={"csv": CSV}).get_json()["added"] == 0
assert c.put("/api/travel/metro/benefit", json={"monthly": -5}).status_code == 400
r = c.put("/api/travel/metro/benefit", json={"monthly": 50, "start": "2026-10-01", "label": "School SmartBenefits"}).get_json()
assert r["by_month"]["2026-10"]["benefit"] == 50 and r["by_month"]["2026-10"]["benefit_left"] == 45.5
assert r["by_month"]["2026-11"]["benefit_left"] == 44.0
m = date.today().isoformat()[:7]
DATA["users/dev/transactions/w1"] = {"date": f"{m}-02", "amount": 20.0, "merchant": "WMATA SMARTRIP ADD VALUE", "account": "card"}
r = c.get("/api/travel/metro").get_json()
assert r["bank_total"] == 20.0 and r["by_month"][m]["bank"] == 20.0
cm = c.get("/api/travel/commute").get_json()
assert any(x["month"] == "2026-10" and x["metro_rides"] == 2 for x in cm["months"])
assert c.delete("/api/travel/metro").get_json()["totals"]["rides"] == 0

# ---- Trips
d = lambda n: (date.today() + timedelta(days=n)).isoformat()
assert c.post("/api/trips", json={"name": ""}).status_code == 400
assert c.post("/api/trips", json={"name": "x", "start": d(5), "end": d(1)}).status_code == 400
r = c.post("/api/trips", json={"name": "Spring break", "start": d(30), "end": d(37), "places": "Lisbon; Porto", "travelers": 2}).get_json()
t = next(x for x in r["trips"] if x["id"] == r["id"])
assert t["places"] == ["Lisbon", "Porto"] and t["nights"] == 7 and t["days_until"] == 30 and t["when"] == "upcoming"
labels = {l["label"]: l["url"] for l in t["links"]}
assert "Lisbon" in labels["Google Flights"] and f"checkin={d(30)}" in labels["Airbnb"] and "adults=2" in labels["Airbnb"]
c.put("/api/trips/settings", json={"home_airport": "dca", "airlines": ["United", "American", "Nope"]})
r = c.put(f"/api/trips/{t['id']}", json={"bookings": [{"kind": "flight", "airline": "United", "flight_no": "UA 1", "date": d(30),
         "from": "DCA", "to": "LIS", "cost": "$612.40", "link": "javascript:alert(1)"}, {"kind": "stay", "title": "Apartment", "cost": 900}]}).get_json()
t = next(x for x in r["trips"] if x["id"] == t["id"])
assert r["settings"]["home_airport"] == "DCA" and [l["label"] for l in t["links"] if l.get("kind") == "airline"] == ["United", "American"]
assert "from DCA" in next(l["url"] for l in t["links"] if l["label"] == "Google Flights").replace("+", " ")
assert t["bookings"][0]["manage"].startswith("https://www.united.com") and "link" not in t["bookings"][0] and t["cost"] == 1512.4
c.post("/api/trips", json={"name": "Past", "start": d(-40), "end": d(-35)})
c.post("/api/trips", json={"name": "Someday: Japan", "places": ["Tokyo"]})
r = c.get("/api/trips").get_json()
assert [x["name"] for x in r["trips"]] == ["Spring break", "Someday: Japan", "Past"]
assert c.put(f"/api/trips/{t['id']}", json={"end": d(1)}).status_code == 400
r = c.delete(f"/api/trips/{t['id']}").get_json()
assert len(r["trips"]) == 2
# ---- Google Maps key: from the server env, never in the repo; blank when unset
os.environ.pop("GOOGLE_MAPS_KEY", None)
assert c.get("/api/maps/config").get_json() == {"google_maps_key": ""}
os.environ["GOOGLE_MAPS_KEY"] = "test-key "
assert c.get("/api/maps/config").get_json()["google_maps_key"] == "test-key"
os.environ.pop("GOOGLE_MAPS_KEY")
print("ALL TRIPS + APARTMENTS + METRO TESTS PASSED")

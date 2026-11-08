"""Offline test of subscriptions.py: recurring charges are found from the ledger alone (steady merchant, amount and
schedule), everyday spending and transfers are not, amounts are the real charges, and the user can hide one."""
import os as _os
HERE = _os.environ.get("LT_TESTS") or _os.path.dirname(_os.path.abspath(__file__))
exec(open(_os.path.join(HERE, "test_health.py")).read().split("# ---- fake WHOOP HTTP")[0])

import subscriptions as S
from datetime import date, timedelta

TODAY = date(2026, 11, 8)
tx, n = [], 0
def add(day, amount, merchant, category="Entertainment", account="card", **kw):
    global n
    n += 1
    tx.append({"id": f"t{n}", "date": day.isoformat() if hasattr(day, "isoformat") else day, "amount": amount,
               "merchant": merchant, "category": category, "account": account, **kw})

# merchant keys: reference numbers, phones, states and processor prefixes drop out
assert S.merchant_key("VIDEOCO.COM 866-555-0100 CA") == S.merchant_key("Videoco.com")
assert S.merchant_key("SQ *SOFTWARE SHOP 4412") == "SOFTWARE SHOP"
assert S.merchant_key("SHAVECLUB*AB12CD") == "SHAVECLUB"

for i in range(8):                                                    # monthly, with a price rise
    add(S._add_months(date(2026, 3, 6), i), 13.99 if i < 5 else 15.49, f"VIDEOCO.COM 866-555-01{i:02d} CA")
for i in range(10):                                                   # weekly
    add(date(2026, 9, 1) + timedelta(days=7 * i), 3.99, "DAILY PAPER DIGITAL")
for i in range(4):                                                    # stopped in July
    add(S._add_months(date(2026, 4, 2), i), 14.95, "AUDIOBOOKS INC")
add("2025-11-20", 119.00, "CODE TOOL ANNUAL"); add("2026-11-01", 119.00, "CODE TOOL ANNUAL")   # yearly, exact repeat
for i, amt in enumerate((52.10, 61.40, 70.05, 58.90)):                # a bill that varies
    add(S._add_months(date(2026, 7, 14), i), amt, "CITY POWER 4401 IL", "Bills And Utilities", "checking")
for i in range(9):                                                    # two plans at the same store
    add(S._add_months(date(2026, 2, 10), i), 0.99, "APPSTORE BILL")
    add(S._add_months(date(2026, 2, 25), i), 9.99, "APPSTORE BILL")
# not subscriptions
for i in range(12):
    add(date(2026, 8, 1) + timedelta(days=7 * i), 5.75 + (i % 3), "CORNER COFFEE", "Food And Drink")   # weekly coffee
    add(S._add_months(date(2026, 1, 3), i % 10), 400, "ONLINE TRANSFER TO SAV", "Transfer", "checking")
for i in range(6):
    add(S._add_months(date(2026, 5, 1), i), -1200, "EMPLOYER PAYROLL", "Income", "checking")         # money in
    add(S._add_months(date(2026, 5, 1), i), 900, "ZELLE PAYMENT TO A FRIEND", "Zelle", "checking")
add("2026-08-03", 60, "CONCERT TIX"); add("2026-09-03", 60, "CONCERT TIX")                       # only twice, monthly

out = S.detect(tx, TODAY)
act = {s["name"]: s for s in out["active"]}
ended = {s["name"]: s for s in out["ended"]}
assert set(act) == {"Videoco", "Daily Paper Digital", "Code Tool Annual", "City Power", "Appstore"}, sorted(act)
assert set(ended) == {"Audiobooks"}, sorted(ended)
v = act["Videoco"]
assert v["cadence"] == "monthly" and v["amount"] == 15.49 and v["previous_amount"] == 13.99 and v["charges"] == 8
assert v["next_date"] == "2026-11-06" or v["next_date"] == S._add_months(date.fromisoformat(v["last_date"]), 1).isoformat()
assert act["Daily Paper Digital"]["cadence"] == "weekly" and act["Daily Paper Digital"]["monthly"] == round(3.99 * 30.44 / 7, 2)
y = act["Code Tool Annual"]
assert y["cadence"] == "yearly" and y["next_date"] == "2027-11-01" and y["monthly"] == round(119 * 30.44 / 365.25, 2)
p = act["City Power"]
assert p["kind"] == "bill" and p["amount_kind"] == "varies" and p["amount"] == 58.90 and (p["amount_low"], p["amount_high"]) == (52.10, 70.05)
apps = [s for s in out["active"] if s["name"] == "Appstore"]
assert sorted(s["amount"] for s in apps) == [0.99, 9.99] and len({s["key"] for s in apps}) == 2
assert ended["Audiobooks"]["next_date"] is None and ended["Audiobooks"]["last_date"] == "2026-07-02"
assert out["active"][0]["monthly"] >= out["active"][-1]["monthly"]     # biggest first
# every amount shown is one of the real charges
real = {t["amount"] for t in tx}
assert all(s["amount"] in real for s in out["active"] + out["ended"])

# hiding: "not a subscription" moves it aside
out2 = S.detect(tx, TODAY, hidden={act["City Power"]["key"]})
assert "City Power" not in {s["name"] for s in out2["active"]} and [s["name"] for s in out2["hidden"]] == ["City Power"]

# ---- the API on the in-memory Firestore
c = main.app.test_client()
r = c.get("/api/fin/subscriptions").get_json()
assert r["active"] == [] and r["totals"]["count"] == 0 and r["totals"]["monthly"] == 0
today = date.today()
for i in range(5):
    d = S._add_months(today - timedelta(days=125), i)
    DATA[f"users/dev/transactions/s{i}"] = ({"date": d.isoformat(), "amount": 9.99, "merchant": "MUSICBOX 800-555-0100 NY", "category": "Entertainment", "account": "chase_card"})
r = c.get("/api/fin/subscriptions").get_json()
assert [s["name"] for s in r["active"]] == ["Musicbox"], r["active"]
s0 = r["active"][0]
assert r["totals"]["monthly"] == 9.99 and r["totals"]["yearly"] == round(9.99 * 365.25 / 30.44, 2) and s0["next_date"] > today.isoformat()
assert r["upcoming"][0]["date"] == s0["next_date"]
r = c.patch(f"/api/fin/subscriptions/{s0['key']}", json={"hidden": True}).get_json()
assert r["active"] == [] and r["hidden"][0]["key"] == s0["key"]
r = c.patch(f"/api/fin/subscriptions/{s0['key']}", json={"hidden": False}).get_json()
assert len(r["active"]) == 1
print("subscriptions ok")

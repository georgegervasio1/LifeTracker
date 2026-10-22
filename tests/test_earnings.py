"""Offline test of earnings.py: hand-checked 2026 withholding and year-end math."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
# Personal fixtures (bank CSVs, course and workout files) live outside the public repo.
PRIVATE = _os.environ.get("LT_PRIVATE", _os.path.join(HERE, "..", "..", "lifetracker-private"))
def _need(path):
    if not _os.path.exists(path):
        print(f"SKIP: private fixture {_os.path.basename(path)} not found (set LT_PRIVATE)"); raise SystemExit(0)
    return path
import json
src = open(_os.path.join(HERE, "test_health.py")).read()
exec(src.split("# ---- fake WHOOP HTTP")[0])
import earnings as E
c = main.app.test_client()
st = c.get("/api/earnings").get_json(); assert st["ready"] and st["settings"]["archived"]  # seeded summer job
c.put("/api/earnings/settings", json={"archived": False})
# example inputs only (test fixture): $225k annualized, weekly pay, Mon 2026-05-18 to Fri 2026-07-24 (10 weeks)
S = {"annual_salary": 225000, "start": "2026-05-18", "end": "2026-07-24", "frequency": "weekly"}
st = c.put("/api/earnings/settings", json=S).get_json(); assert st["ready"], st
ch = st["checks"]; first = ch[0]
print("checks:", len(ch), "first:", {k: first[k] for k in ("pay_date", "gross", "federal", "state", "ss", "medicare", "net")})
assert len(ch) == 10 and first["gross"] == 4326.92
# Pub 15-T: 4326.92*52 = 224,999.84 - 8,600 = 216,399.84 -> 41,024 + 32% * 7,124.84 = 43,303.95 / 52 = 832.77
assert first["federal"] == 832.77, first["federal"]
# IL: (4326.92 - 2925/52) * 4.95% = 211.40
assert first["state"] == 211.4, first["state"]
assert first["ss"] == 268.27 and first["medicare"] == 62.74
assert first["net"] == round(4326.92 - 832.77 - 211.40 - 268.27 - 62.74, 2)
T = st["totals"]; Y = st["year_end"]
print("totals:", T); print("federal:", {k: Y["federal"][k] for k in ("taxable", "tax", "withheld", "refund", "marginal", "effective")})
assert T["gross"] == 43269.2
# federal: 43,269.20 - 16,100 = 27,169.20 -> 1,240 + 12% * 14,769.20 = 3,012.30
assert Y["federal"]["taxable"] == 27169.2 and Y["federal"]["tax"] == 3012.3 and Y["federal"]["marginal"] == .12
assert Y["federal"]["refund"] == round(T["federal"] - 3012.3, 2)
# IL nonresident: (43,269.20 - 2,925 * 1.0) * 4.95% = 1,997.04
assert Y["il"]["tax"] == 1997.04, Y["il"]
# DC: 43,269.20 - 16,100 = 27,169.20 -> 400 + 6% * 17,169.20 = 1,430.15; credit capped at DC tax on IL share (100%) = 1,430.15
print("il:", Y["il"]); print("dc:", {k: Y["dc"][k] for k in ("taxable", "tax", "credit_other_state", "after_credit", "due")})
assert Y["dc"]["tax"] == 1430.15 and Y["dc"]["credit_other_state"] == 1430.15 and Y["dc"]["due"] == 0
# partial first week: start Wednesday -> 3/5 of a week
st2 = c.put("/api/earnings/settings", json={"start": "2026-05-20"}).get_json()
assert st2["checks"][0]["gross"] == round(225000 / 52 * 3 / 5, 2) and st2["checks"][0]["days_worked"] == 3
c.put("/api/earnings/settings", json={"start": "2026-05-18"})
# an actual stub replaces the projection for that pay date
r = c.post("/api/earnings/paychecks", json={"pay_date": "2026-05-24", "gross": 4326.92, "federal": 800, "state": 211.4, "ss": 268.27, "medicare": 62.74, "net": 2984.51}).get_json()
a0 = r["checks"][0]; assert a0["source"] == "actual" and a0["federal"] == 800 and r["totals"]["actual_checks"] == 1 and len(r["checks"]) == 10
pid = a0["actual_id"]; r = c.delete(f"/api/earnings/paychecks/{pid}").get_json(); assert r["checks"][0]["source"] == "projected"
# semimonthly schedule
st3 = c.put("/api/earnings/settings", json={"frequency": "semimonthly"}).get_json()
print("semimonthly:", [(x["pay_date"], x["gross"]) for x in st3["checks"]])
assert st3["checks"][0]["pay_date"] == "2026-05-29" and abs(st3["totals"]["gross"] - 43269) < 1500
assert c.put("/api/earnings/settings", json={"frequency": "daily"}).status_code == 400
assert c.put("/api/earnings/settings", json={"end": "2026-01-01"}).status_code == 400
print("ALL EARNINGS TESTS PASSED")

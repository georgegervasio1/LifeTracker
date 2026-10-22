"""Offline test of food.py (Health → Food): in-memory Firestore + fake USDA / Open Food Facts."""
import os as _os
HERE = _os.environ.get("LT_TESTS") or _os.path.dirname(_os.path.abspath(__file__))
exec(open(_os.path.join(HERE, "test_health.py")).read().split("# ---- fake WHOOP HTTP")[0])

import food
from datetime import date, timedelta
food.firestore = types.SimpleNamespace(SERVER_TIMESTAMP="SERVER_TS")

class R:
    def __init__(s, code, js): s.status_code, s._js = code, js
    def json(s): return s._js
    def raise_for_status(s):
        if s.status_code >= 400: raise Exception(f"HTTP {s.status_code}")
FDC = {"foods": [
    {"fdcId": 1105314, "description": "Bananas, raw", "dataType": "Foundation", "foodNutrients": [
        {"nutrientNumber": "957", "unitName": "KCAL", "value": 98.0}, {"nutrientNumber": "203", "unitName": "G", "value": 0.74},
        {"nutrientNumber": "205", "unitName": "G", "value": 23.0}, {"nutrientNumber": "204", "unitName": "G", "value": 0.29}],
     "foodMeasures": [{"disseminationText": "1 medium", "gramWeight": 118}]},
    {"fdcId": 555, "description": "BANANA CHIPS", "dataType": "Branded", "brandOwner": "ACME FOODS", "servingSize": 28, "servingSizeUnit": "g",
     "householdServingFullText": "1 oz", "foodNutrients": [{"nutrientNumber": "208", "unitName": "KCAL", "value": 536},
        {"nutrientNumber": "204", "unitName": "G", "value": 32}, {"nutrientNumber": "307", "unitName": "MG", "value": 7}]},
    {"fdcId": 9, "description": "No energy", "dataType": "Branded", "foodNutrients": []}]}
OFF = {"products": [{"code": "0123", "product_name": "Banana Bread", "brands": "Bakery Co, Other", "serving_size": "1 slice (60 g)",
                     "serving_quantity": 60, "nutriments": {"energy-kcal_100g": 326, "proteins_100g": 4.3, "carbohydrates_100g": 54,
                                                           "fat_100g": 10.5, "sodium_100g": 0.3}},
                    {"code": "0456", "product_name": "", "nutriments": {"energy-kcal_100g": 1}}]}
down = {"usda": False}
def fake_get(url, params=None, **k):
    if url == food.FDC_URL:
        if down["usda"]: raise ConnectionError("down")
        return R(200, FDC)
    if url == food.OFF_URL: return R(200, OFF)
    if url.startswith("https://world.openfoodfacts.org/api/v2/product/0123"): return R(200, {"product": OFF["products"][0]})
    return R(404, {})
food.requests = types.SimpleNamespace(get=fake_get)
c = main.app.test_client()
today = date.today().isoformat()

# 1. search: generic foods first, nutrients per 100 g, kJ ignored, foods without energy dropped
r = c.get("/api/food/search?q=banana").get_json()
names = [f["name"] for f in r["foods"]]
assert names[0] == "Bananas, raw" and "Banana chips" in names and "Banana Bread" in names and "No energy" not in names, names
ban = r["foods"][0]; chips = next(f for f in r["foods"] if f["id"] == "555"); bread = next(f for f in r["foods"] if f["source"] == "off")
assert ban["per100g"]["kcal"] == 98 and ban["servings"] == [{"label": "1 medium", "grams": 118.0}]
assert chips["brand"] == "Acme Foods" and chips["servings"][0] == {"label": "1 oz", "grams": 28.0} and chips["per100g"]["sodium_mg"] == 7
assert bread["brand"] == "Bakery Co" and bread["per100g"]["sodium_mg"] == 300 and bread["servings"][0]["grams"] == 60
down["usda"] = True
r = c.get("/api/food/search?q=banana").get_json(); assert [f["source"] for f in r["foods"]] == ["off"] and r["errors"]
down["usda"] = False
assert c.get("/api/food/search?q=b").get_json()["foods"] == []
print("search OK")

# 2. log by grams, by custom food servings, quick add; totals and meals
assert c.post("/api/food/entries", json={"date": today, "meal": "breakfast", "food": ban, "grams": 236, "servings": 2, "serving_label": "1 medium"}).status_code == 200
assert c.post("/api/food/entries", json={"date": today, "meal": "brunch", "food": ban, "grams": 10}).status_code == 400
assert c.post("/api/food/entries", json={"date": today, "meal": "lunch", "food": ban}).status_code == 400
assert c.post("/api/food/entries", json={"date": today, "meal": "lunch", "food": {"name": "x", "per100g": {}}, "grams": 10}).status_code == 400
cf = c.post("/api/food/custom", json={"name": "Protein shake", "serving_label": "1 bottle", "kcal": 160, "protein": 30, "carbs": "5", "fat": 3}).get_json()["food"]
assert c.post("/api/food/custom", json={"name": "No cals"}).status_code == 400
c.post("/api/food/entries", json={"date": today, "meal": "snacks", "food": cf, "servings": 1.5})
c.post("/api/food/entries", json={"date": today, "meal": "dinner", "quick": True, "kcal": "700", "protein": 40})
assert c.post("/api/food/entries", json={"date": today, "meal": "dinner", "quick": True}).status_code == 400
d = c.get(f"/api/food/day?date={today}").get_json()
b0 = d["meals"]["breakfast"][0]
assert b0["kcal"] == 231.3 and b0["carbs"] == 54.3 and b0["servings"] == 2
assert d["meals"]["snacks"][0]["kcal"] == 240 and d["meals"]["snacks"][0]["protein"] == 45
assert d["totals"]["kcal"] == round(231.3 + 240 + 700, 1) and d["meal_totals"]["dinner"] == 700 and d["goals"] == {}
assert "Protein shake" in [f["name"] for f in c.get("/api/food/search?q=shake").get_json()["foods"]]
print("entries OK:", d["totals"])

# 3. edit amount and meal, delete
r = c.patch(f"/api/food/entries/{today}/{b0['id']}", json={"grams": 118, "servings": 1, "meal": "lunch"}).get_json()
assert r["entry"]["kcal"] == 115.6 and r["entry"]["meal"] == "lunch"
assert c.patch(f"/api/food/entries/{today}/{b0['id']}", json={"grams": 0}).status_code == 400
assert c.patch(f"/api/food/entries/{today}/nope", json={"grams": 10}).status_code == 404
quick = c.get(f"/api/food/day?date={today}").get_json()["meals"]["dinner"][0]
c.delete(f"/api/food/entries/{today}/{quick['id']}")
assert c.get(f"/api/food/day?date={today}").get_json()["meals"]["dinner"] == []
print("edit/delete OK")

# 4. copy a meal to tomorrow, recent foods, goals, history
tmr = (date.today() + timedelta(days=1)).isoformat()
assert c.post("/api/food/copy", json={"from": today, "to": tmr, "meal": "lunch"}).get_json()["copied"] == 1
assert c.post("/api/food/copy", json={"from": today, "to": tmr, "meal": "breakfast"}).status_code == 400
t = c.get(f"/api/food/day?date={tmr}").get_json()
assert t["meals"]["lunch"][0]["kcal"] == 115.6 and t["meals"]["lunch"][0]["id"] != b0["id"]
rec = c.get("/api/food/recent").get_json()["recent"]
assert [x["food"]["name"] for x in rec] == ["Bananas, raw", "Protein shake"] or [x["food"]["name"] for x in rec] == ["Protein shake", "Bananas, raw"]
assert c.put("/api/food/goals", json={"kcal": 100}).status_code == 400
c.put("/api/food/goals", json={"kcal": 2600, "protein_g": 180, "carbs_g": "", "fat_g": 80})
assert c.get("/api/food/goals").get_json() == {"kcal": 2600, "protein_g": 180, "carbs_g": None, "fat_g": 80}
h = c.get("/api/food/history?days=7").get_json()
assert len(h["days"]) == 7 and h["days"][-1]["date"] == today and h["logged_days"] == 1 and h["average"]["kcal"] == round(115.6 + 240, 1)
assert c.delete(f"/api/food/custom/{cf['id']}").status_code == 200 and c.get("/api/food/custom").get_json()["foods"] == []
print("copy/recent/goals/history OK")

# 5. barcode
assert c.get("/api/food/barcode/0123").get_json()["food"]["name"] == "Banana Bread"
assert c.get("/api/food/barcode/999").status_code == 404 and c.get("/api/food/barcode/abc").status_code == 400
print("ALL FOOD TESTS PASSED")

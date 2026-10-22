"""Food: a calorie and macro diary (Health → Food).

Data (per user, under users/{uid}/):
  food_log/{YYYY-MM-DD}  -> {date, entries: [entry]}; an entry stores its nutrients at the time it was logged
  food_custom/{id}       -> foods the user created (per-serving nutrients)
  food/goals             -> {kcal, protein_g, carbs_g, fat_g} entered by the user (blank until set)

Food search uses USDA FoodData Central (generic and branded foods; FDC_API_KEY, falls back to the
rate-limited DEMO_KEY) and Open Food Facts (packaged foods, no key). Nutrients are normalized per 100 g.
"""
import os
import uuid
from collections import Counter
from datetime import date, timedelta

import requests
from flask import Blueprint, jsonify, request

bp = Blueprint("food", __name__)
db = firestore = require_owner = user_ref = None

MEALS = ["breakfast", "lunch", "dinner", "snacks"]
NUTRIENTS = ["kcal", "protein", "carbs", "fat", "fiber", "sugar", "sodium_mg"]
FDC_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
OFF_URL = "https://world.openfoodfacts.org/cgi/search.pl"
OFF_PRODUCT = "https://world.openfoodfacts.org/api/v2/product/{}.json"
# USDA nutrient numbers -> our keys (energy: 208 kcal; 957/958 are Atwater energy on Foundation foods)
FDC_NUM = {"208": "kcal", "957": "kcal_alt", "958": "kcal_alt", "203": "protein", "205": "carbs", "204": "fat",
           "291": "fiber", "269": "sugar", "307": "sodium_mg"}


def init(app, _db, _firestore, _require_owner, _user_ref):
    global db, firestore, require_owner, user_ref
    db, firestore, require_owner, user_ref = _db, _firestore, _require_owner, _user_ref
    _register_routes()
    app.register_blueprint(bp)


def _r(x, n=1):
    return round(x, n) if isinstance(x, (int, float)) else None


# ---------------------------------------------------------------- search sources
def from_fdc(f):
    per = {}
    for n in f.get("foodNutrients") or []:
        key = FDC_NUM.get(str(n.get("nutrientNumber") or ""))
        if key and n.get("value") is not None and (n.get("unitName") or "").upper() != "KJ":
            per[key] = float(n["value"])
    if "kcal" not in per and "kcal_alt" in per:
        per["kcal"] = per["kcal_alt"]
    per.pop("kcal_alt", None)
    if "kcal" not in per:
        return None
    servings = []
    unit = (f.get("servingSizeUnit") or "").lower()
    if f.get("servingSize") and unit in ("g", "grm", "ml", "mlt"):
        label = f.get("householdServingFullText") or f"{_r(f['servingSize'], 0):g} {'ml' if unit in ('ml', 'mlt') else 'g'}"
        servings.append({"label": label, "grams": float(f["servingSize"])})
    for m in (f.get("foodMeasures") or [])[:4]:
        if m.get("gramWeight") and m.get("disseminationText") and m["disseminationText"].lower() != "quantity not specified":
            servings.append({"label": m["disseminationText"], "grams": float(m["gramWeight"])})
    brand = f.get("brandName") or f.get("brandOwner") or ""
    name = (f.get("description") or "").strip()
    if name.isupper():                      # branded entries come in all caps
        name = name.capitalize()
    return {"source": "usda", "id": str(f.get("fdcId")), "name": name,
            "brand": brand.strip().title() if brand.isupper() else brand.strip(), "kind": f.get("dataType"), "per100g": {k: _r(v, 2) for k, v in per.items()},
            "servings": servings}


def from_off(p):
    nm = p.get("nutriments") or {}
    kcal = nm.get("energy-kcal_100g")
    if kcal is None and nm.get("energy_100g") is not None:
        kcal = float(nm["energy_100g"]) / 4.184      # energy_100g is kJ
    if kcal is None or not p.get("product_name"):
        return None
    g = lambda k: float(nm[k]) if nm.get(k) not in (None, "") else None
    per = {"kcal": float(kcal), "protein": g("proteins_100g"), "carbs": g("carbohydrates_100g"), "fat": g("fat_100g"),
           "fiber": g("fiber_100g"), "sugar": g("sugars_100g"),
           "sodium_mg": g("sodium_100g") * 1000 if g("sodium_100g") is not None else None}
    servings = []
    try:
        if p.get("serving_quantity") and float(p["serving_quantity"]) > 0:
            servings.append({"label": p.get("serving_size") or f"{p['serving_quantity']} g", "grams": float(p["serving_quantity"])})
    except (TypeError, ValueError):
        pass
    return {"source": "off", "id": str(p.get("code") or p.get("_id")), "name": p["product_name"].strip(),
            "brand": (p.get("brands") or "").split(",")[0].strip(), "kind": "Packaged",
            "per100g": {k: _r(v, 2) for k, v in per.items() if v is not None}, "servings": servings}


def search_foods(q, limit=25):
    out, errors = [], []
    try:
        r = requests.get(FDC_URL, params={"query": q, "pageSize": 20, "api_key": os.environ.get("FDC_API_KEY") or "DEMO_KEY",
                                          "dataType": "Foundation,SR Legacy,Survey (FNDDS),Branded"}, timeout=12)
        r.raise_for_status()
        out += [x for x in (from_fdc(f) for f in r.json().get("foods") or []) if x]
    except Exception as e:      # one source down shouldn't empty the results
        errors.append(f"USDA: {e.__class__.__name__}")
    try:
        r = requests.get(OFF_URL, params={"search_terms": q, "search_simple": 1, "action": "process", "json": 1, "page_size": 12,
                                          "fields": "code,product_name,brands,nutriments,serving_size,serving_quantity"},
                         headers={"User-Agent": "LifeTracker/1.0 (personal app)"}, timeout=12)
        r.raise_for_status()
        out += [x for x in (from_off(p) for p in r.json().get("products") or []) if x]
    except Exception as e:
        errors.append(f"Open Food Facts: {e.__class__.__name__}")
    # generic foods first (Foundation / SR Legacy / FNDDS), then branded, then packaged
    rank = {"Foundation": 0, "SR Legacy": 1, "Survey (FNDDS)": 1, "Branded": 2, "Packaged": 3}
    words = q.lower().split()
    out.sort(key=lambda f: (rank.get(f.get("kind"), 4) - sum(w in f["name"].lower() for w in words) * 0.5, len(f["name"])))
    return out[:limit], errors


# ---------------------------------------------------------------- entries
def nutrients_for(food, grams=None, servings=None):
    """Nutrients for an amount: grams of a per-100g food, or servings of a per-serving (custom) food."""
    if food.get("per_serving"):
        n = float(servings or 1)
        return {k: _r(v * n, 1) for k, v in food["per_serving"].items() if v is not None}
    g = float(grams)
    return {k: _r(v * g / 100, 1) for k, v in (food.get("per100g") or {}).items() if v is not None}


def _day(d):
    return user_ref().collection("food_log").document(d)


def _entries(d):
    snap = _day(d).get()
    return (snap.to_dict() or {}).get("entries", []) if snap.exists else []


def _save(d, entries):
    _day(d).set({"date": d, "entries": entries})


def totals(entries):
    t = {k: 0.0 for k in NUTRIENTS}
    for e in entries:
        for k in NUTRIENTS:
            t[k] += e.get(k) or 0
    return {k: round(v, 1) for k, v in t.items()}


def _goals():
    snap = user_ref().collection("food").document("goals").get()
    return snap.to_dict() if snap.exists else {}


def _num(v, lo, hi, name):
    if v in (None, ""):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number")
    if not lo <= v <= hi:
        raise ValueError(f"{name} must be between {lo:g} and {hi:g}")
    return v


def make_entry(b):
    meal = b.get("meal")
    if meal not in MEALS:
        raise ValueError("meal must be breakfast, lunch, dinner or snacks")
    food = b.get("food") or {}
    if b.get("quick"):                       # quick add: calories (and optional macros) only
        kcal = _num(b.get("kcal"), 0, 10000, "calories")
        if kcal is None:
            raise ValueError("calories are required")
        n = {"kcal": kcal, "protein": _num(b.get("protein"), 0, 1000, "protein"),
             "carbs": _num(b.get("carbs"), 0, 1000, "carbs"), "fat": _num(b.get("fat"), 0, 1000, "fat")}
        return {"id": uuid.uuid4().hex[:10], "meal": meal, "name": str(b.get("name") or "Quick add")[:80], "quick": True,
                **{k: v for k, v in n.items() if v is not None}}
    name = str(food.get("name") or "").strip()[:120]
    if not name:
        raise ValueError("food name is required")
    if food.get("per_serving"):
        servings = _num(b.get("servings"), 0.05, 100, "servings") or 1
        n = nutrients_for(food, servings=servings)
        amount = {"servings": servings, "serving_label": str(food.get("serving_label") or "serving")[:60]}
    else:
        if not food.get("per100g") or food["per100g"].get("kcal") is None:
            raise ValueError("food has no calorie data")
        grams = _num(b.get("grams"), 0.5, 5000, "amount")
        if grams is None:
            raise ValueError("amount in grams is required")
        n = nutrients_for(food, grams=grams)
        amount = {"grams": grams, "servings": _num(b.get("servings"), 0, 100, "servings"),
                  "serving_label": str(b.get("serving_label") or "")[:60]}
    return {"id": uuid.uuid4().hex[:10], "meal": meal, "name": name, "brand": str(food.get("brand") or "")[:80],
            "source": food.get("source"), "food_id": str(food.get("id") or "")[:40],
            "food": {k: food.get(k) for k in ("source", "id", "name", "brand", "per100g", "servings", "per_serving", "serving_label") if food.get(k) is not None},
            **amount, **n}


def _parse_date(s):
    return date.fromisoformat(str(s)).isoformat()


# ---------------------------------------------------------------- routes
def _register_routes():
    @bp.get("/api/food/day")
    @require_owner
    def day():
        try:
            d = _parse_date(request.args.get("date") or date.today().isoformat())
        except ValueError:
            return jsonify(error="date must be YYYY-MM-DD"), 400
        entries = _entries(d)
        meals = {m: [e for e in entries if e.get("meal") == m] for m in MEALS}
        return jsonify(date=d, meals=meals, totals=totals(entries),
                       meal_totals={m: totals(v)["kcal"] for m, v in meals.items()}, goals=_goals())

    @bp.post("/api/food/entries")
    @require_owner
    def add_entry():
        b = request.get_json(force=True) or {}
        try:
            d = _parse_date(b.get("date"))
            e = make_entry(b)
        except ValueError as err:
            return jsonify(error=str(err)), 400
        entries = _entries(d) + [e]
        _save(d, entries)
        return jsonify(ok=True, entry=e)

    @bp.patch("/api/food/entries/<d>/<eid>")
    @require_owner
    def edit_entry(d, eid):
        b = request.get_json(force=True) or {}
        entries = _entries(d)
        e = next((x for x in entries if x["id"] == eid), None)
        if not e:
            return jsonify(error="entry not found"), 404
        try:
            if b.get("meal"):
                if b["meal"] not in MEALS:
                    raise ValueError("bad meal")
                e["meal"] = b["meal"]
            food = e.get("food") or {}
            if "grams" in b and food.get("per100g"):
                e["grams"] = _num(b["grams"], 0.5, 5000, "amount")
                e["servings"] = _num(b.get("servings"), 0, 100, "servings")
                e.update(nutrients_for(food, grams=e["grams"]))
            elif "servings" in b and food.get("per_serving"):
                e["servings"] = _num(b["servings"], 0.05, 100, "servings")
                e.update(nutrients_for(food, servings=e["servings"]))
        except ValueError as err:
            return jsonify(error=str(err)), 400
        _save(d, entries)
        return jsonify(ok=True, entry=e)

    @bp.delete("/api/food/entries/<d>/<eid>")
    @require_owner
    def delete_entry(d, eid):
        entries = _entries(d)
        _save(d, [e for e in entries if e["id"] != eid])
        return jsonify(ok=True)

    @bp.post("/api/food/copy")
    @require_owner
    def copy_meal():
        """Copy one meal (or the whole day) from one date to another."""
        b = request.get_json(force=True) or {}
        try:
            src, dst = _parse_date(b.get("from")), _parse_date(b.get("to"))
        except ValueError:
            return jsonify(error="dates must be YYYY-MM-DD"), 400
        meal = b.get("meal")
        items = [dict(e, id=uuid.uuid4().hex[:10]) for e in _entries(src) if not meal or e.get("meal") == meal]
        if not items:
            return jsonify(error="nothing logged there to copy"), 400
        _save(dst, _entries(dst) + items)
        return jsonify(ok=True, copied=len(items))

    @bp.get("/api/food/search")
    @require_owner
    def search():
        q = (request.args.get("q") or "").strip()
        if len(q) < 2:
            return jsonify(foods=[], errors=[])
        mine = [dict(c.to_dict(), id=c.id, source="custom") for c in user_ref().collection("food_custom").stream()
                if all(w in (c.to_dict().get("name", "") + " " + c.to_dict().get("brand", "")).lower() for w in q.lower().split())]
        foods, errors = search_foods(q)
        return jsonify(foods=mine + foods, errors=errors)

    @bp.get("/api/food/barcode/<code>")
    @require_owner
    def barcode(code):
        if not code.isdigit() or len(code) > 14:
            return jsonify(error="barcode must be digits"), 400
        try:
            r = requests.get(OFF_PRODUCT.format(code), headers={"User-Agent": "LifeTracker/1.0 (personal app)"}, timeout=12)
            p = r.json().get("product") if r.status_code == 200 else None
        except Exception:
            return jsonify(error="Open Food Facts is unreachable right now"), 502
        f = from_off(p) if p else None
        return jsonify(food=f) if f else (jsonify(error="no product with nutrition data for that barcode"), 404)

    @bp.get("/api/food/recent")
    @require_owner
    def recent():
        """Foods logged in the last 30 days, most frequent first, with the last amount used."""
        start = (date.today() - timedelta(days=30)).isoformat()
        q = user_ref().collection("food_log").where("date", ">=", start)
        seen, count = {}, Counter()
        for s in sorted(q.stream(), key=lambda s: s.to_dict().get("date", "")):
            for e in s.to_dict().get("entries", []):
                if e.get("quick") or not e.get("food"):
                    continue
                k = (e["food"].get("source"), e["food"].get("id") or e["name"])
                count[k] += 1
                seen[k] = {"food": e["food"], "grams": e.get("grams"), "servings": e.get("servings"),
                           "serving_label": e.get("serving_label"), "meal": e.get("meal")}
        return jsonify(recent=[seen[k] for k, _ in count.most_common(30)])

    @bp.get("/api/food/custom")
    @require_owner
    def custom_list():
        return jsonify(foods=sorted((dict(c.to_dict(), id=c.id, source="custom") for c in user_ref().collection("food_custom").stream()),
                                    key=lambda f: f.get("name", "").lower()))

    @bp.post("/api/food/custom")
    @require_owner
    def custom_add():
        b = request.get_json(force=True) or {}
        name = str(b.get("name") or "").strip()[:120]
        try:
            if not name:
                raise ValueError("name is required")
            per = {"kcal": _num(b.get("kcal"), 0, 10000, "calories"), "protein": _num(b.get("protein"), 0, 1000, "protein"),
                   "carbs": _num(b.get("carbs"), 0, 1000, "carbs"), "fat": _num(b.get("fat"), 0, 1000, "fat"),
                   "fiber": _num(b.get("fiber"), 0, 1000, "fiber"), "sugar": _num(b.get("sugar"), 0, 1000, "sugar"),
                   "sodium_mg": _num(b.get("sodium_mg"), 0, 100000, "sodium")}
            if per["kcal"] is None:
                raise ValueError("calories per serving are required")
        except ValueError as err:
            return jsonify(error=str(err)), 400
        doc = user_ref().collection("food_custom").document()
        f = {"name": name, "brand": str(b.get("brand") or "")[:80], "serving_label": str(b.get("serving_label") or "1 serving")[:60],
             "per_serving": {k: v for k, v in per.items() if v is not None}}
        doc.set(f)
        return jsonify(ok=True, food=dict(f, id=doc.id, source="custom"))

    @bp.delete("/api/food/custom/<fid>")
    @require_owner
    def custom_delete(fid):
        user_ref().collection("food_custom").document(fid).delete()
        return jsonify(ok=True)

    @bp.get("/api/food/goals")
    @require_owner
    def goals_get():
        return jsonify(_goals())

    @bp.put("/api/food/goals")
    @require_owner
    def goals_put():
        b = request.get_json(force=True) or {}
        try:
            g = {"kcal": _num(b.get("kcal"), 500, 10000, "calorie goal"), "protein_g": _num(b.get("protein_g"), 0, 1000, "protein"),
                 "carbs_g": _num(b.get("carbs_g"), 0, 2000, "carbs"), "fat_g": _num(b.get("fat_g"), 0, 1000, "fat")}
        except ValueError as err:
            return jsonify(error=str(err)), 400
        user_ref().collection("food").document("goals").set(g)
        return jsonify(ok=True, goals=g)

    @bp.get("/api/food/history")
    @require_owner
    def history():
        try:
            days = max(7, min(int(request.args.get("days") or 30), 365))
        except ValueError:
            return jsonify(error="days must be a number"), 400
        end = date.today()
        start = end - timedelta(days=days - 1)
        logged = {s.to_dict()["date"]: totals(s.to_dict().get("entries", []))
                  for s in user_ref().collection("food_log").where("date", ">=", start.isoformat()).stream()
                  if s.to_dict().get("date", "") <= end.isoformat()}
        rows = [{"date": (start + timedelta(days=i)).isoformat(), **(logged.get((start + timedelta(days=i)).isoformat()) or {})}
                for i in range(days)]
        full = [r for r in rows if r.get("kcal")]
        avg = {k: round(sum(r[k] for r in full) / len(full), 1) for k in NUTRIENTS} if full else None
        return jsonify(days=rows, logged_days=len(full), average=avg, goals=_goals())

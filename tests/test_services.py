"""Offline test of services.py (the sign-up service picker and Settings → Your services). Made-up data only."""
import os as _os
HERE = _os.environ.get("LT_TESTS") or _os.path.dirname(_os.path.abspath(__file__))
exec(open(_os.path.join(HERE, "test_health.py")).read().split("# ---- fake WHOOP HTTP")[0])

import services
c = main.app.test_client()

# ---- pure helpers: known ids only, catalog order, Finances only for accounts that can use it
assert services.clean(["chess", "health", "nope", 3, "finances"]) == ["finances", "health", "chess"]
assert services.clean(["finances", "goals"], fin=False) == ["goals"]
assert services.clean(None) == []
# no saved choice: the owner sees every built section and skips the intro; anyone else gets the intro
assert services.state(None, fin=True) == {"picked": services.CORE, "onboarded": True}
assert services.state(None, fin=False) == {"picked": [], "onboarded": False}
assert services.state({"picked": ["finances", "golf"], "onboarded": True}, fin=False) == {"picked": ["golf"], "onboarded": True}
assert set(services.ADDONS if hasattr(services, "ADDONS") else []) <= set(services.IDS)
assert {"golf", "movies", "recipes", "chess"} <= {i for i, *_ in services.CATALOG if _[2] == "addon"}

# ---- routes (DEV_SKIP_AUTH runs as the owner)
r = c.get("/api/services").get_json()
assert r["onboarded"] and r["picked"] == services.CORE
cat = {s["id"]: s for s in r["catalog"]}
assert cat["finances"]["status"] == "live" and cat["chess"]["status"] == "in_development" and cat["golf"]["kind"] == "addon"

assert c.put("/api/services", json={"picked": []}).status_code == 400
assert c.put("/api/services", json={"picked": "health"}).status_code == 400
assert c.put("/api/services", json={"picked": ["bogus"]}).status_code == 400
r = c.put("/api/services", json={"picked": ["movies", "health", "bogus"]}).get_json()
assert r["picked"] == ["health", "movies"] and r["onboarded"]
assert DATA["users/dev/settings/services"]["picked"] == ["health", "movies"]
assert c.get("/api/services").get_json()["picked"] == ["health", "movies"]

# ---- a member (no Finances): Finances isn't offered and can't be saved
with main.app.test_request_context("/api/services"):
    from flask import g
    g.role = "member"
    assert not services.can_finance()
    assert "finances" not in {s["id"] for s in services.catalog()}
    g.role = "owner"
    assert services.can_finance()

print("services ok")

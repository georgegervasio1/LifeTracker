"""Offline security checks: every API route needs sign-in (except a short public list), the allowlist is enforced,
API answers are never cached, and ESPN private-league data is cached
per cookie set. No network."""
import os, sys, types
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
for k in ("DEV_SKIP_AUTH",):
    os.environ.pop(k, None)
os.environ.update(ALLOWED_EMAIL="owner@example.com", FRONTEND_ORIGIN="https://site.example",
                  PLAID_CLIENT_ID="x", PLAID_SECRET="y", PLAID_ENV="sandbox")

import firebase_admin
firebase_admin.initialize_app = lambda *a, **k: None
from google.cloud import firestore as gfs
gfs.Client = lambda *a, **k: types.SimpleNamespace(collection=lambda *a, **k: None)
import main  # noqa: E402
from firebase_admin import auth as fb_auth

c = main.app.test_client()

# 1. Every route under /api needs a token, except these (public on purpose, or proven by other means).
PUBLIC = {
    "/api/sports/calendar.ics",       # public team schedules only
    "/api/whoop/callback",            # one-time OAuth state proves the user
    "/api/strava/callback",           # same
    "/api/apartments/import",         # owner sign-in or the X-Ingest-Key
}
checked = 0
for rule in main.app.url_map.iter_rules():
    if not rule.rule.startswith("/api/") or rule.rule in PUBLIC:
        continue
    path = rule.rule
    for arg in rule.arguments:
        path = path.replace(f"<{arg}>", "x").replace(f"<int:{arg}>", "1").replace(f"<path:{arg}>", "x")
    import re
    path = re.sub(r"<[^>]+>", "x", path)
    for m in sorted(rule.methods - {"HEAD", "OPTIONS"}):
        r = c.open(path, method=m, json={})
        assert r.status_code == 401, f"{m} {rule.rule} answered {r.status_code} without a token"
        checked += 1
assert checked > 100, checked

# apartments import without a token or key is refused too
assert c.post("/api/apartments/import", json={"listings": []}).status_code == 401
assert c.post("/api/apartments/import", json={"listings": []}, headers={"X-Ingest-Key": "nope"}).status_code == 403

# 2. A valid Google token for someone else is refused; an unverified email is refused.
claims = {}
fb_auth.verify_id_token = lambda tok, *a, **k: dict(claims)
claims.update(uid="u2", email="someone@example.com", email_verified=True)
assert c.get("/api/fin/overview", headers={"Authorization": "Bearer t"}).status_code == 403
# A second approved account (a member, or the developer account) gets its own data but never the owner's Finances.
import access, time as _time
access._approved.update(at=_time.time() + 3600, emails={"someone@example.com"})
os.environ["ADMIN_EMAILS"] = "dev@example.com"
for who in ("someone@example.com", "dev@example.com"):
    claims.update(uid="u2", email=who, email_verified=True)
    for m, path in (("GET", "/api/fin/overview"), ("POST", "/api/plaid/link-token"), ("POST", "/api/sync"),
                    ("POST", "/api/chat"), ("GET", "/api/taxes"), ("GET", "/api/earnings"), ("POST", "/api/fin/upload")):
        r = c.open(path, method=m, json={}, headers={"Authorization": "Bearer t"})
        assert r.status_code == 403 and r.get_json()["error"] == "owner only", (who, path, r.status_code)
    with main.app.test_request_context("/api/edu"):
        assert access.gate(dict(claims)) is None and main.g.uid == "u2"      # their own Education, under their own uid
assert access.role_for("owner@example.com") == "owner" and access.role_for("dev@example.com") == "admin"
assert access.role_for("stranger@example.com") == "pending"
claims.update(uid="u3", email="stranger@example.com", email_verified=True)
assert c.get("/api/edu", headers={"Authorization": "Bearer t"}).get_json()["error"] == "pending"
assert c.get("/api/admin/users", headers={"Authorization": "Bearer t"}).status_code == 403   # not the developer
access._approved.update(at=0, emails=set())
claims.update(uid="u1", email="owner@example.com", email_verified=False)
assert c.get("/api/fin/overview", headers={"Authorization": "Bearer t"}).status_code == 403
def bad(*a, **k): raise ValueError("bad token")
fb_auth.verify_id_token = bad
assert c.get("/api/fin/overview", headers={"Authorization": "Bearer forged"}).status_code == 401

# 3. API answers are never cached and carry nosniff.
r = c.get("/api/fin/overview")
assert r.headers["Cache-Control"] == "no-store" and r.headers["X-Content-Type-Options"] == "nosniff"

# 4. CORS: only the configured site, not localhost, in production.
r = c.get("/health", headers={"Origin": "https://site.example"})
assert r.headers.get("Access-Control-Allow-Origin") == "https://site.example"
r = c.get("/health", headers={"Origin": "http://localhost:5500"})
assert "Access-Control-Allow-Origin" not in r.headers
r = c.get("/health", headers={"Origin": "https://evil.example"})
assert "Access-Control-Allow-Origin" not in r.headers

# 5. ESPN: data fetched with one account's cookies is not served to a request with other (or no) cookies.
import fantasy
calls = []
class R:
    status_code = 200
    def __init__(s, who): s.who = who
    def json(s): return {"teams": [], "who": s.who}
def fake_get(url, params=None, cookies=None, **k):
    calls.append(cookies)
    return R((cookies or {}).get("espn_s2", "public"))
fantasy.requests = types.SimpleNamespace(get=fake_get)
fantasy._cache.clear()
assert fantasy.fetch_league("1", 2026, "A" * 40, "{S1}")["who"] == "A" * 40
assert fantasy.fetch_league("1", 2026, "B" * 40, "{S2}")["who"] == "B" * 40
assert fantasy.fetch_league("1", 2026)["who"] == "public"
assert fantasy.fetch_league("1", 2026, "A" * 40, "{S1}")["who"] == "A" * 40 and len(calls) == 3

print(f"security: {checked} route/method pairs need sign-in; allowlist, caching, CORS, ESPN cache ok")

"""Who may use LifeTracker, and the developer view of sign-ups.

Roles (from the verified Google email on the Firebase ID token):
  owner    ALLOWED_EMAIL. Everything, including Finances (Plaid, bank data, taxes, the finance chat).
  admin    ADMIN_EMAILS (comma-separated env). The developer account: sees and approves sign-ups at /api/admin/*,
           and has its own ordinary (empty) data like any member. No Finances.
  member   An email the owner or an admin approved (Firestore app/access {emails}), or anyone when SIGNUP_OPEN=1.
           Every section except Finances, each with their own data under users/{uid}.
  pending  Anyone else with a verified Google account: recorded in signups/{uid} so the developer page can approve
           them, and refused (403 "pending") everywhere except /api/me.

Each person's data lives under users/{their uid}; nothing here reads another user's data. The sign-up log holds
only email, display name, first/last seen and status.
"""
import os
import time
from flask import g, jsonify, request

real_db = None
OWNER_ONLY = ("/api/plaid", "/api/sync", "/api/fin", "/api/transactions", "/api/summary", "/api/holdings",
              "/api/venmo", "/api/import/csv", "/api/earnings", "/api/taxes", "/api/chat", "/api/demo/schedule")
_approved = {"at": 0.0, "emails": set()}
_seen = {}                  # uid -> last time we wrote last_seen (throttles writes; keyed by uid)
SEEN_EVERY = 600


def owner_email():
    return os.environ.get("ALLOWED_EMAIL", "").strip().lower()


def admin_emails():
    return {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}


def _access_doc():
    return real_db.collection("app").document("access")


def approved_emails(fresh=False):
    if fresh or time.time() - _approved["at"] > 60:
        try:
            snap = _access_doc().get()
            emails = set((snap.to_dict() or {}).get("emails") or []) if snap.exists else set()
        except Exception:
            emails = _approved["emails"]          # keep the last good list if Firestore hiccups
        _approved.update(at=time.time(), emails={e.lower() for e in emails})
    return _approved["emails"]


def role_for(email):
    email = (email or "").lower()
    if not email:
        return "pending"
    if email == owner_email():
        return "owner"
    if email in admin_emails():
        return "admin"
    if os.environ.get("SIGNUP_OPEN") == "1" or email in approved_emails():
        return "member"
    return "pending"


def owner_only(path):
    return any(path == p or path.startswith(p + "/") for p in OWNER_ONLY)


def record(claims, role):
    """Keep the sign-up log current (first sign-in creates the row; later ones touch last_seen every 10 min)."""
    uid = claims.get("uid")
    if not uid or real_db is None or time.time() - _seen.get(uid, 0) < SEEN_EVERY:
        return
    _seen[uid] = time.time()
    try:
        ref = real_db.collection("signups").document(uid)
        snap = ref.get()
        row = {"email": (claims.get("email") or "").lower(), "name": claims.get("name") or "",
               "status": role, "last_seen": time.time()}
        if not snap.exists:
            row["first_seen"] = row["last_seen"]
        ref.set(row, merge=True)
    except Exception:
        pass                                   # the log must never block a sign-in


def gate(claims):
    """None if the request may go on (g.uid/g.email/g.role set), else the error response."""
    if not claims.get("email_verified"):
        return jsonify(error="forbidden"), 403
    role = role_for(claims.get("email"))
    record(claims, role)
    g.uid, g.email, g.role = claims["uid"], (claims.get("email") or "").lower(), role
    if role == "pending":
        return jsonify(error="pending"), 403
    if role != "owner" and owner_only(request.path):
        return jsonify(error="owner only"), 403
    return None


def init(app, _real_db, verify):
    """verify(header) -> claims dict or an error response (signature check only, no allowlist)."""
    global real_db
    real_db = _real_db

    def signed_in():
        header = request.headers.get("Authorization", "")
        if header.startswith("Demo "):
            return None, (jsonify(error="the demo has no account"), 403)
        claims = verify(header)
        if not isinstance(claims, dict):
            return None, claims
        if not claims.get("email_verified"):
            return None, (jsonify(error="forbidden"), 403)
        return claims, None

    @app.get("/api/me")
    def me():
        """Who am I and what can I use. Answers for pending accounts too, so the site can say 'waiting for access'."""
        claims, err = signed_in()
        if err:
            return err
        role = role_for(claims.get("email"))
        record(claims, role)
        return jsonify(email=(claims.get("email") or "").lower(), role=role, finances=role == "owner",
                       admin=role in ("owner", "admin"))

    def admin_claims():
        claims, err = signed_in()
        if err:
            return None, err
        if role_for(claims.get("email")) not in ("owner", "admin"):
            return None, (jsonify(error="forbidden"), 403)
        return claims, None

    @app.get("/api/admin/users")
    def admin_users():
        """Developer view: everyone who has signed in, and the approved list. No personal data beyond the account."""
        _, err = admin_claims()
        if err:
            return err
        rows = []
        for d in real_db.collection("signups").stream():
            r = d.to_dict() or {}
            r["role"] = role_for(r.get("email"))
            rows.append({k: r.get(k) for k in ("email", "name", "role", "first_seen", "last_seen")})
        approved = approved_emails(fresh=True)
        known = {r["email"] for r in rows}
        rows += [{"email": e, "name": "", "role": role_for(e), "first_seen": None, "last_seen": None}
                 for e in sorted(approved - known)]
        rows.sort(key=lambda r: -(r.get("last_seen") or 0))
        return jsonify(users=rows, signup_open=os.environ.get("SIGNUP_OPEN") == "1",
                       counts={k: sum(1 for r in rows if r["role"] == k) for k in ("owner", "admin", "member", "pending")})

    @app.post("/api/admin/access")
    def admin_access():
        """{email, approve: true|false}: add to or remove from the approved list."""
        _, err = admin_claims()
        if err:
            return err
        b = request.get_json(force=True) or {}
        email = (b.get("email") or "").strip().lower()
        if "@" not in email or len(email) > 200:
            return jsonify(error="enter an email address"), 400
        if email == owner_email() or email in admin_emails():
            return jsonify(error="the owner and developer accounts are set on the server"), 400
        emails = approved_emails(fresh=True)
        emails = (emails | {email}) if b.get("approve", True) else (emails - {email})
        _access_doc().set({"emails": sorted(emails), "updated": time.time()})
        _approved.update(at=time.time(), emails=set(emails))
        return admin_users()

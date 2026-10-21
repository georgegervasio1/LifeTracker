"""LifeTracker API — Flask on Cloud Run.

Endpoints (all require a Firebase ID token for ALLOWED_EMAIL):
  POST /api/plaid/link-token        -> create a Plaid Link token
  POST /api/plaid/exchange          -> exchange public_token, store the Item
  POST /api/sync                    -> pull new transactions + investment holdings
  POST /api/venmo/upload            -> import a monthly Venmo CSV statement
  GET  /api/transactions            -> normalized transactions (?start=&end=)
  GET  /api/summary                 -> spending by category + monthly totals
  GET  /api/holdings                -> latest investment holdings
"""
import csv
import hashlib
import io
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, g, jsonify, request
from flask_cors import CORS

load_dotenv()

app = Flask(__name__)
CORS(app, origins=[os.environ.get("FRONTEND_ORIGIN", "http://localhost:5500"),
                   "http://localhost:5500", "http://127.0.0.1:5500"])

ALLOWED_EMAIL = os.environ.get("ALLOWED_EMAIL", "").lower()
DEV_SKIP_AUTH = os.environ.get("DEV_SKIP_AUTH") == "1"

# ---------------------------------------------------------------- Firebase / Firestore
import firebase_admin
from firebase_admin import auth as fb_auth
from google.cloud import firestore

if not firebase_admin._apps:
    firebase_admin.initialize_app()
db = firestore.Client()


def require_owner(fn):
    """Reject any request that isn't from the one allowed Google account."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if DEV_SKIP_AUTH:
            g.uid = "dev"
            return fn(*args, **kwargs)
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify(error="missing token"), 401
        try:
            claims = fb_auth.verify_id_token(header.removeprefix("Bearer "))
        except Exception:
            return jsonify(error="invalid token"), 401
        if not claims.get("email_verified") or claims.get("email", "").lower() != ALLOWED_EMAIL:
            return jsonify(error="forbidden"), 403
        g.uid = claims["uid"]
        return fn(*args, **kwargs)
    return wrapper


def user_ref():
    return db.collection("users").document(g.uid)


# ---------------------------------------------------------------- Plaid
import plaid
from plaid.api import plaid_api
from plaid.model.country_code import CountryCode
from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.transactions_sync_request import TransactionsSyncRequest

_PLAID_HOSTS = {"sandbox": plaid.Environment.Sandbox,
                "production": plaid.Environment.Production}
plaid_client = plaid_api.PlaidApi(plaid.ApiClient(plaid.Configuration(
    host=_PLAID_HOSTS[os.environ.get("PLAID_ENV", "sandbox")],
    api_key={"clientId": os.environ.get("PLAID_CLIENT_ID", ""),
             "secret": os.environ.get("PLAID_SECRET", "")},
)))


@app.post("/api/plaid/link-token")
@require_owner
def create_link_token():
    # kind=bank -> Chase (transactions); kind=investments -> UBS
    kind = (request.get_json(silent=True) or {}).get("kind", "bank")
    products = [Products("investments")] if kind == "investments" else [Products("transactions")]
    req = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id=g.uid),
        client_name="LifeTracker",
        products=products,
        country_codes=[CountryCode("US")],
        language="en",
    )
    return jsonify(link_token=plaid_client.link_token_create(req)["link_token"])


@app.post("/api/plaid/exchange")
@require_owner
def exchange_public_token():
    body = request.get_json(force=True)
    resp = plaid_client.item_public_token_exchange(
        ItemPublicTokenExchangeRequest(public_token=body["public_token"]))
    # NOTE: access tokens are long-lived credentials. Firestore is encrypted at rest and
    # locked to this service account; consider moving them to Secret Manager later.
    user_ref().collection("items").document(resp["item_id"]).set({
        "access_token": resp["access_token"],
        "institution": body.get("institution", "Unknown"),
        "kind": body.get("kind", "bank"),
        "cursor": None,
        "created": firestore.SERVER_TIMESTAMP,
    })
    return jsonify(ok=True, item_id=resp["item_id"])


def _sync_transactions(item_doc):
    item = item_doc.to_dict()
    cursor, added, removed = item.get("cursor"), 0, 0
    tx_col = user_ref().collection("transactions")
    while True:
        args = {"access_token": item["access_token"]}
        if cursor:
            args["cursor"] = cursor
        resp = plaid_client.transactions_sync(TransactionsSyncRequest(**args)).to_dict()
        batch = db.batch()
        for t in resp["added"] + resp["modified"]:
            pfc = t.get("personal_finance_category") or {}
            batch.set(tx_col.document(t["transaction_id"]), {
                "source": item["institution"],
                "account_id": t["account_id"],
                "date": str(t["date"]),
                # Plaid: positive = money out. We store spending as positive too.
                "amount": float(t["amount"]),
                "merchant": t.get("merchant_name") or t.get("name"),
                "category": (pfc.get("primary") or "OTHER").replace("_", " ").title(),
                "detail": pfc.get("detailed"),
                "pending": t.get("pending", False),
            })
            added += 1
        for t in resp["removed"]:
            batch.delete(tx_col.document(t["transaction_id"]))
            removed += 1
        batch.commit()
        cursor = resp["next_cursor"]
        if not resp["has_more"]:
            break
    item_doc.reference.update({"cursor": cursor, "last_sync": firestore.SERVER_TIMESTAMP})
    return added, removed


def _sync_holdings(item_doc):
    item = item_doc.to_dict()
    resp = plaid_client.investments_holdings_get(
        InvestmentsHoldingsGetRequest(access_token=item["access_token"])).to_dict()
    securities = {s["security_id"]: s for s in resp["securities"]}
    holdings = [{
        "account_id": h["account_id"],
        "ticker": securities.get(h["security_id"], {}).get("ticker_symbol"),
        "name": securities.get(h["security_id"], {}).get("name"),
        "quantity": float(h["quantity"]),
        "value": float(h.get("institution_value") or 0),
        "cost_basis": float(h["cost_basis"]) if h.get("cost_basis") is not None else None,
    } for h in resp["holdings"]]
    snap = {"date": date.today().isoformat(), "holdings": holdings,
            "total": round(sum(h["value"] for h in holdings), 2)}
    # One snapshot per day -> lets you chart portfolio value over time.
    user_ref().collection("holdings").document(snap["date"]).set(snap)
    return len(holdings)


@app.post("/api/sync")
@require_owner
def sync_all():
    results = []
    for item_doc in user_ref().collection("items").stream():
        kind = item_doc.get("kind")
        try:
            if kind == "investments":
                results.append({"item": item_doc.id, "holdings": _sync_holdings(item_doc)})
            else:
                a, r = _sync_transactions(item_doc)
                results.append({"item": item_doc.id, "added": a, "removed": r})
        except plaid.ApiException as e:
            results.append({"item": item_doc.id, "error": str(e.body)[:300]})
    return jsonify(results=results)


# ---------------------------------------------------------------- Venmo CSV import
_money = re.compile(r"[^\d.\-]")


def _parse_venmo_amount(raw):
    """'- $12.50' -> 12.50 spent ; '+ $40.00' -> -40.00 (money in)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    val = float(_money.sub("", raw.replace(" ", "")) or 0)
    return -val  # Venmo shows outflows negative; flip to Plaid's convention


@app.post("/api/venmo/upload")
@require_owner
def venmo_upload():
    f = request.files.get("file")
    if not f:
        return jsonify(error="attach a CSV as 'file'"), 400
    lines = f.read().decode("utf-8-sig").splitlines()
    # Venmo statements have preamble rows; find the real header row.
    start = next((i for i, l in enumerate(lines) if "Datetime" in l and "Amount" in l), None)
    if start is None:
        return jsonify(error="couldn't find Venmo header row"), 400
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    tx_col, count, batch = user_ref().collection("transactions"), 0, db.batch()
    for row in reader:
        amt_key = next((k for k in row if k and k.startswith("Amount (total)")), None)
        amount = _parse_venmo_amount(row.get(amt_key)) if amt_key else None
        if amount is None or not row.get("Datetime") or row.get("Status") not in ("Complete", "Issued"):
            continue
        # Deterministic id so re-uploading the same month doesn't duplicate.
        tid = "venmo_" + (row.get("ID") or hashlib.sha1(str(row).encode()).hexdigest()[:16])
        counterparty = row.get("To") if amount > 0 else row.get("From")
        batch.set(tx_col.document(tid), {
            "source": "Venmo",
            "account_id": "venmo",
            "date": row["Datetime"][:10],
            "amount": amount,
            "merchant": counterparty or "Venmo",
            "note": row.get("Note"),
            "category": "Venmo Transfer" if row.get("Type") == "Standard Transfer" else "Venmo",
            "pending": False,
        })
        count += 1
    batch.commit()
    return jsonify(imported=count)


# ---------------------------------------------------------------- Read endpoints
def _load_transactions(start, end):
    q = (user_ref().collection("transactions")
         .where("date", ">=", start).where("date", "<=", end))
    return [doc.to_dict() | {"id": doc.id} for doc in q.stream()]


def _date_range():
    end = request.args.get("end") or date.today().isoformat()
    start = request.args.get("start") or (date.today() - timedelta(days=90)).isoformat()
    return start, end


# Transfers between your own accounts shouldn't count as "spending".
EXCLUDE_FROM_SPENDING = {"Transfer In", "Transfer Out", "Loan Payments", "Venmo Transfer", "Income"}


@app.get("/api/transactions")
@require_owner
def list_transactions():
    txs = _load_transactions(*_date_range())
    txs.sort(key=lambda t: t["date"], reverse=True)
    return jsonify(transactions=txs)


@app.get("/api/summary")
@require_owner
def summary():
    start, end = _date_range()
    by_cat, by_month, by_source = defaultdict(float), defaultdict(float), defaultdict(float)
    income = spend = 0.0
    for t in _load_transactions(start, end):
        if t["category"] == "Income" or t["amount"] < 0:
            income += -t["amount"] if t["amount"] < 0 else 0
            continue
        if t["category"] in EXCLUDE_FROM_SPENDING:
            continue
        spend += t["amount"]
        by_cat[t["category"]] += t["amount"]
        by_month[t["date"][:7]] += t["amount"]
        by_source[t["source"]] += t["amount"]
    rnd = lambda d: {k: round(v, 2) for k, v in sorted(d.items(), key=lambda kv: -kv[1])}
    return jsonify(start=start, end=end, total_spent=round(spend, 2), total_in=round(income, 2),
                   by_category=rnd(by_cat), by_source=rnd(by_source),
                   by_month=dict(sorted({k: round(v, 2) for k, v in by_month.items()}.items())))


@app.get("/api/holdings")
@require_owner
def holdings():
    snaps = (user_ref().collection("holdings")
             .order_by("date", direction=firestore.Query.DESCENDING).limit(180).stream())
    snaps = [s.to_dict() for s in snaps]
    return jsonify(latest=snaps[0] if snaps else None,
                   history=[{"date": s["date"], "total": s["total"]} for s in reversed(snaps)])


@app.get("/healthz")
def health():
    return jsonify(ok=True, time=datetime.utcnow().isoformat())


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 8080)), debug=True)

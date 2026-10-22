"""LifeTracker API — Flask on Cloud Run.

Endpoints (all require a Firebase ID token for ALLOWED_EMAIL):
  POST /api/plaid/link-token        -> create a Plaid Link token
  POST /api/plaid/exchange          -> exchange public_token, store the Item
  POST /api/sync                    -> pull new transactions + investment holdings
  POST /api/venmo/upload            -> import a monthly Venmo CSV statement
  POST /api/import/csv              -> import bank/card CSV (Chase or LifeTracker format)
  GET  /api/transactions            -> normalized transactions (?start=&end=)
  GET  /api/summary                 -> spending by category + monthly totals
  GET  /api/holdings                -> latest investment holdings
  POST /api/chat                    -> ask the AI assistant (Claude) about your finances
  Health: /api/health/summary, /api/workouts, /api/whoop/{connect,callback,sync}  (see health.py)
  Education: /api/edu, /api/edu/courses, /api/edu/profile, /api/edu/import  (see education.py)
  Goals: /api/goals, /api/goals/library, /api/goals/metrics, /api/goals/coach  (see goals.py)
  Syllabi: /api/edu/syllabi/{import,upload}, /api/edu/syllabi/<id>[/file]  (see syllabus.py)
  My Day: /api/edu/today, /api/edu/tasks, /api/edu/plan, /api/edu/gmail/scan  (see edutasks.py)
  Finances (ledger): /api/fin/{overview,cash,investments,income,taxes,upload,imports,reconcile,...}  (see finance.py)
  Training: /api/training, /api/training/{template,state,sessions,history,exercise,import}  (see training.py)
  Running: /api/running[/<id>], /api/runs, /api/strava/{connect,callback,sync}  (see strava.py)
  Race training: /api/raceplans[/<id>], /api/raceplans/preview  (see raceplan.py)
  Food: /api/food/{day,entries,copy,search,barcode,recent,custom,goals,history}  (see food.py)
"""
import csv
import json
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

from categorize import TRANSFER_CATEGORIES, categorize

load_dotenv()

app = Flask(__name__)
app.json.sort_keys = False  # keep categories ordered largest-first
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
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
from plaid.model.investments_transactions_get_request import InvestmentsTransactionsGetRequest
from plaid.model.investments_transactions_get_request_options import InvestmentsTransactionsGetRequestOptions
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


PLAID_TYPES = {("depository", "checking"): "checking", ("depository", "savings"): "savings", ("depository", "money market"): "savings",
               ("depository", "cd"): "savings", ("credit", None): "credit_card", ("loan", None): "loan",
               ("investment", "roth"): "roth_ira", ("investment", "roth 401k"): "roth_401k", ("investment", "401k"): "401k",
               ("investment", "ira"): "traditional_ira", ("investment", "rollover"): "rollover_ira", ("investment", None): "brokerage"}


def _plaid_accounts(item_doc):
    """Map this item's Plaid accounts to canonical accounts and record today's balances."""
    item = item_doc.to_dict()
    resp = plaid_client.accounts_get(AccountsGetRequest(access_token=item["access_token"])).to_dict()
    out = {}
    for a in resp["accounts"]:
        typ, sub = str(a.get("type") or ""), str(a.get("subtype") or "")
        kind = PLAID_TYPES.get((typ, sub)) or PLAID_TYPES.get((typ, None)) or "other"
        canon = ledger.resolve_account(item.get("institution") or "Bank", kind, mask=a.get("mask"),
                                       name=a.get("official_name") or a.get("name"), alias="plaid:" + a["account_id"])
        ledger.map_plaid_account(a["account_id"], canon)
        bal = (a.get("balances") or {}).get("current")
        if bal is not None:
            ledger.set_balance(canon, date.today().isoformat(), float(bal), src="plaid")
        out[a["account_id"]] = canon
    return out


def _sync_transactions(item_doc):
    item = item_doc.to_dict()
    cursor, added, removed = item.get("cursor"), 0, 0
    tx_col = user_ref().collection("transactions")
    amap = _plaid_accounts(item_doc)
    touched = {}
    while True:
        args = {"access_token": item["access_token"]}
        if cursor:
            args["cursor"] = cursor
        resp = plaid_client.transactions_sync(TransactionsSyncRequest(**args)).to_dict()
        batch = db.batch()
        for t in resp["added"] + resp["modified"]:
            pfc = t.get("personal_finance_category") or {}
            canon = amap.get(t["account_id"], "plaid:" + t["account_id"])
            lo, hi = touched.get(canon, (str(t["date"]), str(t["date"])))
            touched[canon] = (min(lo, str(t["date"])), max(hi, str(t["date"])))
            batch.set(tx_col.document(t["transaction_id"]), {
                "source": item["institution"],
                "account_id": t["account_id"], "account": canon, "src": "plaid",
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
    for canon, (lo, hi) in touched.items():          # a Plaid copy outranks the same purchase from a CSV
        ledger.dedupe_transactions(canon, lo, hi)
    return added, removed


PLAID_INV_TYPES = {"buy": "buy", "sell": "sell", "fee": "fee", "dividend": "dividend", "interest": "interest",
                   "deposit": "deposit", "contribution": "contribution", "withdrawal": "withdrawal", "distribution": "distribution"}


def _sync_holdings(item_doc):
    """Holdings become one snapshot per canonical account per day (tax lots are added from the latest
    statement when the share counts match); investment transactions go into the shared activity ledger."""
    item = item_doc.to_dict()
    amap = _plaid_accounts(item_doc)
    resp = plaid_client.investments_holdings_get(
        InvestmentsHoldingsGetRequest(access_token=item["access_token"])).to_dict()
    securities = {s["security_id"]: s for s in resp["securities"]}
    today = date.today().isoformat()
    by_acct = defaultdict(list)
    cash = defaultdict(float)
    for h in resp["holdings"]:
        sec = securities.get(h["security_id"], {})
        canon = amap.get(h["account_id"])
        if not canon:
            continue
        if sec.get("is_cash_equivalent") or sec.get("type") == "cash":
            cash[canon] += float(h.get("institution_value") or 0)
            continue
        by_acct[canon].append({"symbol": sec.get("ticker_symbol"), "name": sec.get("name"), "quantity": float(h["quantity"]),
                               "price": float(h.get("institution_price") or 0), "value": float(h.get("institution_value") or 0),
                               "cost_basis": float(h["cost_basis"]) if h.get("cost_basis") is not None else None,
                               "unrealized": (float(h.get("institution_value") or 0) - float(h["cost_basis"])) if h.get("cost_basis") is not None else None})
    for canon in set(by_acct) | set(cash):
        hs = by_acct.get(canon, [])
        total = round(sum(x["value"] for x in hs) + cash.get(canon, 0), 2)
        user_ref().collection("inv_snapshots").document(f"{canon}_{today}_plaid").set(
            {"account": canon, "date": today, "src": "plaid", "holdings": hs, "cash": round(cash.get(canon, 0), 2), "total": total})
        ledger.set_balance(canon, today, total, src="plaid")
    # investment transactions (up to 24 months)
    start = date.today() - timedelta(days=730)
    offset, n = 0, 0
    while True:
        r = plaid_client.investments_transactions_get(InvestmentsTransactionsGetRequest(
            access_token=item["access_token"], start_date=start, end_date=date.today(),
            options=InvestmentsTransactionsGetRequestOptions(offset=offset, count=500))).to_dict()
        secs = {s["security_id"]: s for s in r.get("securities", [])}
        batch = db.batch()
        for t in r["investment_transactions"]:
            canon = amap.get(t["account_id"])
            if not canon:
                continue
            sub, typ = str(t.get("subtype") or ""), str(t.get("type") or "")
            kind = PLAID_INV_TYPES.get(sub) or PLAID_INV_TYPES.get(typ) or ("transfer_in" if float(t["amount"]) < 0 else "transfer_out")
            desc = t.get("name") or ""
            if kind in ("deposit", "transfer_in"):
                kind = ledger.classify_inflow(desc)
            batch.set(user_ref().collection("inv_activity").document("plaid_" + t["investment_transaction_id"]), {
                "account": canon, "date": str(t["date"]), "type": kind, "description": desc,
                "symbol": (secs.get(t.get("security_id")) or {}).get("ticker_symbol"),
                "quantity": abs(float(t.get("quantity") or 0)) or None, "price": float(t.get("price") or 0) or None,
                "amount": -float(t["amount"]), "src": "plaid", "pending": False}, merge=True)
            n += 1
        batch.commit()
        offset += len(r["investment_transactions"])
        if offset >= r.get("total_investment_transactions", 0) or not r["investment_transactions"]:
            break
    for canon in set(amap.values()):
        ledger.dedupe_activity(canon, start.isoformat(), today)
    ledger.pair_transfers()
    return sum(len(v) for v in by_acct.values())


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
    dates = []
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
            "account_id": "venmo", "account": "venmo", "src": "venmo_csv",
            "date": row["Datetime"][:10],
            "amount": amount,
            "merchant": counterparty or "Venmo",
            "note": row.get("Note"),
            "category": "Venmo Transfer" if row.get("Type") == "Standard Transfer" else "Venmo",
            "pending": False,
        })
        count += 1
        dates.append(row["Datetime"][:10])
    batch.commit()
    if dates:
        ledger.dedupe_transactions("venmo", min(dates), max(dates))
    return jsonify(imported=count)


# ---------------------------------------------------------------- Bank CSV import
def _parse_amount(raw):
    raw = (raw or "").strip().replace("$", "").replace(",", "")
    neg = raw.startswith("(") and raw.endswith(")")
    val = float(raw.strip("()") or 0)
    return -val if neg else val


def _parse_date(raw):
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"unrecognized date: {raw!r}")


def parse_bank_csv(raw_text, account_label=None):
    """Accepts three layouts and returns normalized rows (amount: + = money out):
    - LifeTracker:   date, description, amount[, category, account]
    - Chase card:    Transaction Date, Post Date, Description, Category, Type, Amount, Memo
    - Chase checking: Details, Posting Date, Description, Amount, Type, Balance, Check or Slip #"""
    reader = csv.DictReader(io.StringIO(raw_text))
    cols = {c.strip().lower(): c for c in (reader.fieldnames or [])}
    if {"date", "description", "amount"} <= cols.keys():
        layout = "lifetracker"
    elif {"transaction date", "description", "amount"} <= cols.keys():
        layout = "chase_card"
    elif {"posting date", "description", "amount"} <= cols.keys():
        layout = "chase_checking"
    else:
        raise ValueError("Unrecognized CSV columns: " + ", ".join(reader.fieldnames or []))
    get = lambda row, name: (row.get(cols.get(name, ""), "") or "").strip()

    rows = []
    for row in reader:
        desc = re.sub(r"\s+", " ", get(row, "description"))
        if not desc or not get(row, "amount"):
            continue
        if layout == "lifetracker":
            date_, amt = _parse_date(get(row, "date")), _parse_amount(get(row, "amount"))
            cat = get(row, "category") or categorize(desc, amt)
            acct = get(row, "account") or account_label or "Imported"
        else:
            date_ = _parse_date(get(row, "transaction date") or get(row, "posting date"))
            amt = -_parse_amount(get(row, "amount"))      # Chase: purchases/debits are negative
            is_card = layout == "chase_card"
            cat = categorize(desc, amt, is_card)
            if cat == "Other" and get(row, "category"):
                cat = get(row, "category").replace("&", "And")
            acct = account_label or ("Chase Credit Card" if is_card else "Chase Checking")
        rows.append({"date": date_, "description": desc, "amount": round(amt, 2),
                     "category": cat, "account": acct})
    return layout, rows


@app.post("/api/import/csv")
@require_owner
def import_csv():
    f = request.files.get("file")
    if not f:
        return jsonify(error="attach a CSV as 'file'"), 400
    try:
        layout, rows = parse_bank_csv(f.read().decode("utf-8-sig"), request.form.get("account") or None)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    # Deterministic ids: re-importing the same rows never duplicates them. The occurrence
    # counter keeps genuinely repeated same-day charges (e.g. two identical coffees) distinct.
    seen = defaultdict(int)
    canon_for, spans = {}, {}
    ledger.ensure_defaults()
    tx_col, batch, n = user_ref().collection("transactions"), db.batch(), 0
    for r in rows:
        key = f"{r['account']}|{r['date']}|{r['description']}|{r['amount']:.2f}"
        seen[key] += 1
        tid = "imp_" + hashlib.sha1(f"{key}|{seen[key]}".encode()).hexdigest()[:20]
        canon = canon_for.setdefault(r["account"], ledger.account_for_label(r["account"]))
        span = spans.get(canon, (r["date"], r["date"]))
        spans[canon] = (min(span[0], r["date"]), max(span[1], r["date"]))
        batch.set(tx_col.document(tid), {
            "source": r["account"], "account_id": "import:" + r["account"], "account": canon,
            "src": "chase_csv" if layout.startswith("chase") else "csv", "date": r["date"],
            "amount": r["amount"], "merchant": r["description"], "category": r["category"],
            "pending": False, "imported": True,
        })
        n += 1
        if n % 400 == 0:                       # Firestore batches max out at 500 writes
            batch.commit(); batch = db.batch()
    batch.commit()
    for canon, (lo, hi) in spans.items():         # a CSV row that Plaid already has is kept but not counted
        ledger.dedupe_transactions(canon, lo, hi)
    dates = sorted(r["date"] for r in rows)
    return jsonify(imported=n, layout=layout,
                   first_date=dates[0] if dates else None, last_date=dates[-1] if dates else None)


# ---------------------------------------------------------------- Read endpoints
def _load_transactions(start, end, account=None):
    """The cash ledger for a date range: one row per real transaction (cross-source copies are skipped)."""
    return ledger.load_transactions(start, end, account=account)


def _date_range():
    end = request.args.get("end") or date.today().isoformat()
    start = request.args.get("start") or (date.today() - timedelta(days=90)).isoformat()
    return start, end


@app.get("/api/transactions")
@require_owner
def list_transactions():
    txs = _load_transactions(*_date_range())
    txs.sort(key=lambda t: t["date"], reverse=True)
    return jsonify(transactions=txs)


@app.get("/api/summary")
@require_owner
def summary():
    return jsonify(compute_summary(*_date_range()))


def compute_summary(start, end, account=None):
    by_cat, by_month, by_source = defaultdict(float), defaultdict(float), defaultdict(float)
    income = spend = 0.0
    for t in _load_transactions(start, end, account):
        cat, amt = t.get("category"), t["amount"]
        if cat in TRANSFER_CATEGORIES or t.get("transfer_pair"):   # moving money between your own accounts
            continue
        if cat == "Refund":                     # refunds reduce spending, not income
            spend += amt
            by_month[t["date"][:7]] += amt
            by_source[t["source"]] += amt
            continue
        if amt < 0:                             # money in
            income += -amt
            continue
        if cat == "Income":
            continue
        spend += amt
        by_cat[t["category"]] += t["amount"]
        by_month[t["date"][:7]] += t["amount"]
        by_source[t["source"]] += t["amount"]
    rnd = lambda d: {k: round(v, 2) for k, v in sorted(d.items(), key=lambda kv: -kv[1])}
    return dict(start=start, end=end, total_spent=round(spend, 2), total_in=round(income, 2),
                by_category=rnd(by_cat), by_source=rnd(by_source),
                by_month=dict(sorted({k: round(v, 2) for k, v in by_month.items()}.items())))


def load_holdings():
    """Latest holdings across investment accounts + value history (statements and Plaid snapshots)."""
    import portfolio
    h = portfolio.holdings()
    latest = {"holdings": [{"ticker": x["symbol"], "name": x["name"], "quantity": x["quantity"], "value": x["value"],
                            "cost_basis": x["cost_basis"]} for x in h["holdings"]], "total": h["total"]} if h["holdings"] or h["cash"] else None
    return dict(latest=latest, history=[{"date": p["date"], "total": p["value"]} for p in portfolio.value_series()])


@app.get("/api/holdings")
@require_owner
def holdings():
    return jsonify(load_holdings())


# ---------------------------------------------------------------- Health (workouts + WHOOP)
import health
health.init(app, db, firestore, require_owner, user_ref)

# ---------------------------------------------------------------- Education (grades, audit, planner)
import education
education.init(app, db, firestore, require_owner, user_ref)
import syllabus
syllabus.init(app, db, firestore, require_owner, user_ref)
import edutasks
edutasks.init(app, db, firestore, require_owner, user_ref)

import ledger
import finance
finance.init(app, db, firestore, require_owner, user_ref)

# ---------------------------------------------------------------- Training (planner, sessions, history)
import training
training.init(app, db, firestore, require_owner, user_ref)

# ---------------------------------------------------------------- Running (Strava) and race training plans
import strava
strava.init(app, db, firestore, require_owner, user_ref)
import raceplan
raceplan.init(app, db, firestore, require_owner, user_ref)

# ---------------------------------------------------------------- Goals (SMART goals linked to the other sections)
import goals
goals.init(app, db, firestore, require_owner, user_ref)

# ---------------------------------------------------------------- Food diary (calories and macros)
import food
food.init(app, db, firestore, require_owner, user_ref)

# ---------------------------------------------------------------- Earnings (paychecks, withholding, year-end tax)
import earnings
earnings.init(app, db, firestore, require_owner, user_ref)


# ---------------------------------------------------------------- AI assistant
import assistant


def _acct(account):
    """Accept a canonical id or a type ('brokerage', 'roth_ira', 'credit_card') from the assistant."""
    if not account:
        return None
    rows = ledger.accounts(include_hidden=True)
    if any(a["id"] == account for a in rows):
        return account
    hit = next((a for a in rows if a["type"] == account), None)
    return hit["id"] if hit else account


def tool_search_transactions(start=None, end=None, text=None, category=None, source=None,
                             min_amount=None, max_amount=None, limit=50, account=None):
    start = start or (date.today() - timedelta(days=365)).isoformat()
    end = end or date.today().isoformat()
    rows = _load_transactions(start, end, _acct(account))
    t, c, s = (text or "").lower(), (category or "").lower(), (source or "").lower()
    out = [r for r in rows
           if (not t or t in f"{r.get('merchant')} {r.get('note')} {r.get('category')}".lower())
           and (not c or c in str(r.get("category", "")).lower())
           and (not s or s in str(r.get("source", "")).lower())
           and (min_amount is None or r["amount"] >= min_amount)
           and (max_amount is None or r["amount"] <= max_amount)]
    out.sort(key=lambda r: r["date"], reverse=True)
    keep = ("date", "merchant", "amount", "category", "source", "account", "src", "note", "pending")
    return {"match_count": len(out),
            "total_amount": round(sum(r["amount"] for r in out), 2),
            "transactions": [{k: r.get(k) for k in keep} for r in out[:min(int(limit or 50), 200)]]}


def assistant_context():
    items = [d.to_dict() for d in user_ref().collection("items").stream()]
    linked = ", ".join(f"{i.get('institution')} ({i.get('kind')})" for i in items) or "nothing linked through Plaid yet"
    accts = ", ".join(f"{a['name']} [{a['id']}, {a['type_label']}]" for a in ledger.accounts())
    return (f"Accounts: {accts}. Plaid: {linked}. UBS data comes from uploaded statement PDFs until Plaid links it; "
            f"Venmo from CSV uploads; Chase from CSV uploads or Plaid. Records from several sources are matched so each real "
            f"transaction counts once.")


def _tool_investments(account=None):
    import finance
    v = finance.investments_view(_acct(account))
    v.pop("accounts", None); v.pop("settings", None)
    for h in v["holdings"]["holdings"]:
        h.pop("accounts", None)
    return v


def _tool_activity(account=None, start=None, end=None, type=None):
    rows = ledger.load_activity(start or "1900-01-01", end or "2999-12-31", account=_acct(account))
    if type:
        rows = [r for r in rows if r["type"] == type]
    return {"count": len(rows), "total": round(sum(r["amount"] for r in rows), 2),
            "items": [{k: r.get(k) for k in ("date", "account", "type", "description", "symbol", "quantity", "price", "amount", "pending", "src")} for r in rows[-200:]]}


def _tool_data_quality():
    import finance
    v = finance.imports_view()
    return {"coverage": v["coverage"], "reconciliation": [{k: r[k] for k in ("account_name", "period", "closing_date", "ok", "checks")} for r in v["reconciliation"]],
            "duplicates": v["duplicates"]}


@app.post("/api/chat")
@require_owner
def chat():
    body = request.get_json(force=True) or {}
    import finance, fintax, portfolio
    ledger.ensure_defaults(); ledger.migrate()
    tools_impl = {
        "get_spending_summary": lambda start, end, account=None: compute_summary(start, end, _acct(account)),
        "search_transactions": tool_search_transactions,
        "get_accounts_and_net_worth": portfolio.net_worth,
        "get_investments": _tool_investments,
        "get_investment_activity": _tool_activity,
        "get_income": lambda year=None: finance.income_view(int(year or date.today().year)),
        "get_tax_estimate": lambda year=None: fintax.compute(int(year or date.today().year)),
        "get_data_quality": _tool_data_quality,
    }
    place = str(body.get("place") or "")[:200] or None
    try:
        reply, used = assistant.run_chat(body.get("messages", []), tools_impl, assistant_context(), place)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(reply=reply, tools_used=used)


# ---------------------------------------------------------------- Errors as JSON
@app.errorhandler(Exception)
def handle_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify(error=e.description), e.code
    app.logger.exception("Unhandled error")
    if isinstance(e, plaid.ApiException):
        try:
            detail = json.loads(e.body)
            return jsonify(error=f"Plaid: {detail.get('error_code')} — {detail.get('error_message')}"), 502
        except Exception:
            pass
    if e.__class__.__module__.startswith("anthropic"):
        return jsonify(error=f"AI assistant error: {e.__class__.__name__}"), 502
    return jsonify(error=f"Server error: {e.__class__.__name__}"), 500


@app.get("/health")
def health():
    return jsonify(ok=True, time=datetime.utcnow().isoformat())


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 8080)), debug=True)

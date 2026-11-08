"""Recurring automatic charges (subscriptions and bills), found in the ledger. Nothing is entered by hand and no
merchant list lives in code: a charge counts when the same merchant bills a steady amount on a steady schedule.

  GET   /api/fin/subscriptions                 active + possibly ended, totals, hidden
  PATCH /api/fin/subscriptions/<key>           {"hidden": true|false}  (the user says "not a subscription")

`detect` is pure (transactions in, list out) so it can be tested without Firestore.
Amounts are money out (positive), as in the ledger. Every amount shown is a real charge; the monthly figure is the
real charge spread over its period (an annual $120 plan = $10 a month) and is labeled as such in the UI.
"""
import re
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from flask import Blueprint, jsonify, request

import ledger as L

bp = Blueprint("subscriptions", __name__)
LOOKBACK_DAYS = 430            # long enough to see an annual plan charge twice

# name, typical days between charges, allowed window, months to add for the next date (None = add days), grace days
CADENCES = [
    ("weekly", 7, (5, 9), None, 5),
    ("every 2 weeks", 14, (12, 16), None, 7),
    ("monthly", 30.44, (26, 35), 1, 12),
    ("every 3 months", 91.3, (80, 101), 3, 25),
    ("every 6 months", 182.6, (165, 200), 6, 35),
    ("yearly", 365.25, (345, 385), 12, 45),
]
# never a subscription: money between your own accounts, cash, people, income
SKIP_CATEGORIES = set(L.TRANSFER_CATEGORIES) | {"Income", "Other Income", "Refund", "Zelle", "Venmo", "Cash App",
                                                 "Cash And ATM", "Taxes And Government"}
# everyday spending: only counts when the amount is identical every time (a fixed plan), never "about the same"
EVERYDAY = {"Food And Drink", "Groceries", "Transportation", "Shopping", "Travel", "Personal Care", "Medical"}
BILL_CATEGORIES = {"Bills And Utilities", "Insurance", "Rent", "Loan"}
_STATES = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
           "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR",
           "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY"}
_NOISE = {"PPD", "ID", "WEB", "ACH", "DEBIT", "CREDIT", "PURCHASE", "RECURRING", "POS", "CARD", "INC", "LLC", "CO", "THE",
          "PAYMENT", "PMT", "BILL", "AUTOPAY", "WWW", "COM", "HTTPS", "HELP", "USA", "US"}


def r2(x):
    return None if x is None else round(float(x) + 0.0, 2)


def merchant_key(desc):
    """Stable name for a merchant across charges: drops reference numbers, phone numbers, the state, processor
    prefixes ("SQ *", "TST*") and filler words. 'NETFLIX.COM 866-579-7172 CA' and 'Netflix.com' give the same key."""
    s = (desc or "").upper()
    s = re.sub(r"^(SQ|TST|SP|PY|PP|GOOGLE|APPLE PAY|PAYPAL)\s?\*\s*", "", s)
    s = re.sub(r"[^A-Z0-9&]+", " ", s)
    words = [w for w in s.split() if not re.search(r"\d", w) and w not in _NOISE]
    while len(words) > 1 and words[-1] in _STATES:
        words.pop()
    words = words[:3]
    while len(words) > 1 and words[-1] in ("AND", "OF", "&", "FOR"):
        words.pop()
    return " ".join(words)


def _pretty(key):
    return " ".join(w.capitalize() for w in key.split()) or "Unknown"


def _add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def _cadence(gaps):
    """The schedule the gaps fit, or None. Allows one missed or doubled-up gap when there are enough of them."""
    if not gaps:
        return None
    med = median(gaps)
    for c in CADENCES:
        lo, hi = c[2]
        if lo <= med <= hi:
            fits = sum(lo <= g <= hi for g in gaps)
            if fits == len(gaps) or (len(gaps) >= 4 and fits >= len(gaps) - 1):
                return c
    return None


def _steady(amounts, everyday, bill=False):
    lo, hi = min(amounts), max(amounts)
    if lo <= 0:
        return None
    if hi - lo < 0.005:
        return "fixed"
    if everyday:
        return None
    if hi <= lo * (2.5 if bill else 1.6):    # a bill that moves (power by season, phone with taxes): labeled "varies"
        return "varies" if (hi - lo) > max(1.0, 0.03 * hi) else "fixed"
    return None


def _clusters(rows):
    """Split one merchant's charges into separate plans by amount (two app-store plans at $0.99 and $9.99).
    A price change on one plan stays one plan: amounts within 25% chain together."""
    out = []
    for t in sorted(rows, key=lambda t: t["amount"]):
        if out and t["amount"] <= out[-1][-1]["amount"] * 1.25 + 0.5:
            out[-1].append(t)
        else:
            out.append([t])
    return [sorted(c, key=lambda t: t["date"]) for c in out]


def _evaluate(key, rows, today):
    rows = sorted(rows, key=lambda t: t["date"])
    days = sorted({t["date"][:10] for t in rows})
    if len(days) < len(rows):          # two charges the same day: keep one per day (the larger), or it's not a plan
        by_day = {}
        for t in rows:
            d = t["date"][:10]
            if d not in by_day or t["amount"] > by_day[d]["amount"]:
                by_day[d] = t
        if len(by_day) < len(rows) * 0.7:
            return None
        rows = [by_day[d] for d in sorted(by_day)]
    dates = [date.fromisoformat(t["date"][:10]) for t in rows]
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    cad = _cadence(gaps)
    if not cad:
        return None
    name, period, _win, months, grace = cad
    amounts = [t["amount"] for t in rows]
    cats = [t.get("category") for t in rows if t.get("category")]
    category = max(set(cats), key=cats.count) if cats else None
    everyday = category in EVERYDAY
    # how sure: a monthly/weekly plan needs 3 charges; a quarterly or longer one 2, but then only an exact repeat
    need = 3 if period < 60 else 2
    if len(rows) < need:
        return None
    amount = amounts[-1]
    run = next((i for i in range(len(amounts) - 1, -1, -1) if abs(amounts[i] - amount) >= 0.005), -1) + 1
    prev = None
    if run == 0:
        steady = "fixed"
    elif len(amounts) - run >= 2 and len({round(a, 2) for a in amounts[:run]}) == 1 and _steady(amounts, False):
        # a price change: one price before, a new one at least twice since
        steady, prev = "fixed", amounts[run - 1]
    else:
        steady = _steady(amounts[-6:], everyday, category in BILL_CATEGORIES)
    if not steady or (period >= 60 and len(rows) == 2 and steady != "fixed"):
        return None
    last = dates[-1]
    nxt = _add_months(last, months) if months else last + timedelta(days=round(period))
    status = "active" if today <= nxt + timedelta(days=grace) else "ended"
    typical = amount if steady == "fixed" else sum(amounts[-6:]) / len(amounts[-6:])
    accounts = [t.get("account") for t in rows if t.get("account")]
    return {
        "key": key, "name": _pretty(key), "merchant": rows[-1].get("merchant"), "category": category,
        "kind": "bill" if category in BILL_CATEGORIES else "subscription",
        "cadence": name, "period_days": period, "amount": r2(amount), "amount_kind": steady,
        "amount_low": r2(min(amounts[-6:])), "amount_high": r2(max(amounts[-6:])),
        "previous_amount": r2(prev), "price_changed": rows[run]["date"][:10] if prev is not None else None,
        # spread over the period; a bill that varies uses the average of its recent charges (labeled in the UI)
        "monthly": r2(typical * 30.44 / period), "yearly": r2(typical * 365.25 / period),
        "last_date": last.isoformat(), "next_date": nxt.isoformat() if status == "active" else None,
        "first_date": dates[0].isoformat(), "charges": len(rows),
        "account": max(set(accounts), key=accounts.count) if accounts else None, "status": status,
        "history": [{"date": t["date"][:10], "amount": r2(t["amount"]), "id": t.get("id")} for t in rows[-12:]],
    }


def _eligible(t):
    a = t.get("amount") or 0
    return (a > 0 and not t.get("transfer_pair") and not t.get("pending") and t.get("category") not in SKIP_CATEGORIES
            and not re.search(r"ZELLE|VENMO|TRANSFER|ATM|CASH APP", (t.get("merchant") or "").upper()))


def detect(transactions, today=None, hidden=()):
    """transactions: ledger rows. Returns {"active": [...], "ended": [...], "hidden": [...]} sorted by monthly cost."""
    today = today or date.today()
    groups = defaultdict(list)
    for t in transactions:
        if _eligible(t):
            k = merchant_key(t.get("merchant"))
            if k:
                groups[k].append(t)
    found = []
    for k, rows in groups.items():
        whole = _evaluate(k, rows, today)
        if whole:
            found.append(whole)
            continue
        parts = _clusters(rows)
        if len(parts) > 1:
            for c in parts:
                hit = _evaluate(k, c, today)
                if hit:
                    hit["key"] = f"{k}#{round(c[0]['amount'])}"     # the plan's starting price keeps the key stable
                    found.append(hit)
    hidden = set(hidden or ())
    shown = [s for s in found if s["key"] not in hidden]
    order = lambda s: (-(s["monthly"] or 0), s["name"])
    return {"active": sorted([s for s in shown if s["status"] == "active"], key=order),
            "ended": sorted([s for s in shown if s["status"] == "ended"], key=lambda s: s["last_date"], reverse=True),
            "hidden": sorted([s for s in found if s["key"] in hidden], key=order)}


def summary(today=None):
    today = today or date.today()
    start = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    tx = L.load_transactions(start, today.isoformat())
    hidden = (L.col("fin_settings").document("subscriptions").get().to_dict() or {}).get("hidden") or []
    out = detect(tx, today, hidden)
    act = out["active"]
    soon = sorted((s for s in act if s["next_date"]), key=lambda s: s["next_date"])
    names = {a["id"]: a["name"] for a in L.accounts(include_hidden=True)}
    for s in act + out["ended"] + out["hidden"]:
        s["account_name"] = names.get(s["account"])
    span = None
    if tx:
        span = {"start": min(t["date"][:10] for t in tx), "end": max(t["date"][:10] for t in tx)}
    return out | {
        "totals": {"count": len(act), "monthly": r2(sum(s["monthly"] or 0 for s in act)),
                   "yearly": r2(sum(s["yearly"] or 0 for s in act)),
                   "subscriptions_monthly": r2(sum(s["monthly"] or 0 for s in act if s["kind"] == "subscription")),
                   "bills_monthly": r2(sum(s["monthly"] or 0 for s in act if s["kind"] == "bill")),
                   "next_30_days": r2(sum(s["amount"] for s in soon if s["next_date"] <= (today + timedelta(days=30)).isoformat()))},
        "upcoming": [{"key": s["key"], "name": s["name"], "date": s["next_date"], "amount": s["amount"], "amount_kind": s["amount_kind"]} for s in soon[:6]],
        "data_span": span, "today": today.isoformat(),
    }


def init(app, _db, _firestore, require_owner, _user_ref):
    if "subscriptions" in app.blueprints:
        return

    @bp.get("/api/fin/subscriptions")
    @require_owner
    def subs():
        return jsonify(summary())

    @bp.patch("/api/fin/subscriptions/<path:key>")
    @require_owner
    def hide(key):
        b = request.get_json(force=True) or {}
        ref = L.col("fin_settings").document("subscriptions")
        cur = set((ref.get().to_dict() or {}).get("hidden") or [])
        (cur.add if b.get("hidden") else cur.discard)(key[:120])
        ref.set({"hidden": sorted(cur)}, merge=True)
        return jsonify(summary())

    app.register_blueprint(bp)

"""The finance ledger: one canonical record per real account and per real transaction, whatever the source.

Sources: Plaid, UBS statement PDFs, Chase CSV, Venmo CSV, manual entry. Each record keeps `src`
(plaid | ubs_pdf | chase_csv | venmo_csv | csv | manual) and `account` (a canonical account id).

No double counting:
  * Deterministic ids: re-importing the same file rewrites the same documents.
  * Cross-source matching: the same account, the same amount, dates within 3 days (and for trades the same
    type, symbol and quantity) is one real event. The lower-ranked copy is kept but marked `superseded_by`
    the higher-ranked one (Plaid > statement PDF > CSV > manual), and every read skips superseded records.
    Matching is one-to-one, and two records from the same source are never merged (two identical coffees
    on one day are two coffees).
  * Holdings are point-in-time snapshots per account; the latest one wins, they are never added together.
  * Transfers between your own accounts are paired on both sides and kept out of spending and income.

Data (per user, under users/{uid}/):
  fin_accounts/{id}     canonical accounts
  transactions/{id}     cash ledger (Plaid, CSV, Venmo, manual)       amount: + money out, - money in
  inv_activity/{id}     investment-account activity                   amount: + money into the account
  inv_snapshots/{id}    holdings (with tax lots when the source has them) per account per date
  inv_statements/{id}   a statement's own totals per account per period (for reconciliation + returns)
  fin_balances/{id}     account balances per date (statement, Plaid, manual)
  tax_docs/{id}         parsed tax forms (1099-DIV/INT/B/R, 5498)
"""
import hashlib
import re
from datetime import date, datetime, timedelta, timezone

db = firestore = user_ref = None

SOURCE_RANK = {"plaid": 5, "ubs_pdf": 4, "chase_csv": 3, "venmo_csv": 3, "csv": 3, "manual": 1}
MATCH_DAYS = 3
TRANSFER_CATEGORIES = {"Transfer", "Credit Card Payment", "Transfer In", "Transfer Out", "Loan Payments", "Venmo Transfer"}

# type -> how it's grouped on the dashboard
ACCOUNT_KINDS = {
    "checking": {"group": "cash", "label": "Checking"},
    "savings": {"group": "cash", "label": "Savings"},
    "credit_card": {"group": "credit", "label": "Credit card", "liability": True},
    "p2p": {"group": "cash", "label": "Venmo / Zelle"},
    "brokerage": {"group": "investment", "label": "Brokerage", "tax": "taxable"},
    "roth_ira": {"group": "retirement", "label": "Roth IRA", "tax": "roth"},
    "rollover_ira": {"group": "retirement", "label": "Rollover IRA", "tax": "pre_tax"},
    "traditional_ira": {"group": "retirement", "label": "Traditional IRA", "tax": "pre_tax"},
    "401k": {"group": "retirement", "label": "401(k)", "tax": "pre_tax"},
    "roth_401k": {"group": "retirement", "label": "Roth 401(k)", "tax": "roth"},
    "loan": {"group": "loan", "label": "Loan", "liability": True},
    "other": {"group": "cash", "label": "Other"},
}
DEFAULT_ACCOUNTS = [
    {"id": "chase_checking", "name": "Chase Checking", "institution": "Chase", "type": "checking", "aliases": ["import:Chase Checking"]},
    {"id": "chase_savings", "name": "Chase Savings", "institution": "Chase", "type": "savings", "aliases": ["import:Chase Savings"]},
    {"id": "chase_card", "name": "Chase Credit Card", "institution": "Chase", "type": "credit_card", "aliases": ["import:Chase Credit Card"]},
    {"id": "venmo", "name": "Venmo", "institution": "Venmo", "type": "p2p", "aliases": ["venmo"]},
]
TYPE_NAMES = {"brokerage": "Brokerage", "roth_ira": "Roth IRA", "rollover_ira": "Rollover IRA", "traditional_ira": "Traditional IRA"}


def init(_db, _firestore, _user_ref):
    global db, firestore, user_ref
    db, firestore, user_ref = _db, _firestore, _user_ref


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def col(name):
    return user_ref().collection(name)


def r2(x):
    return None if x is None else round(float(x) + 0.0, 2)


def _d(s):
    return date.fromisoformat(s[:10])


def norm_desc(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())[:60]


def make_id(prefix, *parts):
    return prefix + "_" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


# ---------------------------------------------------------------- canonical accounts
def accounts(include_hidden=False):
    rows = [d.to_dict() | {"id": d.id} for d in col("fin_accounts").stream()]
    if not rows:
        ensure_defaults()
        rows = [d.to_dict() | {"id": d.id} for d in col("fin_accounts").stream()]
    for a in rows:
        kind = ACCOUNT_KINDS.get(a.get("type"), ACCOUNT_KINDS["other"])
        a.setdefault("group", kind["group"])
        a.setdefault("tax", kind.get("tax"))
        a["liability"] = bool(a.get("liability", kind.get("liability", False)))
        a["type_label"] = kind["label"]
    rows = [a for a in rows if include_hidden or not a.get("hidden")]
    order = ["cash", "credit", "investment", "retirement", "loan"]
    rows.sort(key=lambda a: (order.index(a["group"]) if a["group"] in order else 9, a.get("name", "")))
    return rows


def ensure_defaults():
    for a in DEFAULT_ACCOUNTS:
        ref = col("fin_accounts").document(a["id"])
        if not ref.get().exists:
            ref.set({k: v for k, v in a.items() if k != "id"} | {"created": now_iso()})


def resolve_account(institution, acct_type, mask=None, name=None, alias=None, note=None):
    """Find the canonical account for a source's account, or create it. Order: an alias already linked,
    then same institution + last 4, then the one account of that institution + type with no last 4 yet."""
    rows = accounts(include_hidden=True)
    inst = (institution or "").lower()
    if alias:
        hit = next((a for a in rows if alias in (a.get("aliases") or [])), None)
        if hit:
            return hit["id"]
    if mask:
        hit = next((a for a in rows if (a.get("institution") or "").lower() == inst and a.get("mask") == mask), None)
        if hit:
            _add_alias(hit, alias)
            return hit["id"]
    same = [a for a in rows if (a.get("institution") or "").lower() == inst and a.get("type") == acct_type and not a.get("mask")]
    if len(same) == 1:
        upd = {"mask": mask} if mask else {}
        col("fin_accounts").document(same[0]["id"]).set(upd, merge=True)
        _add_alias(same[0], alias)
        return same[0]["id"]
    aid = re.sub(r"[^a-z0-9]+", "_", f"{inst}_{acct_type}_{mask or len(rows)}").strip("_")
    col("fin_accounts").document(aid).set({
        "name": name or f"{institution} {TYPE_NAMES.get(acct_type, ACCOUNT_KINDS.get(acct_type, {}).get('label', acct_type))}",
        "institution": institution, "type": acct_type, "mask": mask, "aliases": [alias] if alias else [],
        "note": note, "created": now_iso()}, merge=True)
    return aid


def _add_alias(acct, alias):
    if alias and alias not in (acct.get("aliases") or []):
        col("fin_accounts").document(acct["id"]).set({"aliases": (acct.get("aliases") or []) + [alias]}, merge=True)


def account_for_label(label):
    """CSV imports carry a free-text account label ('Chase Credit Card'); map it to a canonical account."""
    alias = "import:" + label
    rows = accounts(include_hidden=True)
    hit = next((a for a in rows if alias in (a.get("aliases") or []) or a.get("name", "").lower() == label.lower()), None)
    if hit:
        return hit["id"]
    low = label.lower()
    typ = "credit_card" if any(w in low for w in ("card", "sapphire", "freedom", "credit")) else \
          "savings" if "saving" in low else "checking" if "checking" in low else "other"
    return resolve_account("Chase" if "chase" in low else label.split()[0], typ, name=label, alias=alias)


# ---------------------------------------------------------------- reading the cash ledger
def load_transactions(start, end, account=None, include_superseded=False):
    q = col("transactions").where("date", ">=", start).where("date", "<=", end)
    out = []
    for d in q.stream():
        t = d.to_dict() | {"id": d.id}
        if t.get("superseded_by") and not include_superseded:
            continue
        if account and t.get("account") != account:
            continue
        out.append(t)
    return out


def load_activity(start="1900-01-01", end="2999-12-31", account=None, include_superseded=False):
    out = []
    for d in col("inv_activity").where("date", ">=", start).where("date", "<=", end).stream():
        a = d.to_dict() | {"id": d.id}
        if a.get("superseded_by") and not include_superseded:
            continue
        if account and a.get("account") != account:
            continue
        out.append(a)
    out.sort(key=lambda a: (a["date"], a["id"]))
    return out


# ---------------------------------------------------------------- de-duplication
def _shift(s, days):
    return (_d(s) + timedelta(days=days)).isoformat()


def dedupe(collection, account, start, end, key=lambda r: (round(r.get("amount") or 0, 2),)):
    """Mark cross-source copies of the same real event as superseded. Returns how many links changed."""
    docs = [d.to_dict() | {"id": d.id} for d in col(collection)
            .where("date", ">=", _shift(start, -MATCH_DAYS)).where("date", "<=", _shift(end, MATCH_DAYS)).stream()]
    docs = [r for r in docs if r.get("account") == account]
    ids = {r["id"] for r in docs}
    want = {}                                      # id -> superseded_by (or None)
    for r in docs:                                 # links to records that no longer exist are dropped
        if r.get("superseded_by") and r["superseded_by"] not in ids:
            want[r["id"]] = None
    pool = sorted(docs, key=lambda r: (-SOURCE_RANK.get(r.get("src"), 0), r["date"], r["id"]))
    matched = set()
    for hi in pool:
        if hi["id"] in matched or hi.get("pending_match_lock"):
            continue
        rank_hi = SOURCE_RANK.get(hi.get("src"), 0)
        best = None
        for lo in pool:
            if lo["id"] == hi["id"] or lo["id"] in matched or lo.get("src") == hi.get("src"):
                continue
            if SOURCE_RANK.get(lo.get("src"), 0) >= rank_hi or key(lo) != key(hi):
                continue
            gap = abs((_d(lo["date"]) - _d(hi["date"])).days)
            if gap <= MATCH_DAYS and (best is None or gap < best[0]):
                best = (gap, lo)
        if best:
            lo = best[1]
            matched.update({hi["id"], lo["id"]})
            want[lo["id"]] = hi["id"]
    # anything previously superseded that no longer has a partner becomes active again
    for r in docs:
        if r.get("superseded_by") and r["id"] not in want and r["id"] not in matched:
            want[r["id"]] = None
    changed, batch, n = 0, db.batch(), 0
    for r in docs:
        if r["id"] in want and r.get("superseded_by") != want[r["id"]]:
            batch.set(col(collection).document(r["id"]), {"superseded_by": want[r["id"]]}, merge=True)
            changed += 1; n += 1
            if n % 400 == 0:
                batch.commit(); batch = db.batch()
    batch.commit()
    return changed


def dedupe_transactions(account, start, end):
    return dedupe("transactions", account, start, end)


def dedupe_activity(account, start, end):
    def k(r):
        t = r.get("type")
        if t in ("buy", "sell"):
            return (t, r.get("symbol"), round(abs(r.get("quantity") or 0), 4), round(abs(r.get("amount") or 0), 2))
        return (t if t not in ("deposit", "transfer_in", "trust_distribution", "contribution") else "in",
                round(abs(r.get("amount") or 0), 2))
    return dedupe("inv_activity", account, start, end, key=k)


def dedupe_all():
    """Re-run matching over every account and the whole date range (used after migrations / big imports)."""
    out = 0
    for a in accounts(include_hidden=True):
        out += dedupe_transactions(a["id"], "2000-01-01", "2999-12-31")
        out += dedupe_activity(a["id"], "2000-01-01", "2999-12-31")
    return out


def duplicate_report():
    tx = [d.to_dict() | {"id": d.id} for d in col("transactions").stream()]
    act = [d.to_dict() | {"id": d.id} for d in col("inv_activity").stream()]
    sup = [r for r in tx + act if r.get("superseded_by")]
    by_acct = {}
    for r in sup:
        by_acct.setdefault(r.get("account") or "unassigned", 0)
        by_acct[r.get("account") or "unassigned"] += 1
    return {"records": len(tx) + len(act), "superseded": len(sup), "by_account": by_acct,
            "examples": [{"id": r["id"], "date": r["date"], "amount": r.get("amount"), "src": r.get("src"),
                          "description": r.get("merchant") or r.get("description"), "kept": r["superseded_by"]} for r in sup[:20]]}


# ---------------------------------------------------------------- transfers between your own accounts
CHASE_IN = re.compile(r"JPMORGAN CHASE|CHASE BANK|\bCHASE\b", re.I)
TRUST_IN = re.compile(r"\bUA\b|\bU/A\b|TRUST|GRNDCH|GRANDCHILD|ESTATE|INHERIT", re.I)


def classify_inflow(desc, own_numbers=()):
    """An investment-account deposit: from one of your own accounts (transfer) or new money (trust etc.)."""
    d = desc or ""
    for n in own_numbers:
        if n and n.replace(" ", "") in d.replace(" ", ""):
            return "transfer_in"
    if TRUST_IN.search(d):
        return "trust_distribution"
    if CHASE_IN.search(d):
        return "transfer_in"
    return "deposit"


def pair_transfers(days=5):
    """Match investment deposits that came from a bank account with the bank's outgoing transaction."""
    acts = [a for a in load_activity() if a.get("type") in ("transfer_in", "deposit") and CHASE_IN.search(a.get("description") or "")]
    paired = 0
    for a in acts:
        lo, hi = _shift(a["date"], -days), _shift(a["date"], days)
        cands = [t for t in load_transactions(lo, hi) if round(t.get("amount") or 0, 2) == round(a["amount"], 2)
                 and (t.get("account") or "").startswith("chase") and not t.get("transfer_pair")]
        if not cands:
            continue
        t = min(cands, key=lambda t: abs((_d(t["date"]) - _d(a["date"])).days))
        col("transactions").document(t["id"]).set({"transfer_pair": a["id"], "category": "Transfer",
                                                    "category_before_pair": t.get("category")}, merge=True)
        col("inv_activity").document(a["id"]).set({"transfer_pair": t["id"], "type": "transfer_in"}, merge=True)
        paired += 1
    return paired


# ---------------------------------------------------------------- writing investment data
def save_statement(acct_id, s, src="ubs_pdf", file_name=None):
    """One parsed account section of a statement: totals, holdings snapshot, balance, activity."""
    sid = f"{acct_id}_{s['closing_date']}"
    stmt = {k: s.get(k) for k in ("period_label", "opening_date", "closing_date", "opening_value", "closing_value",
                                   "ytd", "change", "gains", "income", "allocation", "benchmarks", "benchmark_month",
                                   "eai_total", "contributions", "cost_basis_method", "type_label")}
    stmt |= {"account": acct_id, "src": src, "file_name": file_name, "imported_at": now_iso(),
             "activity_count": len(s.get("activity") or []), "holdings_value": r2(sum(h["value"] for h in s.get("holdings") or [])),
             "parsed_activity": {t: r2(sum(a["amount"] for a in s.get("activity") or [] if a["type"] == t and not a.get("pending")))
                                 for t in ("deposit", "transfer_in", "dividend", "interest", "buy", "sell", "withdrawal", "fee")}}
    col("inv_statements").document(sid).set(stmt)
    col("inv_snapshots").document(f"{acct_id}_{s['closing_date']}_{src}").set({
        "account": acct_id, "date": s["closing_date"], "src": src, "holdings": s.get("holdings") or [],
        "cash": s.get("cash") or 0.0, "total": s.get("closing_value"), "allocation": s.get("allocation") or {},
        "eai_total": s.get("eai_total")})
    col("fin_balances").document(f"{acct_id}_{s['closing_date']}_{src}").set({
        "account": acct_id, "date": s["closing_date"], "balance": s.get("closing_value"), "src": src})

    own = [a.get("mask") for a in accounts(include_hidden=True)]
    seen, written = {}, 0
    batch = db.batch()
    for a in s.get("activity") or []:
        typ = a["type"]
        if typ in ("deposit", "transfer_in"):
            typ = classify_inflow(a.get("description"), [n for n in own if n])
        k = (a["date"], typ, a.get("symbol"), a.get("quantity"), r2(a["amount"]), norm_desc(a.get("description")))
        seen[k] = seen.get(k, 0) + 1
        aid = make_id("pdf", acct_id, *k, seen[k])
        batch.set(col("inv_activity").document(aid), {
            "account": acct_id, "date": a["date"], "type": typ, "description": a.get("description"),
            "symbol": a.get("symbol"), "quantity": a.get("quantity"), "price": a.get("price"),
            "amount": r2(a["amount"]), "settle_date": a.get("settle_date"), "pending": bool(a.get("pending")),
            "src": src, "statement": sid}, merge=True)
        written += 1
    batch.commit()
    dedupe_activity(acct_id, s["opening_date"], s["closing_date"])
    return {"statement": sid, "activity": written}


def import_ubs_statement(parsed, file_name=None):
    out = []
    for s in parsed["accounts"]:
        if not s.get("closing_date"):
            continue
        aid = resolve_account("UBS", s["type"], mask=s["mask"],
                              name=("UBS Brokerage (RMA)" if s["type"] == "brokerage" else
                                    "Roth IRA" if s["type"] == "roth_ira" else
                                    "Rollover IRA" if s["type"] == "rollover_ira" else None),
                              alias="ubs:" + s["mask"])
        r = save_statement(aid, s, file_name=file_name)
        out.append({"account": aid, "period": s.get("period_label"), "closing_date": s["closing_date"],
                    "closing_value": s.get("closing_value"), **r})
    pair_transfers()
    return out


def import_tax_forms(parsed, file_name=None):
    rows = accounts(include_hidden=True)
    out = []
    for i, f in enumerate(parsed["forms"]):
        hint = re.sub(r"\D", "", f.get("account_hint") or "")
        # tax forms print the account as '2T NNNAB'; the statement number is '2T NNNAB CD', so the last 4 are 'ABCD'
        acct = next((a["id"] for a in rows if hint and a.get("institution") == "UBS" and a.get("mask")
                     and a["mask"][:2] == hint[-2:]), None)
        did = make_id("tax", parsed["year"], f["form"], hint, i)
        col("tax_docs").document(did).set({**f, "year": parsed["year"], "account": acct, "file_name": file_name,
                                           "imported_at": now_iso()})
        out.append({"form": f["form"], "account": acct})
    return out


# ---------------------------------------------------------------- snapshots, balances, coverage
def latest_snapshots():
    best = {}
    for d in col("inv_snapshots").stream():
        s = d.to_dict()
        cur = best.get(s["account"])
        if not cur or (s["date"], SOURCE_RANK.get(s["src"], 0)) > (cur["date"], SOURCE_RANK.get(cur["src"], 0)):
            best[s["account"]] = s
    # a Plaid snapshot has no tax lots; borrow them from the latest statement when the share count still matches
    for acct, s in best.items():
        if s["src"] == "plaid":
            pdfs = sorted([d.to_dict() for d in col("inv_snapshots").where("account", "==", acct).stream()
                           if d.to_dict().get("src") == "ubs_pdf"], key=lambda x: x["date"])
            if pdfs:
                lots = {h["symbol"]: h for h in pdfs[-1].get("holdings", [])}
                for h in s.get("holdings", []):
                    p = lots.get(h.get("symbol"))
                    if p and abs((p.get("quantity") or 0) - (h.get("quantity") or 0)) < 1e-6:
                        h["lots"] = p.get("lots")
    return best


def snapshots(account=None):
    rows = [d.to_dict() for d in col("inv_snapshots").stream()]
    rows = [r for r in rows if not account or r["account"] == account]
    rows.sort(key=lambda r: (r["date"], SOURCE_RANK.get(r["src"], 0)))
    return rows


def statements(account=None):
    rows = [d.to_dict() | {"id": d.id} for d in col("inv_statements").stream()]
    rows = [r for r in rows if not account or r["account"] == account]
    rows.sort(key=lambda r: r["closing_date"])
    return rows


def balances():
    """account -> sorted [(date, balance, src)], best source per date."""
    out = {}
    for d in col("fin_balances").stream():
        b = d.to_dict()
        out.setdefault(b["account"], {})
        cur = out[b["account"]].get(b["date"])
        if not cur or SOURCE_RANK.get(b["src"], 0) > SOURCE_RANK.get(cur[2], 0):
            out[b["account"]][b["date"]] = (b["date"], b["balance"], b["src"])
    return {k: sorted(v.values()) for k, v in out.items()}


def set_balance(account, day, balance, src="manual"):
    col("fin_balances").document(f"{account}_{day}_{src}").set({"account": account, "date": day, "balance": float(balance), "src": src})


def coverage():
    rows = {a["id"]: {"account": a["id"], "name": a["name"], "sources": {}} for a in accounts(include_hidden=True)}

    def add(acct, src, d):
        r = rows.setdefault(acct, {"account": acct, "name": acct, "sources": {}})
        cur = r["sources"].setdefault(src, {"start": d, "end": d, "records": 0})
        cur["start"], cur["end"] = min(cur["start"], d), max(cur["end"], d)
        cur["records"] += 1

    for d in col("transactions").stream():
        t = d.to_dict()
        if t.get("account") and t.get("date"):
            add(t["account"], t.get("src") or "csv", t["date"])
    for st in statements():
        r = rows.setdefault(st["account"], {"account": st["account"], "name": st["account"], "sources": {}})
        cur = r["sources"].setdefault("ubs_pdf", {"start": st["opening_date"], "end": st["closing_date"], "records": 0, "periods": []})
        cur["start"], cur["end"] = min(cur["start"], st["opening_date"]), max(cur["end"], st["closing_date"])
        cur["records"] += 1
        cur.setdefault("periods", []).append({"start": st["opening_date"], "end": st["closing_date"], "label": st.get("period_label")})
    for d in col("inv_activity").stream():
        a = d.to_dict()
        if a.get("src") == "plaid":
            add(a["account"], "plaid", a["date"])
    for r in rows.values():
        p = (r["sources"].get("ubs_pdf") or {}).get("periods") or []
        p.sort(key=lambda x: x["start"])
        gaps = [{"from": a["end"], "to": b["start"]} for a, b in zip(p, p[1:]) if b["start"] > a["end"]]
        overlaps = [{"from": b["start"], "to": a["end"]} for a, b in zip(p, p[1:]) if b["start"] < a["end"]]
        if p:
            r["sources"]["ubs_pdf"]["gaps"], r["sources"]["ubs_pdf"]["overlaps"] = gaps, overlaps
    return [r for r in rows.values() if r["sources"]] + [r for r in rows.values() if not r["sources"]]


# ---------------------------------------------------------------- migration of pre-ledger data
def migrate():
    """Give every existing transaction a canonical account and a source, then match across sources.
    Runs once per user (fin_meta/state.migrated)."""
    meta = col("fin_meta").document("state")
    m = meta.get()
    if m.exists and (m.to_dict() or {}).get("migrated", 0) >= 1:
        return False
    ensure_defaults()
    plaid_map = {a: v for a, v in ((m.to_dict() or {}).get("plaid_accounts") or {}).items()} if m.exists else {}
    batch, n = db.batch(), 0
    for d in col("transactions").stream():
        t = d.to_dict()
        if t.get("account") and t.get("src"):
            continue
        aid = t.get("account_id") or ""
        if d.id.startswith("venmo_") or aid == "venmo":
            acct, src = "venmo", "venmo_csv"
        elif aid.startswith("import:"):
            acct, src = account_for_label(aid[len("import:"):]), "chase_csv" if "chase" in aid.lower() else "csv"
        else:
            acct, src = plaid_map.get(aid, "plaid:" + aid), "plaid"
        batch.set(d.reference, {"account": acct, "src": src}, merge=True)
        n += 1
        if n % 400 == 0:
            batch.commit(); batch = db.batch()
    batch.commit()
    meta.set({"migrated": 1, "migrated_at": now_iso()}, merge=True)
    dedupe_all()
    return True


def map_plaid_account(plaid_account_id, canonical):
    """Remember which canonical account a Plaid account is; fix any transactions waiting for it."""
    col("fin_meta").document("state").set({"plaid_accounts": {plaid_account_id: canonical}}, merge=True)
    batch, n = db.batch(), 0
    for d in col("transactions").where("account", "==", "plaid:" + plaid_account_id).stream():
        batch.set(d.reference, {"account": canonical}, merge=True); n += 1
        if n % 400 == 0:
            batch.commit(); batch = db.batch()
    batch.commit()

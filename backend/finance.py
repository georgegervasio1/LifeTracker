"""Finances API: net worth, cash & cards, investments, income, taxes, imports. See ledger.py for the data model.

  GET    /api/fin/overview                      net worth, accounts, what moved it
  GET    /api/fin/cash?account=&start=&end=     one cash/card account (or 'all'): balance, spending, transactions
  GET    /api/fin/investments?account=          holdings + lots, allocation, returns vs S&P 500, movers, fees, dividends, retirement
  GET    /api/fin/income?year=                  jobs, trust distributions, investment income
  GET    /api/fin/taxes?year=                   the year's tax picture;  PUT /api/fin/tax-settings
  POST   /api/fin/profile                       finance profile file (paycheck job, tax settings, jobs)
  POST   /api/fin/upload                        UBS statement / tax-form PDFs (several at once)
  GET    /api/fin/imports                       coverage timeline, reconciliation, duplicates, files on record
  POST   /api/fin/reconcile                     re-run cross-source matching and transfer pairing
  PATCH  /api/fin/accounts/<id>, POST /api/fin/accounts, POST /api/fin/balances
  PATCH  /api/fin/activity/<id>, PATCH /api/fin/transactions/<id>
  POST/PATCH/DELETE /api/fin/income/jobs[/<id>]
"""
from collections import defaultdict
from datetime import date, timedelta

from flask import Blueprint, jsonify, request

import fintax
import ledger as L
import portfolio as P
import ubs_parser

bp = Blueprint("finance", __name__)
require_owner = None
MAX_PDF = 15_000_000
PAY_JOB = "paychecks"      # the job backed by the paycheck calculator (earnings.py); other jobs live in income_jobs


_routes_done = False


def init(app, _db, _firestore, _require_owner, _user_ref):
    global require_owner, _routes_done
    L.init(_db, _firestore, _user_ref)
    if not _routes_done:
        require_owner = _require_owner
        _routes()
        _routes_done = True
    app.register_blueprint(bp)


def r2(x):
    return None if x is None else round(float(x) + 0.0, 2)


def _ready():
    L.ensure_defaults()
    L.migrate()


# ---------------------------------------------------------------- cash & cards
def cash_view(account, start, end):
    accts = {a["id"]: a for a in L.accounts()}
    if account == "p2p":                  # Venmo + Zelle (Zelle shows up inside checking)
        rows = [t for t in L.load_transactions(start, end) if t.get("account") == "venmo" or t.get("category") == "Zelle"
                or "ZELLE" in (t.get("merchant") or "").upper()]
    elif account in (None, "", "all"):
        rows = L.load_transactions(start, end)
    else:
        rows = L.load_transactions(start, end, account=account)
    by_cat, by_month = defaultdict(float), defaultdict(float)
    spent = money_in = transfers = 0.0
    for t in rows:
        a, cat = t.get("amount") or 0, t.get("category")
        if cat in L.TRANSFER_CATEGORIES or t.get("transfer_pair"):
            transfers += a
            continue
        if a < 0 and cat != "Refund":
            money_in += -a
            continue
        spent += a
        by_cat[cat or "Other"] += a
        by_month[t["date"][:7]] += a
    bal = L.balances()
    acct = accts.get(account)
    b = (bal.get(account) or [None])[-1] if acct else None
    rows.sort(key=lambda t: t["date"], reverse=True)
    keep = ("id", "date", "merchant", "amount", "category", "source", "src", "account", "note", "pending", "transfer_pair", "charitable")
    return {"account": acct, "balance": {"value": b[1], "date": b[0], "src": b[2]} if b else None,
            "spent": r2(spent), "money_in": r2(money_in), "transfers": r2(transfers),
            "by_category": {k: r2(v) for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])},
            "by_month": {k: r2(v) for k, v in sorted(by_month.items())},
            "transactions": [{k: t.get(k) for k in keep} for t in rows[:800]], "count": len(rows),
            "accounts": [a for a in accts.values() if a["group"] in ("cash", "credit")]}


# ---------------------------------------------------------------- investments
def investments_view(account=None):
    accts = P.investment_accounts()
    if account in ("", "all"):
        account = None
    if account and not any(a["id"] == account for a in accts):   # allow a type ('brokerage', 'roth_ira'...) as shorthand
        hit = next((a for a in accts if a["type"] == account), None)
        account = hit["id"] if hit else None
    h = P.holdings(account)
    out = {"accounts": accts, "account": account, "holdings": h, "allocation": P.allocation(account), "fees": P.fees(account),
           "movers": P.movers(account), "dividends": P.dividends(account), "value_series": P.value_series(account),
           "settings": fintax.settings()}
    if account:
        out["performance"] = {"ytd": P.ytd_series(account), "periods": P.period_returns(account)}
        a = next(a for a in accts if a["id"] == account)
        if a["type"] == "roth_ira":
            out["roth"] = P.roth_detail(account, fintax.settings())
        if a.get("tax") == "pre_tax":
            out["pre_tax"] = {"note": a.get("note") or "Pre-tax money: every dollar is taxed as ordinary income when withdrawn."}
        out["statements"] = [{k: s.get(k) for k in ("id", "period_label", "opening_date", "closing_date", "opening_value", "closing_value", "change", "gains")}
                             for s in L.statements(account)]
    else:
        out["performance"] = {"by_account": [{"account": a["id"], "name": a["name"], **P.ytd_series(a["id"])} for a in accts]}
    return out


# ---------------------------------------------------------------- income
def jobs(pay_settings=None):
    """Jobs the user added, plus the paycheck-calculator job. Its name and dates come from the earnings settings
    (the employer from the bank feed), so nothing personal lives in code."""
    rows = [d.to_dict() | {"id": d.id} for d in L.col("income_jobs").stream()]
    rows = [j for j in rows if j.get("link") != "earnings"]        # older builds stored the paycheck job here too
    s = pay_settings or {}
    rows.append({"id": PAY_JOB, "employer": s.get("employer") or "Paychecks", "role": s.get("role") or "",
                 "start": s.get("start"), "end": s.get("end"), "work_state": s.get("work_state"), "link": "earnings", "order": 1})
    return sorted(rows, key=lambda j: (j.get("order") or 99, j.get("employer") or ""))


def income_view(year):
    import earnings
    job = earnings.state()
    js = jobs(job.get("settings"))
    out_jobs = []
    for j in js:
        if j.get("link") == "earnings":
            t = job.get("totals") or {}
            out_jobs.append({**j, "gross": t.get("gross"), "net": t.get("net"), "federal": t.get("federal"), "state": t.get("state"),
                             "checks": t.get("checks"), "year": job.get("year"), "has_data": bool(job.get("ready"))})
        else:
            pay = sorted(j.get("pay") or [], key=lambda p: p.get("date") or "")
            out_jobs.append({**j, "gross": r2(sum(float(p.get("gross") or 0) for p in pay)), "net": r2(sum(float(p.get("net") or 0) for p in pay)),
                             "checks": len(pay), "has_data": bool(pay), "pay": pay})
    trust = [a for a in L.load_activity() if a["type"] == "trust_distribution"]
    inv = [a for a in L.load_activity() if a["type"] in ("dividend", "interest")]
    accts = {a["id"]: a for a in L.accounts(include_hidden=True)}
    by_year = defaultdict(lambda: defaultdict(float))
    for j in out_jobs:
        if j.get("link") == "earnings" and j.get("gross"):
            by_year[str(j.get("year"))]["Wages"] += j["gross"]
        for p in j.get("pay") or []:
            by_year[str(p.get("date", ""))[:4]]["Wages"] += float(p.get("gross") or 0)
    for a in trust:
        by_year[a["date"][:4]]["Trust distributions"] += a["amount"]
    for a in inv:
        k = "Investment income (taxable)" if (accts.get(a["account"]) or {}).get("tax") == "taxable" else "Investment income (IRA, not taxed yet)"
        by_year[a["date"][:4]][k] += a["amount"]
    inv_y = [a for a in inv if a["date"][:4] == str(year)]
    by_acct = defaultdict(float)
    for a in inv_y:
        by_acct[(accts.get(a["account"]) or {}).get("name", a["account"])] += a["amount"]
    return {"year": year, "jobs": out_jobs,
            "trust": [{"date": a["date"], "amount": a["amount"], "description": a.get("description"), "account": (accts.get(a["account"]) or {}).get("name"), "id": a["id"]} for a in trust],
            "trust_total": r2(sum(a["amount"] for a in trust if a["date"][:4] == str(year))),
            "investment_income": {"total": r2(sum(a["amount"] for a in inv_y)), "by_account": {k: r2(v) for k, v in by_acct.items()},
                                  "items": [{"date": a["date"], "amount": a["amount"], "symbol": a.get("symbol"), "type": a["type"],
                                             "account": (accts.get(a["account"]) or {}).get("name")} for a in reversed(inv_y)][:80]},
            "by_year": {y: {k: r2(v) for k, v in d.items()} for y, d in sorted(by_year.items()) if y and y != "None"}}


# ---------------------------------------------------------------- imports
def imports_view():
    sts = L.statements()
    docs = [d.to_dict() | {"id": d.id} for d in L.col("tax_docs").stream()]
    return {"coverage": L.coverage(), "reconciliation": P.reconciliation(), "duplicates": L.duplicate_report(),
            "statements": [{k: s.get(k) for k in ("id", "account", "period_label", "opening_date", "closing_date", "closing_value", "file_name", "imported_at")} for s in sts],
            "tax_docs": sorted(docs, key=lambda d: (d.get("year"), d.get("form"))), "accounts": L.accounts(include_hidden=True),
            "tax_settings": fintax.settings()}


def _routes():
    @bp.get("/api/fin/overview")
    @require_owner
    def overview():
        _ready()
        nw = P.net_worth()
        today = date.today()
        start = today.replace(day=1).isoformat()
        month = cash_view("all", start, today.isoformat())
        return jsonify(nw | {"month_spent": month["spent"], "month_in": month["money_in"],
                             "investments": {"total": P.holdings()["total"], "movers": P.movers()},
                             "taxes": _tax_brief()})

    def _tax_brief():
        try:
            t = fintax.compute(date.today().year)
            return {"settle": t["settle"], "income_tax": t["income_tax"], "needs_payment": t["estimated"]["needs_payment"],
                    "next_due": t["estimated"]["next_due"]}
        except Exception as e:      # the dashboard should still load if a tax input is missing
            return {"error": str(e)[:200]}

    @bp.get("/api/fin/cash")
    @require_owner
    def cash():
        _ready()
        end = request.args.get("end") or date.today().isoformat()
        start = request.args.get("start") or (date.today() - timedelta(days=90)).isoformat()
        return jsonify(cash_view(request.args.get("account") or "all", start, end))

    @bp.get("/api/fin/investments")
    @require_owner
    def investments():
        _ready()
        return jsonify(investments_view(request.args.get("account")))

    @bp.get("/api/fin/income")
    @require_owner
    def income():
        _ready()
        return jsonify(income_view(int(request.args.get("year") or date.today().year)))

    @bp.post("/api/fin/income/jobs")
    @require_owner
    def add_job():
        b = request.get_json(force=True) or {}
        if not b.get("employer"):
            return jsonify(error="employer is required"), 400
        ref = L.col("income_jobs").document()
        ref.set(_clean_job(b) | {"order": 50})
        return jsonify(income_view(date.today().year))

    @bp.patch("/api/fin/income/jobs/<jid>")
    @require_owner
    def patch_job(jid):
        ref = L.col("income_jobs").document(jid)
        if not ref.get().exists:
            return jsonify(error="job not found"), 404
        ref.set(_clean_job(request.get_json(force=True) or {}), merge=True)
        return jsonify(income_view(date.today().year))

    @bp.delete("/api/fin/income/jobs/<jid>")
    @require_owner
    def delete_job(jid):
        if jid == PAY_JOB:
            return jsonify(error="This job is linked to the paycheck calculator"), 400
        L.col("income_jobs").document(jid).delete()
        return jsonify(income_view(date.today().year))

    def _clean_job(b):
        out = {}
        for k in ("employer", "role", "start", "end", "work_state", "notes"):
            if k in b:
                out[k] = (str(b[k]).strip()[:120] or None) if b[k] is not None else None
        if "salary" in b:
            out["salary"] = float(b["salary"]) if b["salary"] not in (None, "") else None
        if "pay" in b:
            out["pay"] = [{"date": str(p.get("date"))[:10], **{k: float(p.get(k) or 0) for k in ("gross", "net", "federal", "state", "ss", "medicare")}}
                          for p in (b.get("pay") or []) if p.get("date")][:200]
        return out

    @bp.get("/api/fin/taxes")
    @require_owner
    def taxes():
        _ready()
        return jsonify(fintax.compute(int(request.args.get("year") or date.today().year)))

    @bp.put("/api/fin/tax-settings")
    @require_owner
    def tax_settings():
        fintax.save_settings(request.get_json(force=True) or {})
        return jsonify(fintax.compute(date.today().year))

    @bp.post("/api/fin/profile")
    @require_owner
    def import_profile():
        """Load a finance profile file: the paycheck job, tax settings and extra jobs. Personal details live in this
        file (kept outside the public repo) and in Firestore, never in code. Re-importing overwrites the same fields."""
        import earnings
        b = request.get_json(force=True) or {}
        if b.get("kind") != "lifetracker-finance-profile":
            return jsonify(error="Not a LifeTracker finance profile file"), 400
        try:
            if b.get("paycheck_job"):
                earnings._settings_ref().set(earnings.clean_settings(b["paycheck_job"]), merge=True)
            if b.get("tax_settings"):
                fintax.save_settings(b["tax_settings"])
            existing = {(d.to_dict().get("employer") or "").lower(): d.id for d in L.col("income_jobs").stream()}
            for j in b.get("jobs") or []:
                if not j.get("employer"):
                    continue
                jid = existing.get(j["employer"].lower())
                ref = L.col("income_jobs").document(jid) if jid else L.col("income_jobs").document()
                ref.set(_clean_job(j) | ({} if jid else {"order": 50}), merge=True)
        except (ValueError, TypeError) as e:
            return jsonify(error=str(e)[:200]), 400
        return jsonify(income_view(date.today().year))

    @bp.post("/api/fin/upload")
    @require_owner
    def upload():
        _ready()
        files = request.files.getlist("files") or ([request.files["file"]] if "file" in request.files else [])
        if not files:
            return jsonify(error="choose one or more PDFs"), 400
        results = []
        for f in files[:24]:
            data = f.read()
            name = (f.filename or "file.pdf")[:200]
            if not name.lower().endswith(".pdf"):
                results.append({"file": name, "error": "Only PDFs here. Chase and Venmo CSVs go in the CSV importers."}); continue
            if len(data) > MAX_PDF:
                results.append({"file": name, "error": "File is over 15 MB"}); continue
            try:
                parsed = ubs_parser.parse_any(data)
                if parsed["kind"] == "ubs_statement":
                    results.append({"file": name, "kind": "statement", "accounts": L.import_ubs_statement(parsed, file_name=name)})
                else:
                    results.append({"file": name, "kind": "tax_forms", "year": parsed["year"], "forms": L.import_tax_forms(parsed, file_name=name)})
            except ValueError as e:
                results.append({"file": name, "error": str(e)})
            except Exception as e:
                results.append({"file": name, "error": f"Couldn't read this PDF ({e.__class__.__name__})"})
        L.pair_transfers()
        return jsonify(results=results, imports=imports_view())

    @bp.get("/api/fin/imports")
    @require_owner
    def imports():
        _ready()
        return jsonify(imports_view())

    @bp.post("/api/fin/reconcile")
    @require_owner
    def reconcile():
        _ready()
        changed = L.dedupe_all()
        paired = L.pair_transfers()
        return jsonify(changed=changed, paired=paired, imports=imports_view())

    @bp.post("/api/fin/accounts")
    @require_owner
    def add_account():
        b = request.get_json(force=True) or {}
        if not b.get("name") or b.get("type") not in L.ACCOUNT_KINDS:
            return jsonify(error="name and a valid type are required"), 400
        aid = L.resolve_account(b.get("institution") or b["name"].split()[0], b["type"], mask=(b.get("mask") or None),
                                name=b["name"], alias="manual:" + b["name"], note=b.get("note"))
        if b.get("balance") not in (None, ""):
            L.set_balance(aid, b.get("date") or date.today().isoformat(), b["balance"])
        return jsonify(accounts=L.accounts(include_hidden=True), id=aid)

    @bp.patch("/api/fin/accounts/<aid>")
    @require_owner
    def patch_account(aid):
        b = request.get_json(force=True) or {}
        upd = {k: b[k] for k in ("name", "note", "hidden", "institution") if k in b}
        if "type" in b and b["type"] in L.ACCOUNT_KINDS:
            upd["type"] = b["type"]
        L.col("fin_accounts").document(aid).set(upd, merge=True)
        return jsonify(accounts=L.accounts(include_hidden=True))

    @bp.post("/api/fin/balances")
    @require_owner
    def add_balance():
        b = request.get_json(force=True) or {}
        try:
            L.set_balance(b["account"], (b.get("date") or date.today().isoformat())[:10], float(b["balance"]))
        except (KeyError, ValueError):
            return jsonify(error="account and balance are required"), 400
        return jsonify(P.net_worth())

    @bp.patch("/api/fin/activity/<aid>")
    @require_owner
    def patch_activity(aid):
        b = request.get_json(force=True) or {}
        if b.get("type") not in ("deposit", "transfer_in", "trust_distribution", "contribution", "withdrawal", "transfer_out"):
            return jsonify(error="unsupported type"), 400
        L.col("inv_activity").document(aid).set({"type": b["type"], "type_set_by_user": True}, merge=True)
        return jsonify(ok=True)

    @bp.patch("/api/fin/transactions/<tid>")
    @require_owner
    def patch_tx(tid):
        b = request.get_json(force=True) or {}
        upd = {}
        if "charitable" in b:
            upd["charitable"] = None if b["charitable"] is None else bool(b["charitable"])
        if "category" in b:
            upd["category"] = str(b["category"])[:60]
        L.col("transactions").document(tid).set(upd, merge=True)
        return jsonify(ok=True)

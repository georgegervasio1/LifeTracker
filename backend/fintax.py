"""One tax picture for the year: wages, the trust distribution, investment income, deductions, federal + Illinois
(nonresident, on Illinois wages) + DC (resident, on everything, with a credit for Illinois tax), estimated payments,
and what retirement withdrawals would be taxed at. An estimate, not tax advice.

Inputs come from the ledger and the paycheck module; anything the documents can't tell us (the trust's taxable
share until its 1099-R / K-1 arrives, last year's tax for safe harbor, birth year) is a setting the user edits.

2026 tax-law figures and their sources:
  brackets, standard deduction, FICA, Illinois, DC  -> earnings.TAX[2026] (sources noted there)
  long-term capital gains / qualified dividends 0% to $49,450, 15% to $545,500 (single)  -> ustax.tools, 2026 capital gains rates
  NIIT 3.8% over $200,000 MAGI (single, not indexed)                               -> same
  charitable: up to $1,000 for non-itemizers; itemizers deduct gifts above 0.5% of AGI -> Taft Law, "Charitable Giving After the OBBBA"
  SALT cap $40,400 for 2026 (OBBBA: $40,000 in 2025, +1% a year)                    -> OBBBA sec. 70120
  Roth IRA: $7,500 limit; single MAGI phase-out $153,000-$168,000                    -> Vanguard, Roth IRA income limits
  Illinois subtracts IRA / qualified plan distributions taxed federally              -> TaxSlayer, "Does Illinois tax pensions"
"""
import re
from datetime import date

import earnings
import ledger as L
import portfolio as P

LAW = {2026: {"ltcg": [(0, 0.0), (49450, 0.15), (545500, 0.20)], "niit_threshold": 200000, "niit_rate": 0.038,
              "charity_nonitemizer": 1000, "charity_floor_pct": 0.005, "salt_cap": 40400,
              "roth_limit": 7500, "roth_phaseout": (153000, 168000), "dc_estimate_threshold": 100,
              "q_due": ["2026-04-15", "2026-06-15", "2026-09-15", "2027-01-15"]}}
DEFAULT_SETTINGS = {"filing_status": "single", "resident_state": "DC", "work_state": "IL",
                    "trust_taxable_pct": 100, "trust_federal_withheld": 0, "trust_state_withheld": 0,
                    "prior_year_tax": None, "prior_year_agi": None, "estimated_payments": [],
                    "birth_year": None, "roth_first_year": None, "roth_prior_contributions": {},
                    "charitable_extra": 0, "qualified_dividend_pct": None}
CHARITY = re.compile(r"BIG SHOULDERS|DONAT|CHARIT|FOUNDATION|GOFUNDME|UNITED WAY|RED CROSS|\bFUND\b.*\bGIFT|ALUMNI", re.I)


def r2(x):
    return None if x is None else round(float(x) + 0.0, 2)


def settings():
    snap = L.col("fin_settings").document("tax").get()
    return {**DEFAULT_SETTINGS, **(snap.to_dict() if snap.exists else {})}


def save_settings(b):
    allowed = set(DEFAULT_SETTINGS)
    upd = {k: v for k, v in b.items() if k in allowed}
    for k in ("trust_taxable_pct", "trust_federal_withheld", "trust_state_withheld", "prior_year_tax", "prior_year_agi",
              "birth_year", "roth_first_year", "charitable_extra", "qualified_dividend_pct"):
        if k in upd and upd[k] not in (None, ""):
            upd[k] = float(upd[k]) if k not in ("birth_year", "roth_first_year") else int(upd[k])
        elif k in upd:
            upd[k] = None
    if "estimated_payments" in upd:
        upd["estimated_payments"] = [{"date": str(p.get("date"))[:10], "jurisdiction": p.get("jurisdiction") or "federal",
                                      "amount": float(p.get("amount") or 0)} for p in upd["estimated_payments"] or []][:24]
    L.col("fin_settings").document("tax").set(upd, merge=True)
    return settings()


def _brackets(income, table):
    tax, fill = 0.0, []
    for i, (lo, rate) in enumerate(table):
        hi = table[i + 1][0] if i + 1 < len(table) else None
        amt = max(0.0, min(income, hi) - lo) if hi is not None else max(0.0, income - lo)
        tax += amt * rate
        fill.append({"from": lo, "to": hi, "rate": rate, "amount": r2(amt), "tax": r2(amt * rate)})
    return tax, fill


def _pref_tax(ordinary_taxable, pref, table):
    """Qualified dividends / long-term gains stack on top of ordinary income."""
    tax, start = 0.0, ordinary_taxable
    for i, (lo, rate) in enumerate(table):
        hi = table[i + 1][0] if i + 1 < len(table) else float("inf")
        seg = max(0.0, min(start + pref, hi) - max(start, lo))
        tax += seg * rate
    return tax


# ---------------------------------------------------------------- gathering income
def wages(year):
    job = earnings.state()
    if not job.get("ready") or job.get("year") != year:
        return {"gross": 0.0, "federal": 0.0, "state": 0.0, "dc": 0.0, "ss": 0.0, "medicare": 0.0, "employer": None, "jobs": []}
    t = job["totals"]
    emp = " · ".join(x for x in (job["settings"].get("employer"), job["settings"].get("role")) if x) or "Wages"
    jobs = [{"employer": emp, "gross": t["gross"], "work_state": job["settings"].get("work_state")}]
    # other jobs with pay entered on the Income tab
    other = 0.0
    for d in L.col("income_jobs").stream():
        j = d.to_dict()
        if j.get("link") == "earnings":
            continue
        g = sum(float(p.get("gross") or 0) for p in j.get("pay") or [] if str(p.get("date", ""))[:4] == str(year))
        if g:
            jobs.append({"employer": j.get("employer"), "gross": r2(g), "work_state": j.get("work_state")})
            other += g
    return {"gross": r2(t["gross"] + other), "federal": t["federal"], "state": t["state"], "dc": t["dc"], "ss": t["ss"],
            "medicare": t["medicare"], "employer": emp, "jobs": jobs, "il_wages": r2(t["gross"] if job["settings"].get("work_state") == "IL" else 0)}


def trust(year, s):
    rows = [a for a in L.load_activity(f"{year}-01-01", f"{year}-12-31") if a["type"] == "trust_distribution"]
    gross = sum(a["amount"] for a in rows)
    pct = (s.get("trust_taxable_pct") if s.get("trust_taxable_pct") is not None else 100) / 100
    r_docs = [d.to_dict() for d in L.col("tax_docs").where("year", "==", year).stream() if d.to_dict().get("form") in ("1099-R", "K-1")]
    return {"gross": r2(gross), "taxable": r2(gross * pct), "taxable_pct": pct * 100, "withheld_federal": float(s.get("trust_federal_withheld") or 0),
            "withheld_state": float(s.get("trust_state_withheld") or 0),
            "items": [{"date": a["date"], "amount": a["amount"], "description": a.get("description"), "id": a["id"], "account": a["account"]} for a in rows],
            "form_on_file": bool(r_docs)}


def investment_income(year):
    """Taxable accounts only (IRA dividends aren't taxed until withdrawn)."""
    taxable = [a["id"] for a in L.accounts() if a.get("tax") == "taxable"]
    divs = intr = st_g = lt_g = 0.0
    srcs = []
    for acct in taxable:
        sts = [s for s in L.statements(acct) if s["closing_date"][:4] == str(year)]
        last_close = None
        if sts:
            s = sts[-1]
            inc, g = s.get("income") or {}, s.get("gains") or {}
            divs += inc.get("dividends_ytd") or 0
            intr += inc.get("interest_ytd") or 0
            st_g += (g.get("st") or {}).get("ytd") or 0
            lt_g += (g.get("lt") or {}).get("ytd") or 0
            last_close = s["closing_date"]
            srcs.append({"label": f"{s.get('period_label')} statement, year-to-date", "statement": s["id"]})
        for a in L.load_activity(last_close or f"{year}-01-01", f"{year}-12-31", account=acct):
            if last_close and a["date"] <= last_close:
                continue
            if a["type"] == "dividend":
                divs += a["amount"]
            elif a["type"] == "interest":
                intr += a["amount"]
    return {"dividends": r2(divs), "interest": r2(intr), "st_gains": r2(st_g), "lt_gains": r2(lt_g), "sources": srcs}


def charitable(year, s):
    rows = []
    for t in L.load_transactions(f"{year}-01-01", f"{year}-12-31"):
        if t.get("amount", 0) > 0 and (t.get("charitable") or t.get("category") == "Gifts And Donations" or CHARITY.search(t.get("merchant") or "")):
            if t.get("charitable") is False:
                continue
            rows.append({"date": t["date"], "merchant": t.get("merchant"), "amount": t["amount"], "id": t["id"], "account": t.get("account")})
    total = sum(r["amount"] for r in rows) + float(s.get("charitable_extra") or 0)
    return {"total": r2(total), "items": rows, "extra": float(s.get("charitable_extra") or 0)}


# ---------------------------------------------------------------- the calculation
def compute(year=2026):
    s = settings()
    T = earnings.TAX[year]
    law = LAW[year]
    fs = "mfj" if s.get("filing_status") == "mfj" else "single"
    w = wages(year)
    tr = trust(year, s)
    inv = investment_income(year)
    ch = charitable(year, s)

    prior_div = next((d.to_dict() for d in L.col("tax_docs").where("year", "==", year - 1).stream() if d.to_dict().get("form") == "1099-DIV"), None)
    qpct = s.get("qualified_dividend_pct")
    if qpct is None:
        qpct = (prior_div["qualified_dividends"] / prior_div["ordinary_dividends"] * 100) if prior_div and prior_div.get("ordinary_dividends") else 0.0
    qdiv = (inv["dividends"] or 0) * qpct / 100
    pref = qdiv + max(0.0, inv["lt_gains"] or 0)

    agi = (w["gross"] or 0) + tr["taxable"] + (inv["dividends"] or 0) + (inv["interest"] or 0) + (inv["st_gains"] or 0) + (inv["lt_gains"] or 0)
    std = T["std_deduction"][fs]
    salt = min(law["salt_cap"], (w["state"] or 0) + (w["dc"] or 0) + tr["withheld_state"] +
               sum(p["amount"] for p in s.get("estimated_payments") or [] if p["jurisdiction"] != "federal" and p["date"][:4] == str(year)))
    itemized = salt + max(0.0, ch["total"] - law["charity_floor_pct"] * agi)
    nonitem_charity = min(ch["total"], law["charity_nonitemizer"] * (2 if fs == "mfj" else 1))
    use_itemized = itemized > std + nonitem_charity
    deduction = itemized if use_itemized else std + nonitem_charity
    taxable = max(0.0, agi - deduction)
    ordinary_taxable = max(0.0, taxable - pref)
    ord_tax, fill = _brackets(ordinary_taxable, T["brackets"][fs])
    pref_tax = _pref_tax(ordinary_taxable, min(pref, taxable), law["ltcg"])
    nii = (inv["dividends"] or 0) + (inv["interest"] or 0) + (inv["st_gains"] or 0) + (inv["lt_gains"] or 0)
    niit = law["niit_rate"] * max(0.0, min(nii, agi - law["niit_threshold"]))
    fed_tax = ord_tax + pref_tax + niit
    est = s.get("estimated_payments") or []
    fed_paid = (w["federal"] or 0) + tr["withheld_federal"] + sum(p["amount"] for p in est if p["jurisdiction"] == "federal" and p["date"] <= f"{year + 1}-01-15")
    marginal = next((b["rate"] for b in reversed(fill) if b["amount"]), 0)

    # Illinois: nonresident, taxed on Illinois-source wages only; exemption prorated by the Illinois share of AGI
    il_wages = w.get("il_wages") or 0
    il_ratio = il_wages / agi if agi else 0
    il_tax = max(0.0, (il_wages - T["il_exemption"] * il_ratio) * T["il_rate"]) if il_wages else 0.0
    il_paid = (w["state"] or 0) + sum(p["amount"] for p in est if p["jurisdiction"] == "IL")

    # DC: resident, taxed on all income; credit for Illinois tax, capped at DC's tax on the Illinois income
    dc = None
    if s.get("resident_state") == "DC":
        dc_std = std
        dc_taxable = max(0.0, agi - dc_std)
        dc_tax, dc_fill = _brackets(dc_taxable, T["dc_brackets"])
        cap = dc_tax * (il_wages / agi) if agi else 0
        credit = min(il_tax, cap)
        dc_paid = (w["dc"] or 0) + tr["withheld_state"] + sum(p["amount"] for p in est if p["jurisdiction"] == "DC")
        dc = {"agi": r2(agi), "std_deduction": dc_std, "taxable": r2(dc_taxable), "tax_before_credit": r2(dc_tax),
              "credit_for_illinois": r2(credit), "tax": r2(dc_tax - credit), "paid": r2(dc_paid), "balance": r2(dc_paid - (dc_tax - credit)),
              "fill": dc_fill, "marginal": next((b["rate"] for b in reversed(dc_fill) if b["amount"]), 0)}

    fed = {"agi": r2(agi), "deduction": r2(deduction), "deduction_kind": "itemized" if use_itemized else "standard + charitable",
           "std_deduction": std, "itemized": r2(itemized), "salt": r2(salt), "charity_nonitemizer": r2(nonitem_charity),
           "taxable": r2(taxable), "ordinary_taxable": r2(ordinary_taxable), "preferential": r2(min(pref, taxable)),
           "ordinary_tax": r2(ord_tax), "preferential_tax": r2(pref_tax), "niit": r2(niit), "tax": r2(fed_tax),
           "paid": r2(fed_paid), "balance": r2(fed_paid - fed_tax), "fill": fill, "marginal": marginal,
           "effective": r2(fed_tax / agi * 100) if agi else 0, "qualified_dividend_pct": r2(qpct)}
    il = {"wages": r2(il_wages), "ratio_pct": r2(il_ratio * 100), "exemption": T["il_exemption"], "tax": r2(il_tax),
          "paid": r2(il_paid), "balance": r2(il_paid - il_tax), "note": "Nonresident: Illinois taxes only the wages earned there. The trust money and dividends aren't Illinois income."}

    # estimated payments / safe harbor (federal)
    prior = s.get("prior_year_tax")
    req = 0.9 * fed_tax
    if prior is not None:
        mult = 1.10 if (s.get("prior_year_agi") or 0) > 150000 else 1.0
        req = min(req, prior * mult)
    owe = fed_tax - fed_paid
    q4 = law["q_due"][3]
    est_out = {"federal_required": r2(req), "federal_paid": r2(fed_paid), "short_by": r2(max(0.0, req - fed_paid)),
               "owe_at_filing": r2(max(0.0, owe)), "next_due": next((d for d in law["q_due"] if d >= date.today().isoformat()), q4),
               "needs_payment": owe >= 1000 and fed_paid < req,
               "explain": ("The trust money arrived on " + (tr["items"][0]["date"] if tr["items"] else "—") +
                           ", which falls in the last estimated-tax period (Sept 1 to Dec 31), due Jan 15. The annualized-income method "
                           "(Form 2210, Schedule AI) generally means you aren't penalized for earlier quarters on income you received late in the year.")
                          if tr["items"] else None,
               "prior_year_known": prior is not None,
               "dc_needs_payment": bool(dc and -dc["balance"] > law["dc_estimate_threshold"]),
               "dc_short_by": r2(max(0.0, -dc["balance"])) if dc else None}

    # Roth contribution check
    lo, hi = law["roth_phaseout"]
    magi = agi
    allowed = law["roth_limit"] if magi <= lo else 0.0 if magi >= hi else law["roth_limit"] * (hi - magi) / (hi - lo)
    allowed = max(0.0, (int(allowed / 10 + 0.999) * 10) if 0 < allowed < law["roth_limit"] else allowed)
    contributed = 0.0
    for a in L.accounts():
        if a["type"] == "roth_ira":
            for row in P.roth_detail(a["id"], s)["by_year"]:
                if row["year"] == year:
                    contributed += row["contributed"] or 0
    roth = {"magi": r2(magi), "phaseout": [lo, hi], "limit": law["roth_limit"], "allowed": r2(allowed), "contributed": r2(contributed),
            "ok": contributed <= allowed + 0.01,
            "note": "Your 2026 income is below the Roth phase-out, so the full contribution is allowed." if magi <= lo else
                    "Your 2026 income is inside the Roth phase-out range: only part of a contribution is allowed." if magi < hi else
                    "Your 2026 income is above the Roth limit: a direct contribution isn't allowed (excess contributions can be withdrawn or recharacterized before the filing deadline)."}

    sources = [{"label": w["employer"] or "Wages", "amount": w["gross"], "link": "#/finances/income?sub=kirkland"} if w["gross"] else None,
               {"label": "Trust distribution", "amount": tr["taxable"], "link": "#/finances/income?sub=trust",
                "note": None if tr["form_on_file"] else "Taxable share assumed 100% until the 1099-R or K-1 arrives"} if tr["gross"] else None,
               {"label": "Dividends (taxable account)", "amount": inv["dividends"], "link": "#/finances/investments?acct=brokerage"} if inv["dividends"] else None,
               {"label": "Interest", "amount": inv["interest"], "link": "#/finances/investments?acct=brokerage"} if inv["interest"] else None,
               {"label": "Realized gains", "amount": r2((inv["st_gains"] or 0) + (inv["lt_gains"] or 0)), "link": "#/finances/investments"} if (inv["st_gains"] or inv["lt_gains"]) else None]
    sources = [x for x in sources if x]
    total_tax = fed_tax + il_tax + (dc["tax"] if dc else 0) + (w["ss"] or 0) + (w["medicare"] or 0)
    paid_total = fed_paid + il_paid + (dc["paid"] if dc else 0)
    return {"year": year, "settings": s, "income": r2(agi), "sources": sources, "wages": w, "trust": tr, "investment": inv,
            "charitable": ch, "federal": fed, "illinois": il, "dc": dc,
            "fica": {"ss": w["ss"], "medicare": w["medicare"]}, "estimated": est_out, "roth": roth,
            "total_tax": r2(total_tax), "income_tax": r2(fed_tax + il_tax + (dc["tax"] if dc else 0)),
            "settle": r2((fed_paid - fed_tax) + (il_paid - il_tax) + ((dc["paid"] - dc["tax"]) if dc else 0)),
            "effective_pct": r2(total_tax / agi * 100) if agi else 0, "filing_deadline": f"{year + 1}-04-15",
            "retirement": retirement_projection(s, fed, dc),
            "disclaimer": "Estimate built from your statements, paychecks and settings, using 2026 tax-law figures with sources noted. Not tax advice."}


def retirement_projection(s, fed, dc):
    """What the Roth and the pre-tax IRA would cost in tax if withdrawn, now (as a DC resident) and later (Illinois)."""
    out = []
    by = s.get("birth_year")
    age = date.today().year - by if by else None
    fed_m = fed["marginal"] or 0.22
    dc_m = (dc or {}).get("marginal") or 0
    for a in L.accounts():
        if a["type"] == "roth_ira":
            r = P.roth_detail(a["id"], s)
            v = r["value"] or 0
            contrib = r["contributions"] or 0
            conv = sum(c["amount"] for c in r["conversions"])
            earn = max(0.0, v - contrib - conv)
            today = date.today().isoformat()
            conv_pen = sum(c["amount"] for c in r["conversions"] if c["penalty_free_from"] > today) * 0.10 if (age is None or age < 59.5) else 0
            out.append({"account": a["id"], "name": a["name"], "kind": "roth", "value": r2(v),
                        "pieces": [{"label": "Your contributions", "amount": r2(contrib), "tax_now": 0.0, "note": "Always come out first, tax- and penalty-free."},
                                   {"label": "Converted (Genpact 401(k), 2025)" if conv else "Conversions", "amount": r2(conv), "tax_now": r2(conv_pen),
                                    "note": "Already taxed in the year converted. 10% penalty if withdrawn within 5 years of the conversion while under 59½."},
                                   {"label": "Earnings", "amount": r2(earn), "tax_now": r2(earn * (fed_m + dc_m + 0.10)),
                                    "note": "Taxable + 10% penalty if withdrawn now; tax-free once qualified (59½ and 5 years)."}],
                        "qualified_tax": 0.0, "early_tax": r2(conv_pen + earn * (fed_m + dc_m + 0.10)),
                        "assumptions": f"Federal {fed_m * 100:.0f}% + DC {dc_m * 100:.2f}% marginal rates this year, plus the 10% early-withdrawal penalty."
                                       + ("" if by else " Add your birth year in tax settings to apply the age rules exactly.")})
        elif a.get("tax") == "pre_tax":
            v = (L.latest_snapshots().get(a["id"]) or {}).get("total") or 0
            out.append({"account": a["id"], "name": a["name"], "kind": "pre_tax", "value": r2(v),
                        "pieces": [{"label": "Whole balance (pre-tax)", "amount": r2(v), "tax_now": r2(v * (fed_m + dc_m + 0.10)),
                                    "note": "Every dollar is ordinary income when withdrawn."}],
                        "scenarios": [{"label": "Withdraw now (DC resident, under 59½)", "federal": r2(v * fed_m), "state": r2(v * dc_m), "penalty": r2(v * 0.10)},
                                      {"label": "In retirement as an Illinois resident", "federal": r2(v * 0.12), "state": 0.0, "penalty": 0.0,
                                       "note": "Illinois subtracts IRA and qualified-plan distributions; federal assumed at 12%."}],
                        "early_tax": r2(v * (fed_m + dc_m + 0.10))})
    return out

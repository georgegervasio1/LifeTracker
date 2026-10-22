"""Earnings: a job's paychecks laid out one by one, with the withholding math and the year-end tax picture.

Data (per user, under users/{uid}/):
  earnings/settings   -> employer, salary, dates, pay schedule, W-4 / state withholding inputs
  paychecks/{id}      -> actual pay stubs (optional); they replace the projection for that pay date

Federal withholding follows IRS Pub 15-T (2026) Worksheet 1A, the percentage method for automated payroll
with a 2020-or-later Form W-4. The year-end estimate uses the 2026 brackets. Everything is an estimate:
the user's pay stubs and tax return are the source of truth.
"""
import datetime as dt
import re
from flask import Blueprint, jsonify, request

bp = Blueprint("earnings", __name__)
db = firestore = require_owner = user_ref = None

# ---------------------------------------------------------------- 2026 tax tables (sources in comments)
TAX = {
    2026: {
        # IRS Rev. Proc. / IR-2025 inflation adjustments incl. One Big Beautiful Bill: standard deduction + brackets
        "std_deduction": {"single": 16100, "mfj": 32200},
        "brackets": {
            "single": [(0, .10), (12400, .12), (50400, .22), (105700, .24), (201775, .32), (256225, .35), (640600, .37)],
            "mfj": [(0, .10), (24800, .12), (100800, .22), (211400, .24), (403550, .32), (512450, .35), (768700, .37)],
        },
        # Pub 15-T (2026) Worksheet 1A line 1g, and the STANDARD annual percentage-method schedules:
        # (at least, tentative amount, rate) — MFJ rows as printed; single rows are the 2026 brackets shifted by
        # the standard deduction less the line-1g amount ($16,100 - $8,600 = $7,500), same construction as MFJ.
        "w4_1g": {"single": 8600, "mfj": 12900},
        "wh_table": {
            "single": [(0, 0, 0), (7500, 0, .10), (19900, 1240, .12), (57900, 5800, .22), (113200, 17966, .24),
                       (209275, 41024, .32), (263725, 58448, .35), (648100, 192979.25, .37)],
            "mfj": [(0, 0, 0), (19300, 0, .10), (44100, 2480, .12), (120100, 11600, .22), (230700, 35932, .24),
                    (422850, 82048, .32), (531750, 116896, .35), (788000, 206583.50, .37)],
        },
        "ss_rate": .062, "ss_wage_base": 184500,              # SSA 2026 wage base
        "medicare_rate": .0145, "addl_medicare_rate": .009, "addl_medicare_threshold": 200000,
        # Illinois IL-700-T (2026): flat 4.95%; $2,925 per line-1 allowance, $1,000 per line-2 allowance
        "il_rate": .0495, "il_allow1": 2925, "il_allow2": 1000, "il_exemption": 2925,
        # District of Columbia 2026 single brackets (taxable income)
        "dc_brackets": [(0, .04), (10000, .06), (40000, .065), (60000, .085), (250000, .0925), (500000, .0975), (1000000, .1075)],
    },
}
PERIODS = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}
WORKDAYS_PER_YEAR = 260          # partial periods are paid at salary / 260 per workday

# The owner's past summer job, fixed so there's nothing to enter. The employer's name and the actual
# deposits come from the bank feed at runtime (payroll deposits in the job's window), not from this file.
SEED = {"role": "Summer Associate", "annual_salary": 225000, "start": "2026-05-26", "end": "2026-07-31",
        "frequency": "semimonthly", "work_state": "IL", "resident_state": "DC", "archived": True}
DEFAULT_SETTINGS = {
    "employer": "", "role": "", "annual_salary": None, "start": None, "end": None,
    "frequency": "weekly", "pay_lag_days": 0,
    "filing_status": "single", "w4_step2": False, "w4_credits": 0, "w4_other_income": 0, "w4_deductions": 0, "w4_extra": 0,
    "work_state": "IL", "resident_state": "DC", "il_allow1": 1, "il_allow2": 0,
    "dc_withholding": 0, "dc_std_deduction": None, "other_income": 0, "archived": False,
}


def init(app, _db, _firestore, _require_owner, _user_ref):
    global db, firestore, require_owner, user_ref
    db, firestore, require_owner, user_ref = _db, _firestore, _require_owner, _user_ref
    _register_routes()
    app.register_blueprint(bp)


def r2(x):
    return round(x + 1e-9, 2)


def _d(s):
    return dt.date.fromisoformat(s) if isinstance(s, str) else s


# ---------------------------------------------------------------- pay periods
def _workdays(a, b):
    """Mon-Fri days in [a, b]."""
    if b < a:
        return 0
    n, d = 0, a
    while d <= b:
        n += d.weekday() < 5
        d += dt.timedelta(days=1)
    return n


def pay_periods(start, end, frequency):
    """Pay periods overlapping [start, end] as (period_start, period_end)."""
    start, end = _d(start), _d(end)
    out = []
    if frequency in ("weekly", "biweekly"):
        step = 7 if frequency == "weekly" else 14
        p = start - dt.timedelta(days=start.weekday())       # periods run Monday-Sunday from the first week
        while p <= end:
            out.append((p, p + dt.timedelta(days=step - 1)))
            p += dt.timedelta(days=step)
    else:
        y, m = start.year, start.month
        while dt.date(y, m, 1) <= end:
            last = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
            halves = [(dt.date(y, m, 1), dt.date(y, m, 15)), (dt.date(y, m, 16), last)] if frequency == "semimonthly" else [(dt.date(y, m, 1), last)]
            out += [h for h in halves if h[1] >= start and h[0] <= end]
            y, m = y + (m == 12), m % 12 + 1
    return out


def _bracket_tax(income, brackets):
    tax, fill = 0.0, []
    for i, (lo, rate) in enumerate(brackets):
        hi = brackets[i + 1][0] if i + 1 < len(brackets) else None
        amt = max(0.0, (min(income, hi) if hi is not None else income) - lo)
        fill.append({"from": lo, "to": hi, "rate": rate, "amount": r2(amt), "tax": r2(amt * rate)})
        tax += amt * rate
    return tax, fill


# ---------------------------------------------------------------- withholding (per paycheck)
def federal_withholding(wages, pp, s, T):
    fs = "mfj" if s.get("filing_status") == "mfj" else "single"
    l1c = wages * pp
    l1e = l1c + float(s.get("w4_other_income") or 0)
    l1g = 0 if s.get("w4_step2") else T["w4_1g"][fs]
    l1h = max(0.0, l1e - float(s.get("w4_deductions") or 0) - l1g)
    row = max((r for r in T["wh_table"][fs] if l1h >= r[0]), key=lambda r: r[0])
    annual = row[1] + row[2] * (l1h - row[0])
    per = max(0.0, annual / pp - float(s.get("w4_credits") or 0) / pp) + float(s.get("w4_extra") or 0)
    return r2(per), {"annualized_wages": r2(l1c), "line_1g": l1g, "adjusted_annual": r2(l1h),
                     "row_from": row[0], "row_base": row[1], "row_rate": row[2], "annual_withholding": r2(annual),
                     "pay_periods": pp}


def il_withholding(wages, pp, s, T):
    allow = int(s.get("il_allow1") or 0) * T["il_allow1"] + int(s.get("il_allow2") or 0) * T["il_allow2"]
    base = max(0.0, wages - allow / pp)
    return r2(base * T["il_rate"]), {"allowance_total": allow, "allowance_per_period": r2(allow / pp), "taxable": r2(base)}


# ---------------------------------------------------------------- the engine
def match_deposits(checks, deposits):
    """Attach the bank deposit for each paycheck (closest payroll deposit within 4 days)."""
    pool = sorted(deposits, key=lambda d: d["date"])
    used = set()
    for c in checks:
        pd = _d(c["pay_date"])
        best = None
        for i, d in enumerate(pool):
            gap = abs((_d(d["date"]) - pd).days)
            if i not in used and gap <= 4 and (best is None or gap < best[1]):
                best = (i, gap)
        if best:
            used.add(best[0]); d = pool[best[0]]
            c["deposit"] = d["amount"]; c["deposit_date"] = d["date"]
            c["other_deductions"] = r2(c["net"] - d["amount"])
    return [d for i, d in enumerate(pool) if i not in used]


def compute(settings, actuals=(), deposits=(), reimbursements=()):
    s = {**DEFAULT_SETTINGS, **(settings or {})}
    missing = [k for k in ("annual_salary", "start", "end") if not s.get(k)]
    if missing:
        return {"settings": s, "ready": False, "missing": missing}
    start, end = _d(s["start"]), _d(s["end"])
    year = start.year
    T = TAX.get(year)
    if not T:
        return {"settings": s, "ready": False, "missing": [], "error": f"Tax tables for {year} aren't loaded yet."}
    pp = PERIODS[s["frequency"]]
    salary = float(s["annual_salary"])
    per_full = salary / pp
    act = sorted(actuals, key=lambda a: a.get("pay_date") or "")
    used = set()

    checks, ytd = [], {"gross": 0.0, "ss_wages": 0.0, "med_wages": 0.0}
    for ps, pe in pay_periods(start, end, s["frequency"]):
        worked = _workdays(max(ps, start), min(pe, end))
        full = _workdays(ps, pe)
        gross = r2(per_full) if worked == full else r2(salary / WORKDAYS_PER_YEAR * worked)
        pay_date = pe + dt.timedelta(days=int(s.get("pay_lag_days") or 0))
        if s["frequency"] in ("semimonthly", "monthly"):
            while pay_date.weekday() >= 5:
                pay_date -= dt.timedelta(days=1)
        fed, fed_math = federal_withholding(gross, pp, s, T)
        il, il_math = il_withholding(gross, pp, s, T) if s.get("work_state") == "IL" else (0.0, None)
        ss_room = max(0.0, T["ss_wage_base"] - ytd["ss_wages"])
        ss = r2(min(gross, ss_room) * T["ss_rate"])
        over = max(0.0, ytd["med_wages"] + gross - T["addl_medicare_threshold"]) - max(0.0, ytd["med_wages"] - T["addl_medicare_threshold"])
        med = r2(gross * T["medicare_rate"] + over * T["addl_medicare_rate"])
        dc = r2(float(s.get("dc_withholding") or 0) * worked / full) if full else 0.0
        c = {"period_start": ps.isoformat(), "period_end": pe.isoformat(), "pay_date": pay_date.isoformat(),
             "days_worked": worked, "days_in_period": full, "gross": gross, "federal": fed, "state": il, "dc": dc,
             "ss": ss, "medicare": med, "source": "projected",
             "math": {"per_full_period": r2(per_full), "federal": fed_math, "il": il_math,
                      "ss_room_before": r2(ss_room), "addl_medicare_wages": r2(over)}}
        # an actual pay stub within 3 days of the projected pay date replaces the projection
        for i, a in enumerate(act):
            if i not in used and a.get("pay_date") and abs((_d(a["pay_date"]) - pay_date).days) <= 3:
                used.add(i)
                c.update({k: float(a[k]) for k in ("gross", "federal", "state", "ss", "medicare", "dc") if a.get(k) not in (None, "")})
                c.update({"source": "actual", "actual_id": a.get("id"), "pay_date": a["pay_date"]})
                if a.get("net") not in (None, ""):
                    c["net_reported"] = float(a["net"])
                break
        c["net"] = r2(c["gross"] - c["federal"] - c["state"] - c["dc"] - c["ss"] - c["medicare"])
        ytd["gross"] += c["gross"]; ytd["ss_wages"] += min(c["gross"], ss_room); ytd["med_wages"] += c["gross"]
        if c["gross"] > 0:
            checks.append(c)
    for i, a in enumerate(act):            # stubs that didn't line up with a projected date still count
        if i not in used:
            c = {"pay_date": a["pay_date"], "period_start": a.get("period_start"), "period_end": a.get("period_end"),
                 "source": "actual", "actual_id": a.get("id"), "math": None,
                 **{k: float(a.get(k) or 0) for k in ("gross", "federal", "state", "ss", "medicare", "dc")}}
            c["net"] = r2(c["gross"] - c["federal"] - c["state"] - c["dc"] - c["ss"] - c["medicare"])
            checks.append(c)
    checks.sort(key=lambda c: c["pay_date"])
    cum = 0.0
    for c in checks:
        cum += c["net"]; c["cum_net"] = r2(cum)

    unmatched = match_deposits(checks, deposits) if deposits else []
    tot = {k: r2(sum(c[k] for c in checks)) for k in ("gross", "federal", "state", "dc", "ss", "medicare", "net")}
    dep = [c for c in checks if "deposit" in c]
    tot["deposited"] = r2(sum(c["deposit"] for c in dep)) if dep else None
    tot["deposit_checks"] = len(dep)
    tot["other_deductions"] = r2(sum(c["other_deductions"] for c in dep)) if dep else 0.0
    tot["reimbursements"] = r2(sum(r["amount"] for r in reimbursements))
    tot["checks"] = len(checks)
    tot["actual_checks"] = sum(c["source"] == "actual" for c in checks)
    return {"settings": s, "ready": True, "year": year, "checks": checks, "totals": tot,
            "reimbursements": list(reimbursements), "unmatched_deposits": unmatched,
            "year_end": year_end(s, tot, T), "tables": {"federal": T["brackets"]["mfj" if s["filing_status"] == "mfj" else "single"],
                                                       "dc": T["dc_brackets"], "il_rate": T["il_rate"]}}


def year_end(s, tot, T):
    """Estimated tax for the whole year vs. what was withheld."""
    fs = "mfj" if s.get("filing_status") == "mfj" else "single"
    wages, other = tot["gross"], float(s.get("other_income") or 0)
    agi = wages + other
    std = T["std_deduction"][fs]
    fed_taxable = max(0.0, agi - std)
    fed_tax, fed_fill = _bracket_tax(fed_taxable, T["brackets"][fs])
    marginal = next((b["rate"] for b in reversed(fed_fill) if b["amount"] > 0), 0)
    # what payroll assumed: one full-time paycheck annualized
    pp = PERIODS[s["frequency"]]
    assumed = max(0.0, float(s["annual_salary"]) - T["w4_1g"][fs])
    assumed_row = max((r for r in T["wh_table"][fs] if assumed >= r[0]), key=lambda r: r[0])
    out = {"agi": r2(agi), "wages": r2(wages), "other_income": r2(other),
           "federal": {"std_deduction": std, "taxable": r2(fed_taxable), "tax": r2(fed_tax), "fill": fed_fill,
                       "withheld": tot["federal"], "refund": r2(tot["federal"] - fed_tax), "marginal": marginal,
                       "effective": r2(fed_tax / agi * 100) if agi else 0,
                       "payroll_assumed_income": float(s["annual_salary"]), "payroll_assumed_marginal": assumed_row[2]},
           "fica": {"ss": tot["ss"], "medicare": tot["medicare"], "note": "Social Security and Medicare aren't refunded; they're owed on every dollar of wages."}}
    if s.get("work_state") == "IL":
        il_wages = wages
        ratio = il_wages / agi if agi else 0
        il_tax = max(0.0, (il_wages - T["il_exemption"] * ratio) * T["il_rate"])
        out["il"] = {"wages": r2(il_wages), "exemption": T["il_exemption"], "ratio": r2(ratio * 100),
                     "tax": r2(il_tax), "withheld": tot["state"], "refund": r2(tot["state"] - il_tax),
                     "resident": s.get("resident_state") == "IL"}
    if s.get("resident_state") == "DC":
        dc_std = float(s["dc_std_deduction"]) if s.get("dc_std_deduction") not in (None, "") else std
        dc_taxable = max(0.0, agi - dc_std)
        dc_tax, dc_fill = _bracket_tax(dc_taxable, T["dc_brackets"])
        il_tax = out.get("il", {}).get("tax", 0)
        credit_cap = dc_tax * (out.get("il", {}).get("wages", 0) / agi) if agi else 0
        credit = min(il_tax, credit_cap)
        out["dc"] = {"std_deduction": r2(dc_std), "std_assumed": s.get("dc_std_deduction") in (None, ""),
                     "taxable": r2(dc_taxable), "tax": r2(dc_tax), "fill": dc_fill,
                     "credit_other_state": r2(credit), "credit_cap": r2(credit_cap),
                     "after_credit": r2(dc_tax - credit), "withheld": tot["dc"], "due": r2(dc_tax - credit - tot["dc"])}
    net_refund = out["federal"]["refund"] + out.get("il", {}).get("refund", 0) - out.get("dc", {}).get("due", 0)
    out["net_settle"] = r2(net_refund)                    # + means money back overall
    out["true_take_home"] = r2(wages - fed_tax - out["fica"]["ss"] - out["fica"]["medicare"]
                               - out.get("il", {}).get("tax", 0) - out.get("dc", {}).get("after_credit", 0))
    return out


# ---------------------------------------------------------------- whole-year tax picture
def tax_picture(job):
    """Concise view of the year built on the job's year-end estimate: where each dollar went, the marginal
    rate on the next dollar, the bracket ladder and what's left to settle."""
    if not job.get("ready"):
        return {"ready": False}
    s, T, Y, tot = job["settings"], TAX[job["year"]], job["year_end"], job["totals"]
    income = Y["agi"]
    il_tax = Y.get("il", {}).get("tax", 0)
    dc_tax = Y.get("dc", {}).get("after_credit", 0)
    parts = [("federal", "Federal income tax", Y["federal"]["tax"]), ("ss", "Social Security", Y["fica"]["ss"]),
             ("medicare", "Medicare", Y["fica"]["medicare"])]
    if "il" in Y:
        parts.append(("il", "Illinois", il_tax))
    if "dc" in Y:
        parts.append(("dc", "DC (after credit)", dc_tax))
    taxes = sum(a for _, _, a in parts)
    parts.append(("keep", "You keep", income - taxes))
    breakdown = [{"key": k, "label": l, "amount": r2(a), "pct": r2(a / income * 100) if income else 0} for k, l, a in parts]

    # marginal: re-run the year with $100 more wages
    bump = 100.0
    tot2 = {**tot, "gross": tot["gross"] + bump}
    Y2 = year_end(s, tot2, T)
    ss_room = tot["gross"] < T["ss_wage_base"]
    marg = {"federal": r2((Y2["federal"]["tax"] - Y["federal"]["tax"]) / bump * 100),
            "ss": r2(T["ss_rate"] * 100) if ss_room else 0,
            "medicare": r2((T["medicare_rate"] + (T["addl_medicare_rate"] if tot["gross"] > T["addl_medicare_threshold"] else 0)) * 100),
            "state": r2(((Y2.get("il", {}).get("tax", 0) + Y2.get("dc", {}).get("after_credit", 0)) - (il_tax + dc_tax)) / bump * 100)}
    marg["total"] = r2(sum(marg.values()))
    settle = [{"tax": "Federal", "owed": Y["federal"]["tax"], "paid": Y["federal"]["withheld"], "balance": Y["federal"]["refund"]}]
    if "il" in Y:
        settle.append({"tax": "Illinois", "owed": il_tax, "paid": Y["il"]["withheld"], "balance": Y["il"]["refund"]})
    if "dc" in Y:
        settle.append({"tax": "DC", "owed": Y["dc"]["after_credit"], "paid": Y["dc"]["withheld"], "balance": r2(-Y["dc"]["due"])})
    settle.append({"tax": "Social Security & Medicare", "owed": r2(Y["fica"]["ss"] + Y["fica"]["medicare"]),
                   "paid": r2(tot["ss"] + tot["medicare"]), "balance": 0.0})
    return {"ready": True, "year": job["year"], "income": r2(income), "taxes": r2(taxes), "breakdown": breakdown,
            "effective_total": r2(taxes / income * 100) if income else 0, "marginal": marg,
            "federal": {k: Y["federal"][k] for k in ("std_deduction", "taxable", "tax", "fill", "marginal", "effective")},
            "settle": settle, "net_settle": Y["net_settle"], "filing_deadline": f"{job['year'] + 1}-04-15",
            "sources": [{"label": " · ".join(x for x in (s.get("employer"), s.get("role")) if x) or "Wages", "amount": tot["gross"]}]
                       + ([{"label": "Other income", "amount": Y["other_income"]}] if Y["other_income"] else [])}


def bank_tax_activity():
    """Tax payments and refunds that show up in the bank feed (IRS, state tax agencies)."""
    out = []
    for d in user_ref().collection("transactions").where("date", ">=", "2025-01-01").stream():
        t = d.to_dict(); m = (t.get("merchant") or "")
        if re.search(r"\birs\b|usataxpymt|treas tax|tax ?rfd|taxrfd|tax refund|dept of revenue|state tax|comptroller", m, re.I) or t.get("category") == "Taxes And Government":
            out.append({"date": t["date"], "merchant": m.split(" PPD")[0].strip(), "amount": r2(-t["amount"]),
                        "kind": "refund" if t["amount"] < 0 else "payment"})
    return sorted(out, key=lambda x: x["date"], reverse=True)


# ---------------------------------------------------------------- storage + routes
def _settings_ref():
    return user_ref().collection("earnings").document("settings")


def clean_settings(b):
    out = {}
    for k, default in DEFAULT_SETTINGS.items():
        if k not in b:
            continue
        v = b[k]
        if k in ("employer", "role"):
            v = str(v or "").strip()[:80]
        elif k in ("start", "end"):
            v = str(v or "").strip() or None
            if v:
                dt.date.fromisoformat(v)
        elif k == "frequency":
            if v not in PERIODS:
                raise ValueError(f"frequency must be one of {list(PERIODS)}")
        elif k == "filing_status":
            if v not in ("single", "mfj"):
                raise ValueError("filing status must be single or mfj")
        elif k in ("work_state", "resident_state"):
            v = str(v or "").upper()[:2]
        elif k in ("w4_step2", "archived"):
            v = bool(v)
        elif k in ("il_allow1", "il_allow2", "pay_lag_days"):
            v = int(v or 0)
            if not 0 <= v <= 30:
                raise ValueError(f"{k} must be 0-30")
        else:                                  # money
            v = None if v in (None, "") else float(v)
            if v is not None and not 0 <= v <= 10_000_000:
                raise ValueError(f"{k} is out of range")
        out[k] = v
    if out.get("start") and out.get("end") and out["end"] < out["start"]:
        raise ValueError("end date is before start date")
    return out


def bank_payroll(start, end):
    """Payroll deposits (and the same employer's other deposits, e.g. expense reimbursements) from the
    bank feed between the first day and three weeks after the last. Money in is negative in the feed."""
    lo = (_d(start) - dt.timedelta(days=7)).isoformat()
    hi = (_d(end) + dt.timedelta(days=21)).isoformat()
    txs = [d.to_dict() for d in user_ref().collection("transactions").where("date", ">=", lo).where("date", "<=", hi).stream()]
    inflow = [t for t in txs if (t.get("amount") or 0) < 0]
    pay = [t for t in inflow if "payroll" in (t.get("merchant") or "").lower()]
    if not pay:
        return None, [], []
    employer = re.split(r"\s+payroll\b", pay[0]["merchant"], flags=re.I)[0].strip()
    key = employer.lower()[:12]
    other = [t for t in inflow if t not in pay and (t.get("merchant") or "").lower().startswith(key)]
    as_dep = lambda t: {"date": t["date"], "amount": r2(-t["amount"]), "merchant": t.get("merchant")}
    return employer, [as_dep(t) for t in pay], [as_dep(t) for t in other]


def state():
    snap = _settings_ref().get()
    settings = snap.to_dict() if snap.exists else dict(SEED)
    actuals = [d.to_dict() | {"id": d.id} for d in user_ref().collection("paychecks").stream()]
    employer, deposits, reimb = (None, [], [])
    s = {**DEFAULT_SETTINGS, **settings}
    if s.get("start") and s.get("end"):
        employer, deposits, reimb = bank_payroll(s["start"], s["end"])
    if employer and not settings.get("employer"):
        settings = {**settings, "employer": employer}
    return compute(settings, actuals, deposits, reimb) | {"actuals": actuals}


def _register_routes():
    @bp.get("/api/earnings")
    @require_owner
    def get_earnings():
        return jsonify(state())

    @bp.get("/api/taxes")
    @require_owner
    def get_taxes():
        return jsonify(tax_picture(state()) | {"bank_activity": bank_tax_activity()})

    @bp.put("/api/earnings/settings")
    @require_owner
    def put_settings():
        try:
            data = clean_settings(request.get_json(force=True) or {})
            snap = _settings_ref().get()
            merged = {**(snap.to_dict() if snap.exists else {}), **data}
            if merged.get("start") and merged.get("end") and merged["end"] < merged["start"]:
                raise ValueError("end date is before start date")
        except ValueError as e:
            return jsonify(error=str(e)), 400
        _settings_ref().set(data, merge=True)
        return jsonify(state())

    @bp.post("/api/earnings/paychecks")
    @require_owner
    def add_paycheck():
        b = request.get_json(force=True) or {}
        try:
            dt.date.fromisoformat(b.get("pay_date") or "")
            data = {"pay_date": b["pay_date"]}
            for k in ("gross", "federal", "state", "dc", "ss", "medicare", "net"):
                if b.get(k) not in (None, ""):
                    data[k] = float(b[k])
            for k in ("period_start", "period_end"):
                if b.get(k):
                    dt.date.fromisoformat(b[k]); data[k] = b[k]
        except (ValueError, TypeError):
            return jsonify(error="pay date and amounts must be valid"), 400
        if "gross" not in data:
            return jsonify(error="gross pay is required"), 400
        user_ref().collection("paychecks").document().set(data)
        return jsonify(state())

    @bp.delete("/api/earnings/paychecks/<pid>")
    @require_owner
    def delete_paycheck(pid):
        user_ref().collection("paychecks").document(pid).delete()
        return jsonify(state())

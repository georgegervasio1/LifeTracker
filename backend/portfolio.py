"""Investments and net worth, computed from the ledger (see ledger.py). Read-only.

Returns use the statements' own figures: each statement period's return is a modified Dietz return
(market change + dividends over the starting value plus time-weighted deposits), chained into a
time-weighted return. Months before the first statement come from the statement's year-to-date block.
The S&P 500 comparison uses the index returns UBS prints on each statement until a market feed exists.
"""
from datetime import date, timedelta

import ledger as L

# Reference data for funds you hold. Expense ratios are the funds' published figures (source + date);
# classification is for allocation look-through. Unknown tickers are shown as "Unclassified".
SECURITIES = {
    "SPY":  {"name": "SPDR S&P 500 ETF Trust", "asset": "US stocks", "style": "US large-cap blend", "sector": None,
             "er": 0.09, "er_src": "composer.trade SPY vs XLK comparison", "er_asof": "2026-10-22"},
    "XLK":  {"name": "Technology Select Sector SPDR", "asset": "US stocks", "style": "Sector: technology", "sector": "Technology",
             "er": 0.08, "er_src": "ssga.com fund page (as of Oct 22, 2026)", "er_asof": "2026-10-22"},
    "XLE":  {"name": "Energy Select Sector SPDR", "asset": "US stocks", "style": "Sector: energy", "sector": "Energy",
             "er": 0.08, "er_src": "marketxls.com XLE vs XLK", "er_asof": "2026-10-22"},
    "VUG":  {"name": "Vanguard Growth ETF", "asset": "US stocks", "style": "US large-cap growth", "sector": None,
             "er": 0.04, "er_src": "chartrow.com VOO vs VUG", "er_asof": "2026-10-22"},
    "VIOO": {"name": "Vanguard S&P Small-Cap 600 ETF", "asset": "US stocks", "style": "US small-cap", "sector": None,
             "er": 0.07, "er_src": "stockanalysis.com VIOO", "er_asof": "2026-10-22"},
    "IVOO": {"name": "Vanguard S&P Mid-Cap 400 ETF", "asset": "US stocks", "style": "US mid-cap", "sector": None,
             "er": 0.07, "er_src": "tradingview.com IVOO profile", "er_asof": "2026-10-22"},
}
ROTH_LIMITS = {2023: 6500, 2024: 7000, 2025: 7000, 2026: 7500}       # under 50; 2026: Vanguard, Roth IRA income limits
ROTH_PHASEOUT_SINGLE = {2026: (153000, 168000)}                      # MAGI; Vanguard, Roth IRA income limits (2026)


def r2(x):
    return None if x is None else round(float(x) + 0.0, 2)


def _d(s):
    return date.fromisoformat(s[:10])


def investment_accounts():
    return [a for a in L.accounts() if a["group"] in ("investment", "retirement")]


# ---------------------------------------------------------------- returns
def _flows(acct, start, end):
    """External money in (+) / out (-) of an investment account between two dates (exclusive start)."""
    out = []
    for a in L.load_activity(start, end, account=acct):
        if a["date"] <= start:
            continue
        if a["type"] in ("deposit", "transfer_in", "trust_distribution", "contribution"):
            out.append((a["date"], a["amount"]))
        elif a["type"] in ("withdrawal", "transfer_out", "distribution"):
            out.append((a["date"], -abs(a["amount"])))
    return out


def period_returns(acct):
    rows = []
    sts = L.statements(acct)
    for st in sts:
        ch = st.get("change") or {}
        o, c = st.get("opening_value"), st.get("closing_value")
        if o is None or c is None:
            continue
        net = (ch.get("deposits") or 0) - (ch.get("withdrawals") or 0)
        days = max(1, (_d(st["closing_date"]) - _d(st["opening_date"])).days)
        flows = _flows(acct, st["opening_date"], st["closing_date"])
        if flows and abs(sum(f for _, f in flows) - net) < 0.01:
            weighted = sum(f * (_d(st["closing_date"]) - _d(dd)).days / days for dd, f in flows)
        else:
            weighted = net * 0.5
        gain = c - o - net
        base = o + weighted
        rows.append({"start": st["opening_date"], "end": st["closing_date"], "label": st.get("period_label"),
                     "opening": o, "closing": c, "net_deposits": r2(net), "gain": r2(gain),
                     "return_pct": r2(gain / base * 100) if base > 0 else None,
                     "dividends": ch.get("div_int"), "market_change": ch.get("market_change"),
                     "sp500_ytd": next((b["ytd"] for b in st.get("benchmarks") or [] if b["index"] == "S&P 500"), None),
                     "sp500_period": next((b["period"] for b in st.get("benchmarks") or [] if b["index"] == "S&P 500"), None),
                     "benchmark_month": st.get("benchmark_month"), "statement": st["id"]})
    # one row per period end (two statements can describe the same period; keep the longer one)
    uniq = {}
    for r in rows:
        if r["end"] not in uniq or r["start"] < uniq[r["end"]]["start"]:
            uniq[r["end"]] = r
    rows = sorted(uniq.values(), key=lambda r: r["end"])
    # drop periods fully inside another
    keep = []
    for r in rows:
        while keep and keep[-1]["start"] >= r["start"]:
            keep.pop()
        if keep and r["start"] < keep[-1]["end"]:
            continue
        keep.append(r)
    return keep


def ytd_series(acct):
    """Year-to-date return at each statement end vs the S&P 500's year-to-date return printed alongside."""
    periods = period_returns(acct)
    if not periods:
        return {"points": [], "ytd_pct": None, "sp500_ytd": None}
    sts = {s["closing_date"]: s for s in L.statements(acct)}
    year = periods[-1]["end"][:4]
    periods = [p for p in periods if p["end"][:4] == year]
    if not periods:
        return {"points": [], "ytd_pct": None, "sp500_ytd": None}
    first = sts[periods[0]["end"]]
    ytd, ch = first.get("ytd") or {}, first.get("change") or {}
    pts, growth = [], 1.0
    start_val = ytd.get("start_value")
    if start_val:
        pre_dep = (ch.get("deposits_ytd") or 0) - (ch.get("deposits") or 0) - ((ch.get("withdrawals_ytd") or 0) - (ch.get("withdrawals") or 0))
        pre_gain = periods[0]["opening"] - start_val - pre_dep
        base = start_val + pre_dep * 0.5
        growth *= 1 + (pre_gain / base if base > 0 else 0)
        pts.append({"date": f"{year}-01-01", "ytd_pct": 0.0, "sp500_ytd": 0.0})
        sp_start = None
        # the opening of the first statement period: S&P YTD at that point isn't printed; estimate from the
        # first statement's YTD minus its own month
        p0 = periods[0]
        if p0["sp500_ytd"] is not None and p0["sp500_period"] is not None and p0["label"] and "-" not in p0["label"]:
            sp_start = ((1 + p0["sp500_ytd"] / 100) / (1 + p0["sp500_period"] / 100) - 1) * 100
        pts.append({"date": periods[0]["start"], "ytd_pct": r2((growth - 1) * 100), "sp500_ytd": r2(sp_start)})
    for p in periods:
        if p["return_pct"] is not None:
            growth *= 1 + p["return_pct"] / 100
        pts.append({"date": p["end"], "ytd_pct": r2((growth - 1) * 100), "sp500_ytd": p["sp500_ytd"]})
    return {"points": pts, "ytd_pct": pts[-1]["ytd_pct"], "sp500_ytd": periods[-1]["sp500_ytd"], "as_of": periods[-1]["end"],
            "approximate": True}


def value_series(acct=None):
    """Account value over time from statements + Plaid snapshots (one point per date)."""
    accts = [acct] if acct else [a["id"] for a in investment_accounts()]
    bal = L.balances()
    dates = sorted({d for a in accts for d, _, _ in bal.get(a, [])})
    out = []
    for d in dates:
        total, n = 0.0, 0
        for a in accts:
            prior = [b for b in bal.get(a, []) if b[0] <= d]
            if prior:
                total += prior[-1][1]; n += 1
        out.append({"date": d, "value": r2(total), "accounts": n})
    return out


# ---------------------------------------------------------------- holdings, allocation, fees, movers
def holdings(acct=None):
    snaps = L.latest_snapshots()
    accts = {a["id"]: a for a in investment_accounts()}
    rows, cash, as_of = [], 0.0, {}
    for aid, s in snaps.items():
        if aid not in accts or (acct and aid != acct):
            continue
        as_of[aid] = {"date": s["date"], "src": s["src"]}
        cash += s.get("cash") or 0
        for h in s.get("holdings") or []:
            rows.append({**h, "account": aid, "account_name": accts[aid]["name"], "as_of": s["date"]})
    if acct is None:      # combine the same security across accounts, keep the per-account rows underneath
        by = {}
        for h in rows:
            b = by.setdefault(h["symbol"], {"symbol": h["symbol"], "name": h["name"], "quantity": 0.0, "value": 0.0,
                                            "cost_basis": 0.0, "unrealized": 0.0, "eai": 0.0, "price": h.get("price"),
                                            "accounts": [], "lots": []})
            for k in ("quantity", "value", "cost_basis", "unrealized"):
                b[k] += h.get(k) or 0
            b["eai"] += h.get("eai") or 0
            b["accounts"].append({"account": h["account"], "name": h["account_name"], "quantity": h["quantity"], "value": h["value"]})
            b["lots"] += [{**l, "account": h["account"], "account_name": h["account_name"]} for l in h.get("lots") or []]
        rows = list(by.values())
    today = date.today()
    total = sum(h["value"] for h in rows) + cash
    for h in rows:
        info = SECURITIES.get(h["symbol"], {})
        h["weight_pct"] = r2(h["value"] / total * 100) if total else None
        h["yield_pct"] = h.get("yield_pct") or (r2(h["eai"] / h["value"] * 100) if h.get("eai") and h["value"] else None)
        h["expense_ratio"] = info.get("er")
        h["annual_fee"] = r2(h["value"] * info["er"] / 100) if info.get("er") is not None else None
        h["style"] = info.get("style", "Unclassified")
        for l in h.get("lots") or []:
            lt_on = _d(l["trade_date"]).replace(year=_d(l["trade_date"]).year + 1) + timedelta(days=1)
            l["long_term_on"] = lt_on.isoformat()
            l["term"] = "LT" if today >= lt_on else "ST"
            l["days_to_lt"] = max(0, (lt_on - today).days)
        for k in ("quantity", "value", "cost_basis", "unrealized", "eai"):
            if h.get(k) is not None:
                h[k] = r2(h[k]) if k != "quantity" else round(h[k], 6)
    rows.sort(key=lambda h: -(h["value"] or 0))
    return {"holdings": rows, "cash": r2(cash), "total": r2(total), "as_of": as_of}


def allocation(acct=None):
    h = holdings(acct)
    total = h["total"] or 0
    by_asset, by_style = {"Cash": h["cash"] or 0.0}, {"Cash": h["cash"] or 0.0}
    for x in h["holdings"]:
        info = SECURITIES.get(x["symbol"], {})
        by_asset[info.get("asset", "Unclassified")] = by_asset.get(info.get("asset", "Unclassified"), 0) + x["value"]
        by_style[info.get("style", "Unclassified")] = by_style.get(info.get("style", "Unclassified"), 0) + x["value"]
    fmt = lambda d: [{"label": k, "value": r2(v), "pct": r2(v / total * 100) if total else 0} for k, v in sorted(d.items(), key=lambda kv: -kv[1]) if v]
    flags = []
    cash_pct = (h["cash"] or 0) / total * 100 if total else 0
    if cash_pct >= 25:
        flags.append(f"{cash_pct:.0f}% of this is cash as of the latest statement.")
    pend = [a for a in L.load_activity(account=acct) if a.get("pending") and a["type"] == "buy"] if acct else \
           [a for a in L.load_activity() if a.get("pending") and a["type"] == "buy"]
    if pend:
        flags.append(f"${L.r2(-sum(a['amount'] for a in pend)):,.2f} of purchases were pending settlement on the statement date; "
                     "they'll show as holdings on the next statement or Plaid sync.")
    for k in ("Bonds", "International stocks", "Crypto"):
        by_asset.setdefault(k, 0.0)
    return {"by_asset": fmt(by_asset) + [{"label": k, "value": 0.0, "pct": 0.0} for k in ("Bonds", "International stocks", "Crypto") if not by_asset.get(k)],
            "by_style": fmt(by_style), "total": h["total"], "flags": flags}


def fees(acct=None):
    h = holdings(acct)
    rows = [{"symbol": x["symbol"], "name": SECURITIES.get(x["symbol"], {}).get("name") or x["name"], "value": x["value"],
             "expense_ratio": x["expense_ratio"], "annual_cost": x["annual_fee"],
             "source": SECURITIES.get(x["symbol"], {}).get("er_src"), "as_of": SECURITIES.get(x["symbol"], {}).get("er_asof")}
            for x in h["holdings"]]
    known = [r for r in rows if r["annual_cost"] is not None]
    acct_fees = [a for a in L.load_activity(account=acct) if a["type"] == "fee"]
    invested = sum(r["value"] for r in known)
    return {"funds": rows, "fund_cost_per_year": r2(sum(r["annual_cost"] for r in known)),
            "weighted_expense_ratio": round(sum(r["annual_cost"] for r in known) / invested * 100, 4) if invested else None,
            "unknown": [r["symbol"] for r in rows if r["annual_cost"] is None],
            "account_fees": [{"date": a["date"], "amount": a["amount"], "description": a.get("description"), "account": a["account"]} for a in acct_fees],
            "account_fees_total": r2(sum(abs(a["amount"]) for a in acct_fees)),
            "note": "Statements list no advisory or wrap fees; these accounts are brokerage/IRA accounts, not an advisory program."}


def movers(acct=None):
    """Price change per security between the two most recent snapshots (daily once Plaid syncs)."""
    snaps = [s for s in L.snapshots() if not acct or s["account"] == acct]
    dates = sorted({s["date"] for s in snaps})
    if len(dates) < 2:
        return {"items": [], "label": None}
    d1, d0 = dates[-1], dates[-2]
    price = lambda d: {h["symbol"]: h.get("price") for s in snaps if s["date"] == d for h in s.get("holdings") or [] if h.get("price")}
    p1, p0 = price(d1), price(d0)
    qty = {}
    for s in snaps:
        if s["date"] == d1:
            for h in s.get("holdings") or []:
                qty[h["symbol"]] = qty.get(h["symbol"], 0) + (h.get("quantity") or 0)
    items = [{"symbol": k, "from": p0[k], "to": p1[k], "change_pct": r2((p1[k] / p0[k] - 1) * 100),
              "change_value": r2((p1[k] - p0[k]) * qty.get(k, 0))} for k in p1 if k in p0 and p0[k]]
    items.sort(key=lambda x: -x["change_pct"])
    days = (_d(d1) - _d(d0)).days
    label = "Today" if days <= 3 else f"Since {_d(d0).strftime('%b %-d')} (statement to statement)"
    total_change = sum(i["change_value"] for i in items)
    return {"items": items, "label": label, "from": d0, "to": d1, "total_change": r2(total_change), "daily": days <= 3}


def dividends(acct=None, year=None):
    acts = [a for a in L.load_activity(account=acct) if a["type"] in ("dividend", "interest")]
    if year:
        acts = [a for a in acts if a["date"][:4] == str(year)]
    by_month = {}
    for a in acts:
        by_month[a["date"][:7]] = r2(by_month.get(a["date"][:7], 0) + a["amount"])
    h = holdings(acct)
    return {"received": r2(sum(a["amount"] for a in acts)), "by_month": dict(sorted(by_month.items())),
            "items": [{"date": a["date"], "symbol": a.get("symbol"), "amount": a["amount"], "type": a["type"], "account": a["account"]} for a in reversed(acts)][:60],
            "projected_annual": r2(sum(x.get("eai") or 0 for x in h["holdings"])),
            "note": "Received = dividends and interest on the statements (and Plaid once linked). Projected = UBS's estimated annual income."}


# ---------------------------------------------------------------- retirement detail
def roth_detail(acct, settings):
    sts = L.statements(acct)
    contrib = {}
    for st in sts:
        for y, rows in (st.get("contributions") or {}).items():
            for k, v in rows.items():
                if v:
                    contrib.setdefault(int(y), {})[k] = max(contrib.get(int(y), {}).get(k, 0), v)
    for d in L.col("tax_docs").where("form", "==", "5498").stream():
        f = d.to_dict()
        if f.get("account") == acct:
            y = int(f["year"])
            if f.get("roth_contributions"):
                contrib.setdefault(y, {})["non_deductible"] = max(contrib.get(y, {}).get("non_deductible", 0), f["roth_contributions"])
            if f.get("roth_conversion"):
                contrib.setdefault(y, {})["rollover_conversion"] = max(contrib.get(y, {}).get("rollover_conversion", 0), f["roth_conversion"])
    extra = {int(k): float(v) for k, v in (settings.get("roth_prior_contributions") or {}).items()}
    for y, v in extra.items():
        contrib.setdefault(y, {})["non_deductible"] = max(contrib.get(y, {}).get("non_deductible", 0), v)
    years = sorted(contrib)
    contributions = sum(v.get("non_deductible", 0) + v.get("regular", 0) for v in contrib.values())
    conversions = [{"year": y, "amount": v.get("rollover_conversion", 0) + v.get("roth_conversion", 0),
                    "penalty_free_from": f"{y + 5}-01-01"} for y, v in contrib.items() if v.get("rollover_conversion") or v.get("roth_conversion")]
    value = (L.latest_snapshots().get(acct) or {}).get("total")
    first_year = settings.get("roth_first_year") or (min(years) if years else None)
    basis = contributions + sum(c["amount"] for c in conversions)
    by_year = [{"year": y, "contributed": r2(v.get("non_deductible", 0) + v.get("regular", 0)),
                "limit": ROTH_LIMITS.get(y), "converted": r2(v.get("rollover_conversion", 0) + v.get("roth_conversion", 0))} for y, v in sorted(contrib.items())]
    return {"value": value, "contributions": r2(contributions), "conversions": conversions, "basis": r2(basis),
            "earnings": r2(value - basis) if value is not None else None, "by_year": by_year,
            "five_year_clock": {"first_year": first_year, "qualified_from": f"{first_year + 5}-01-01" if first_year else None,
                                "note": "Earnings come out tax-free only after the account's first contribution is 5 tax years old and you're 59½ (or another qualifying event). "
                                        "Each conversion also has its own 5-year clock for the 10% penalty if you're under 59½."},
            "contributions_known_from": years[0] if years else None,
            "missing_years_note": None if settings.get("roth_prior_contributions") else
                "Contributions before the statements on file aren't known yet; add them in Accounts & Imports (Roth contribution history) so the basis is complete."}


# ---------------------------------------------------------------- net worth
def net_worth():
    accts = L.accounts()
    bal = L.balances()
    today = date.today().isoformat()
    rows = []
    for a in accts:
        b = bal.get(a["id"], [])
        last = b[-1] if b else None
        val = last[1] if last else None
        rows.append({"account": a["id"], "name": a["name"], "type": a["type"], "type_label": a["type_label"], "group": a["group"],
                     "tax": a.get("tax"), "liability": a["liability"], "balance": val, "as_of": last[0] if last else None,
                     "src": last[2] if last else None,
                     "stale_days": (date.today() - _d(last[0])).days if last else None})
    assets = sum(r["balance"] for r in rows if r["balance"] is not None and not r["liability"])
    debts = sum(abs(r["balance"]) for r in rows if r["balance"] is not None and r["liability"])
    by_type = {}
    for r in rows:
        if r["balance"] is None:
            continue
        k = "Debt" if r["liability"] else {"investment": "Taxable investments", "retirement": "Tax-advantaged",
                                            "cash": "Cash"}.get(r["group"], "Other")
        by_type[k] = by_type.get(k, 0) + (-abs(r["balance"]) if r["liability"] else r["balance"])

    # month-end history (last known balance of each account, carried forward)
    all_dates = sorted({d for v in bal.values() for d, _, _ in v})
    hist = []
    if all_dates:
        cur = _d(all_dates[0]).replace(day=1)
        end = date.today()
        while cur <= end:
            nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
            me = min(nxt - timedelta(days=1), end).isoformat()
            tot, known = 0.0, 0
            for a in accts:
                prior = [x for x in bal.get(a["id"], []) if x[0] <= me]
                if prior:
                    v = prior[-1][1]
                    tot += -abs(v) if a["liability"] else v
                    known += 1
            hist.append({"date": me, "net_worth": r2(tot), "accounts": known})
            cur = nxt
    # what moved it: market change + dividends from statements vs everything else
    moves = []
    for i in range(1, len(hist)):
        a, b = hist[i - 1], hist[i]
        mkt = 0.0
        for st in L.statements():
            if a["date"] < st["closing_date"] <= b["date"]:
                ch = st.get("change") or {}
                mkt += (ch.get("market_change") or 0) + (ch.get("div_int") or 0)
        new_money = sum(x["amount"] for x in L.load_activity(a["date"], b["date"]) if x["date"] > a["date"] and x["type"] == "trust_distribution")
        # accounts whose first balance lands this month aren't a change in wealth, just newly tracked
        added = 0.0
        for acc in accts:
            pts = bal.get(acc["id"], [])
            if pts and a["date"] < pts[0][0] <= b["date"]:
                added += -abs(pts[0][1]) if acc["liability"] else pts[0][1]
        change = b["net_worth"] - a["net_worth"]
        moves.append({"month": b["date"][:7], "change": r2(change), "investment_returns": r2(mkt), "new_money": r2(new_money),
                      "newly_tracked": r2(added), "saving_and_spending": r2(change - mkt - new_money - added)})
    missing = [r["name"] for r in rows if r["balance"] is None]
    return {"assets": r2(assets), "liabilities": r2(debts), "net_worth": r2(assets - debts), "accounts": rows,
            "by_type": [{"label": k, "value": r2(v)} for k, v in sorted(by_type.items(), key=lambda kv: -abs(kv[1]))],
            "history": hist, "changes": moves[-12:], "missing_balances": missing, "as_of": today}


# ---------------------------------------------------------------- reconciliation
def reconciliation():
    out = []
    names = {a["id"]: a["name"] for a in L.accounts(include_hidden=True)}
    for st in L.statements():
        ch = st.get("change") or {}
        o, c = st.get("opening_value"), st.get("closing_value")
        flows = (ch.get("deposits") or 0) - (ch.get("withdrawals") or 0) + (ch.get("div_int") or 0) + (ch.get("market_change") or 0) - abs(ch.get("fees") or 0)
        rolled = r2((o or 0) + flows)
        alloc_sum = r2(sum((st.get("allocation") or {}).values()))
        pa = st.get("parsed_activity") or {}
        checks = [
            {"check": "Opening + deposits + income + market change = closing", "expected": c, "got": rolled},
            {"check": "Holdings + cash = closing value", "expected": c, "got": r2((st.get("holdings_value") or 0) + ((st.get("allocation") or {}).get("cash") or 0))},
            {"check": "Asset summary adds up to closing value", "expected": c, "got": alloc_sum},
        ]
        if ch.get("div_int") is not None and st.get("activity_count"):
            checks.append({"check": "Dividend & interest lines = statement's income total", "expected": ch.get("div_int"),
                           "got": r2((pa.get("dividend") or 0) + (pa.get("interest") or 0)), "partial_ok": "-" in (st.get("period_label") or "")})
        if ch.get("deposits"):
            checks.append({"check": "Deposit lines = statement's deposits", "expected": ch.get("deposits"),
                           "got": r2((pa.get("deposit") or 0) + (pa.get("transfer_in") or 0))})
        for k in checks:
            k["diff"] = r2((k["got"] or 0) - (k["expected"] or 0)) if k["expected"] is not None else None
            k["ok"] = k["diff"] is not None and abs(k["diff"]) < 0.005
        out.append({"statement": st["id"], "account": st["account"], "account_name": names.get(st["account"], st["account"]),
                    "period": st.get("period_label"), "opening_date": st["opening_date"], "closing_date": st["closing_date"],
                    "opening": o, "closing": c, "deposits": ch.get("deposits"), "income": ch.get("div_int"),
                    "market_change": ch.get("market_change"), "checks": checks, "ok": all(k["ok"] or k.get("partial_ok") for k in checks),
                    "file": st.get("file_name")})
    out.sort(key=lambda r: (r["account_name"], r["closing_date"]))
    return out

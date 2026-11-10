"""Made-up data for the demo profile ("Alex"), across Finances, Health, Education and Goals.

Nothing here is the owner's: names, employers, merchants, accounts, balances, workouts, runs and grades are invented
for the demo, and dates are relative to today so the demo always looks current. The same date gives the same data
(seeded random numbers), so every server instance builds the same demo.

build(app, client, schedule) fills `client` (a demo_store.Client) for user "demo". Most of it goes through the app's
own endpoints (imports, entries) so it is stored exactly as real data is; the rest is written in the stored shape.
`schedule` is the owner's course schedule without grades, if they chose to share it (see demo.py), else None.
"""
import math
import random
from datetime import date, datetime, timedelta, timezone

UID = "demo"
MILE = 1609.344


def _iso(d):
    return d.isoformat()


def _monday(d):
    return d - timedelta(days=d.weekday())


# ---------------------------------------------------------------- finances
ACCOUNTS = {
    "demo_checking": {"name": "Everyday Checking", "institution": "Harbor Bank", "type": "checking", "mask": "4821"},
    "demo_savings": {"name": "High-Yield Savings", "institution": "Harbor Bank", "type": "savings", "mask": "9034"},
    "demo_card": {"name": "Summit Rewards Card", "institution": "Summit Card", "type": "credit_card", "mask": "7710"},
    "demo_brokerage": {"name": "Northfield Brokerage", "institution": "Northfield Investing", "type": "brokerage", "mask": "3305"},
    "demo_roth": {"name": "Roth IRA", "institution": "Northfield Investing", "type": "roth_ira", "mask": "3306"},
}
EMPLOYER = "Lakeside Analytics"
SALARY = 41600

# merchant, category, (low, high), times per week (card unless noted)
CARD_SPEND = [
    ("GREEN BASKET MARKET", "Groceries", (38, 112), 1.6),
    ("DAILY GRIND COFFEE", "Food And Drink", (4.25, 7.5), 2.5),
    ("NOODLE HOUSE", "Food And Drink", (14, 26), 0.6),
    ("TAQUERIA SOL", "Food And Drink", (11, 19), 0.5),
    ("LUCA'S TRATTORIA", "Food And Drink", (38, 84), 0.25),
    ("CORNER BAKERY & DELI", "Food And Drink", (8, 15), 0.6),
    ("METRO TRANSIT", "Transportation", (2.75, 2.75), 2.0),
    ("QUICKRIDE TRIP", "Transportation", (11, 27), 0.35),
    ("NORTHLINE OUTFITTERS", "Shopping", (24, 140), 0.2),
    ("HOME GOODS DEPOT", "Shopping", (12, 65), 0.25),
    ("CORNER BOOKS", "Entertainment", (14, 38), 0.15),
    ("CITY CINEMA", "Entertainment", (13, 28), 0.15),
    ("NEIGHBORHOOD PHARMACY", "Personal Care", (7, 31), 0.2),
]
CARD_MONTHLY = [  # merchant, category, amount, day of month
    ("STREAMFLIX SUBSCRIPTION", "Entertainment", 15.49, 6),
    ("TUNEBOX MUSIC", "Entertainment", 11.99, 12),
    ("IRONWORKS GYM", "Health And Fitness", 45.00, 3),
    ("CITY BIKESHARE MEMBERSHIP", "Transportation", 10.00, 2),
    ("CLOUDNEST STORAGE", "Shopping", 2.99, 15),
]
# more made-up recurring charges for the subscriptions widget: (merchant, category, amount, every n days or months,
# "d"/"m", first charge days ago, last charge days ago or None). A price rise and a cancelled plan are included.
CARD_RECURRING = [
    ("STREAMFLIX SUBSCRIPTION", "Entertainment", 13.99, 1, "m", None, 130),      # the old price, then 15.49 above
    ("MORNING LEDGER DIGITAL", "Entertainment", 3.99, 7, "d", 200, None),
    ("PAGETURN AUDIOBOOKS", "Entertainment", 14.95, 1, "m", 290, 95),             # cancelled about three months ago
    ("FRESHFACE RAZOR CLUB", "Personal Care", 24.00, 3, "m", 280, None),
    ("CODEDESK PRO ANNUAL", "Entertainment", 119.00, 12, "m", 375, None),
]
CHECKING_MONTHLY = [
    ("MAPLE COURT APARTMENTS RENT", "Bills And Utilities", 1325.00, 1),
    ("CITY POWER AND LIGHT", "Bills And Utilities", None, 14),       # varies by season
    ("FASTLINE INTERNET", "Bills And Utilities", 60.00, 18),
    ("BRIGHTWAVE MOBILE", "Bills And Utilities", 45.00, 22),
    ("STUDENT RENTERS INSURANCE", "Bills And Utilities", 14.50, 9),
]


def _tx(store, tid, account, day, amount, merchant, category):
    tid = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in tid)
    store.document(f"users/{UID}/transactions/{tid}").set({
        "source": ACCOUNTS[account]["institution"], "account": account, "src": "csv", "date": _iso(day),
        "amount": round(amount, 2), "merchant": merchant, "category": category, "pending": False})


def _due(d, today, every, unit, first, last, start):
    """Is a recurring charge due on day d? Anchored on its first charge (or on the 6th of each month when it started
    before the demo's history, like the old streaming price)."""
    if last is not None and d > today - timedelta(days=last):
        return False
    anchor = today - timedelta(days=first) if first is not None else start.replace(day=6)
    if d < anchor:
        return False
    if unit == "d":
        return (d - anchor).days % every == 0
    months = (d.year - anchor.year) * 12 + d.month - anchor.month
    return d.day == min(anchor.day, 28) and months % every == 0


def finances(c, store, today, rng):
    start = (today - timedelta(days=300)).replace(day=1)
    for aid, a in ACCOUNTS.items():
        store.document(f"users/{UID}/fin_accounts/{aid}").set({**a, "aliases": [f"demo:{aid}"], "created": _iso(start)})
    for aid in ("chase_checking", "chase_savings", "chase_card", "venmo"):    # the app's built-in defaults stay hidden
        store.document(f"users/{UID}/fin_accounts/{aid}").set({"name": aid, "type": "other", "hidden": True, "aliases": []})

    # the paycheck job: earnings computes each check's withholding and net pay from these settings
    job_start = _monday(start) + timedelta(days=7)
    c.post("/api/fin/profile", json={
        "kind": "lifetracker-finance-profile",
        "paycheck_job": {"employer": EMPLOYER, "role": "Data analyst (part-time)", "annual_salary": SALARY,
                         "start": _iso(job_start), "end": _iso(date(today.year, 12, 31)), "frequency": "biweekly",
                         "work_state": "IL", "resident_state": "IL", "archived": False},
        "tax_settings": {"resident_state": "IL", "work_state": "IL", "roth_prior_contributions": {str(today.year - 1): 6000}},
        "jobs": [{"employer": "Campus tutoring", "notes": "Demo: occasional tutoring, paid by Venmo",
                  "pay": [{"date": _iso(today - timedelta(days=d)), "gross": g, "net": g}
                          for d, g in ((190, 120), (120, 180), (45, 150))]}]})
    checks = []
    for y in sorted({start.year, today.year}):
        r = c.get(f"/api/earnings?year={y}").get_json() or {}
        checks += [p for p in r.get("checks") or [] if p.get("pay_date") and p["pay_date"] <= _iso(today)]

    n, card_since_payment = 0, 0.0
    checking, savings = 4600.0, 9200.0
    bal_points = {"demo_checking": [], "demo_savings": [], "demo_card": []}
    card_month = {}
    paid = {}
    for m, cat, amt, every, unit, first, last in CARD_RECURRING:     # a yearly plan's first charge, before the history starts
        if first is not None and today - timedelta(days=first) < start:
            _tx(store, f"demo_tx_pre_{m[:6]}", "demo_card", today - timedelta(days=first), amt, m, cat)
    d = start
    while d <= today:
        n += 1
        # card spending
        for m, cat, (lo, hi), per_week in CARD_SPEND:
            if rng.random() < per_week / 7:
                amt = round(rng.uniform(lo, hi), 2)
                _tx(store, f"demo_tx_{n}_{m[:6]}", "demo_card", d, amt, m, cat)
                card_month[d.strftime("%Y-%m")] = card_month.get(d.strftime("%Y-%m"), 0) + amt
        for m, cat, amt, dom in CARD_MONTHLY:
            if m.startswith("STREAMFLIX") and d < today - timedelta(days=130):
                continue
            if d.day == dom:
                _tx(store, f"demo_tx_{n}_{m[:6]}", "demo_card", d, amt, m, cat)
                card_month[d.strftime("%Y-%m")] = card_month.get(d.strftime("%Y-%m"), 0) + amt
        for m, cat, amt, every, unit, first, last in CARD_RECURRING:
            if _due(d, today, every, unit, first, last, start):
                _tx(store, f"demo_tx_{n}_{m[:6]}", "demo_card", d, amt, m, cat)
                card_month[d.strftime("%Y-%m")] = card_month.get(d.strftime("%Y-%m"), 0) + amt
        # bills from checking
        for m, cat, amt, dom in CHECKING_MONTHLY:
            if d.day == dom:
                if amt is None:
                    amt = round(48 + 34 * abs(math.cos((d.month - 1) / 12 * 2 * math.pi)) + rng.uniform(-6, 6), 2)
                _tx(store, f"demo_tx_{n}_{m[:6]}", "demo_checking", d, amt, m, cat)
                checking -= amt
        # paychecks
        for p in checks:
            if p["pay_date"] == _iso(d):
                net = float(p.get("net") or 0)
                if net:
                    _tx(store, f"demo_pay_{p['pay_date']}", "demo_checking", d, -net, f"{EMPLOYER.upper()} PAYROLL", "Income")
                    checking += net
        # monthly: pay last month's card in full on the 20th, move $400 to savings on the 2nd
        if d.day == 20:
            prev = (d.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
            amt = round(card_month.get(prev, 0), 2)
            if amt and prev not in paid:
                paid[prev] = True
                _tx(store, f"demo_ccpay_{prev}", "demo_checking", d, amt, "SUMMIT CARD PAYMENT", "Credit Card Payment")
                _tx(store, f"demo_ccpaid_{prev}", "demo_card", d, -amt, "PAYMENT THANK YOU", "Credit Card Payment")
                checking -= amt
        if d.day == 2:
            _tx(store, f"demo_xfer_out_{d}", "demo_checking", d, 400, "ONLINE TRANSFER TO SAVINGS", "Transfer")
            _tx(store, f"demo_xfer_in_{d}", "demo_savings", d, -400, "ONLINE TRANSFER FROM CHECKING", "Transfer")
            checking -= 400; savings += 400
        if d.day == 28:
            interest = round(savings * 0.038 / 12, 2)
            _tx(store, f"demo_int_{d}", "demo_savings", d, -interest, "INTEREST PAYMENT", "Income")
            savings += interest
        # Venmo tutoring income lands in checking
        for days_ago, g in ((190, 120), (120, 180), (45, 150)):
            if d == today - timedelta(days=days_ago):
                _tx(store, f"demo_venmo_{days_ago}", "demo_checking", d, -g, "VENMO CASHOUT TUTORING", "Venmo")
                checking += g
        # month ends and today: record balances like a statement would
        if (d + timedelta(days=1)).day == 1 or d == today:
            owed = sum(v for k, v in card_month.items() if k not in paid)
            bal_points["demo_checking"].append((d, round(checking, 2)))
            bal_points["demo_savings"].append((d, round(savings, 2)))
            bal_points["demo_card"].append((d, round(owed, 2)))
        d += timedelta(days=1)
    for aid, pts in bal_points.items():
        for day, b in pts:
            store.document(f"users/{UID}/fin_balances/{aid}_{_iso(day)}_csv").set(
                {"account": aid, "date": _iso(day), "balance": b, "src": "csv"})


# investments: monthly statements with holdings, activity and an S&P 500 line
FUNDS = {  # symbol: (name, starting price, monthly drift, monthly volatility, yield)
    "VTI": ("Vanguard Total Stock Market ETF", 268.0, 0.009, 0.035, 0.013),
    "VXUS": ("Vanguard Total International Stock ETF", 61.0, 0.006, 0.03, 0.03),
    "BND": ("Vanguard Total Bond Market ETF", 72.0, 0.001, 0.01, 0.036),
    "QQQ": ("Invesco QQQ Trust", 470.0, 0.012, 0.045, 0.006),
}
PORTFOLIOS = {
    "demo_brokerage": {"VTI": 22, "VXUS": 40, "BND": 30},
    "demo_roth": {"VTI": 18, "QQQ": 6},
}


def investments(store, today, rng):
    import ledger as L
    first = (today.replace(day=1) - timedelta(days=330)).replace(day=1)
    prices = {s: f[1] for s, f in FUNDS.items()}
    qty = {a: dict(p) for a, p in PORTFOLIOS.items()}
    basis = {a: {s: q * prices[s] * 0.92 for s, q in p.items()} for a, p in PORTFOLIOS.items()}
    cash = {"demo_brokerage": 310.0, "demo_roth": 120.0}
    sp_ytd, sp_year = 0.0, first.year
    month = first
    while month < today.replace(day=1):
        nxt = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        close = min(nxt - timedelta(days=1), today)
        market = rng.gauss(0.008, 0.03)
        sp_period = round(market * 100, 2)
        if close.year != sp_year:
            sp_ytd, sp_year = 0.0, close.year
        sp_ytd = round(((1 + sp_ytd / 100) * (1 + market) - 1) * 100, 2)
        opening = {a: sum(qty[a][s] * prices[s] for s in qty[a]) + cash[a] for a in qty}
        for s, (_, _, drift, vol, _) in FUNDS.items():
            prices[s] = round(prices[s] * (1 + drift + (market - 0.008) * (vol / 0.035) * 0.8 + rng.gauss(0, vol * 0.35)), 2)
        for aid in qty:
            activity, deposits, divs = [], 0.0, 0.0
            contrib = 250.0 if aid == "demo_brokerage" else 625.0      # monthly auto-invest
            dep_day = month.replace(day=5)
            activity.append({"date": _iso(dep_day), "type": "deposit" if aid == "demo_brokerage" else "contribution",
                             "description": "AUTOMATIC DEPOSIT FROM HARBOR BANK", "amount": contrib})
            deposits += contrib
            sym = "VTI" if month.month % 2 else ("VXUS" if aid == "demo_brokerage" else "QQQ")
            px = prices[sym]
            q = round((contrib + cash[aid] - 100) / px, 3)
            if q > 0:
                activity.append({"date": _iso(month.replace(day=7)), "type": "buy", "description": f"BOUGHT {sym}",
                                 "symbol": sym, "quantity": q, "price": px, "amount": -round(q * px, 2)})
                cash[aid] = round(cash[aid] + contrib - q * px, 2)
                qty[aid][sym] = round(qty[aid].get(sym, 0) + q, 3)
                basis[aid][sym] = basis[aid].get(sym, 0) + q * px
            else:
                cash[aid] += contrib
            if month.month in (3, 6, 9, 12):
                for s, qn in qty[aid].items():
                    dv = round(qn * prices[s] * FUNDS[s][4] / 4, 2)
                    if dv:
                        activity.append({"date": _iso(close - timedelta(days=3)), "type": "dividend",
                                         "description": f"DIVIDEND {s}", "symbol": s, "amount": dv})
                        cash[aid] = round(cash[aid] + dv, 2); divs += dv
            holdings = []
            for s, qn in qty[aid].items():
                v = round(qn * prices[s], 2)
                holdings.append({"symbol": s, "name": FUNDS[s][0], "quantity": qn, "price": prices[s], "value": v,
                                 "cost_basis": round(basis[aid][s], 2), "unrealized": round(v - basis[aid][s], 2),
                                 "eai": round(v * FUNDS[s][4], 2), "yield_pct": round(FUNDS[s][4] * 100, 2), "lots": []})
            closing = round(sum(h["value"] for h in holdings) + cash[aid], 2)
            market_change = round(closing - opening[aid] - deposits - divs, 2)
            alloc = {"Cash": round(cash[aid], 2), "Equities": round(sum(h["value"] for h in holdings if h["symbol"] != "BND"), 2)}
            if any(h["symbol"] == "BND" for h in holdings):
                alloc["Fixed income"] = round(sum(h["value"] for h in holdings if h["symbol"] == "BND"), 2)
            stmt = {"period_label": f"{month.strftime('%B %Y')}", "opening_date": _iso(month), "closing_date": _iso(close),
                    "opening_value": round(opening[aid], 2), "closing_value": closing,
                    "change": {"deposits": deposits, "withdrawals": 0.0, "div_int": round(divs, 2),
                               "market_change": market_change, "fees": 0.0},
                    "holdings": holdings, "cash": round(cash[aid], 2), "allocation": alloc,
                    "benchmarks": [{"index": "S&P 500", "period": sp_period, "ytd": sp_ytd}],
                    "benchmark_month": month.strftime("%B %Y"),
                    "eai_total": round(sum(h["eai"] for h in holdings), 2), "type_label": ACCOUNTS[aid]["name"],
                    "activity": activity}
            if aid == "demo_roth":
                stmt["contributions"] = {str(month.year): {"regular": 625.0 * month.month}}
            L.save_statement(aid, stmt, src="demo")
        month = nxt


# ---------------------------------------------------------------- health
SPORTS = [("Tennis", 75, 11.5, 0.6), ("Basketball", 60, 12.5, 0.45), ("Cycling", 55, 10.5, 0.4),
          ("Hiking", 140, 9.5, 0.12), ("Yoga", 45, 5.5, 0.35)]


def health(store, today, rng):
    for i in range(200, -1, -1):
        d = today - timedelta(days=i)
        sleep = max(5.1, min(9.3, rng.gauss(7.3, 0.7) + (0.4 if d.weekday() >= 5 else 0)))
        rec = int(max(18, min(99, 30 + (sleep - 5.5) * 17 + rng.gauss(0, 11))))
        store.document(f"users/{UID}/health_days/{_iso(d)}").set({
            "date": _iso(d), "sleep_hours": round(sleep, 2), "sleep_performance": int(max(55, min(100, sleep / 8 * 100 + rng.gauss(0, 4)))),
            "recovery": rec, "hrv": round(max(28, min(120, 42 + rec * 0.35 + rng.gauss(0, 6))), 1),
            "rhr": int(max(46, min(68, 61 - rec * 0.09 + rng.gauss(0, 2)))), "strain": round(max(3.5, min(19.5, rng.gauss(11, 3))), 1)})
        for sport, minutes, strain, per_week in SPORTS:
            if rng.random() < per_week / 7:
                dur = int(minutes * rng.uniform(0.8, 1.2))
                hr = int(rng.uniform(118, 148))
                st = datetime(d.year, d.month, d.day, rng.choice([7, 12, 17, 18]), rng.choice([0, 15, 30]))
                store.document(f"users/{UID}/workouts/whoop_demo_{_iso(d)}_{sport.lower()}").set({
                    "source": "whoop", "date": _iso(d), "type": sport, "duration_min": dur,
                    "strain": round(strain + rng.gauss(0, 1.5), 1), "avg_hr": hr, "max_hr": hr + int(rng.uniform(25, 40)),
                    "calories": int(dur * rng.uniform(7, 10)),
                    "distance_m": round(dur / 60 * 22000, 1) if sport == "Cycling" else (round(dur / 60 * 4200, 1) if sport == "Hiking" else None),
                    "elev_m": round(rng.uniform(80, 420), 1) if sport in ("Cycling", "Hiking") else None,
                    "start_local": st.isoformat(), "exercises": [], "notes": "",
                    "start": st.replace(tzinfo=timezone.utc).isoformat(),
                    "end": (st + timedelta(minutes=dur)).replace(tzinfo=timezone.utc).isoformat()})
    store.document(f"users/{UID}/integrations/whoop").set({"access_token": "demo", "last_sync": datetime.now(timezone.utc), "has_distance": True})


LIFTS = {  # name: (unit, starting top weight, weekly gain, reps)
    "Bench Press": ("lb", 165, 2.5, 5), "Back Squat": ("lb", 205, 5, 5), "Deadlift": ("lb", 245, 5, 3),
    "Overhead Press": ("lb", 95, 1.25, 6), "Barbell Row": ("lb", 135, 2.5, 8), "Pull-up": ("lb", 0, 0, 8),
    "Romanian Deadlift": ("lb", 155, 2.5, 8), "Incline Dumbbell Press": ("lb", 50, 1.25, 10),
    "Leg Press": ("lb", 270, 5, 10), "Lat Pulldown": ("lb", 120, 2.5, 10), "Walking Lunge": ("lb", 30, 1.25, 12),
}
DAYS = [  # name, kind, weekday, exercises (name, sets, reps, % of max, max lift)
    ("Upper A", "strength", "mon", [("Bench Press", 4, "5", 80, "bench"), ("Barbell Row", 4, "8", None, None),
                                    ("Overhead Press", 3, "6", None, None), ("Pull-up", 3, "8", None, None)]),
    ("Lower A", "strength", "tue", [("Back Squat", 4, "5", 80, "squat"), ("Romanian Deadlift", 3, "8", None, None),
                                    ("Walking Lunge", 3, "12", None, None)]),
    ("Easy run", "cardio", "wed", []),
    ("Upper B", "strength", "thu", [("Incline Dumbbell Press", 4, "10", None, None), ("Lat Pulldown", 4, "10", None, None),
                                    ("Bench Press", 3, "8", 70, "bench")]),
    ("Lower B", "strength", "fri", [("Deadlift", 4, "3", 85, None), ("Leg Press", 3, "10", None, None),
                                    ("Back Squat", 3, "8", 70, "squat")]),
    ("Long run", "cardio", "sun", []),
]


def training(c, today, rng):
    weeks = []
    for w in range(4):
        days = {k: [] for k in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
        for i, (name, kind, wd, ex) in enumerate(DAYS):
            days[wd].append({"name": name, "kind": kind, "program_day": i + 1, "exercises": [
                {"name": e, "unit": LIFTS[e][0], "rest_sec": 150 if pct else 90, "sets": s, "reps": reps,
                 "pct_of_max": int(pct + 2.5 * w) if pct else None, "max_lift": ml}
                for e, s, reps, pct, ml in ex]})
        weeks.append({"week": w + 1, "days": days, "tray": []})
    sessions, first = [], _monday(today) - timedelta(weeks=12)
    for wk in range(13):
        for name, kind, wd, ex in DAYS:
            if kind != "strength":
                continue
            d = first + timedelta(weeks=wk, days=["mon", "tue", "wed", "thu", "fri"].index(wd))
            if d >= today or (wk < 12 and rng.random() < 0.08):        # a missed day now and then, not this week
                continue
            exercises = []
            for e, s, reps, pct, ml in ex:
                unit, w0, gain, r = LIFTS[e]
                top = round((w0 + gain * wk) / 2.5) * 2.5 if w0 else None
                if pct and pct < 80:
                    top = round(top * 0.88 / 2.5) * 2.5
                rr = int(reps)
                sets = [{"weight": top, "reps": rr if i < s - 1 else max(1, rr - rng.choice([0, 0, 1, 2])), "done": True} for i in range(s)]
                if top and top > 100:
                    sets.insert(0, {"weight": round(top * 0.6 / 5) * 5, "reps": rr, "done": True, "warmup": True})
                exercises.append({"name": e, "unit": unit, "rest_sec": 150 if pct else 90, "sets": sets})
            dur = int(rng.uniform(52, 78) * 60)
            start = datetime(d.year, d.month, d.day, 6, 30)
            sessions.append({"date": _iso(d), "name": name, "type": "Weightlifting", "duration_sec": dur,
                             "started_at": start.isoformat(), "finished_at": (start + timedelta(seconds=dur)).isoformat(),
                             "exercises": exercises})
    c.post("/api/training/import", json={
        "state": {"program_name": "Demo strength block", "maxes": {"bench": 205, "squat": 265},
                  "cycle_start": _iso(_monday(today) - timedelta(weeks=1)), "history_start": _iso(first), "cycle_weeks": 4,
                  "default_rest_sec": 120},
        "template": {"weeks": weeks}, "sessions": sessions})


# runs: a few made-up loops around a riverside park (not the owner's city)
ROUTE_CENTERS = [(45.5152, -122.6712), (45.5290, -122.6630), (45.5048, -122.6550)]


def _encode(points):
    out, plat, plng = [], 0, 0
    for lat, lng in points:
        for v, prev in ((round(lat * 1e5), plat), (round(lng * 1e5), plng)):
            d = v - prev
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1F)) + 63)); d >>= 5
            out.append(chr(d + 63))
        plat, plng = round(lat * 1e5), round(lng * 1e5)
    return "".join(out)


def _route(miles, rng):
    clat, clng = rng.choice(ROUTE_CENTERS)
    r_km = miles * 1.609 / (2 * math.pi)
    pts, wob = [], [rng.uniform(0.1, 0.25) for _ in range(4)]
    for i in range(121):
        t = i / 120 * 2 * math.pi
        r = r_km * (1 + sum(w * math.sin((k + 2) * t + k) for k, w in enumerate(wob)) / 2)
        pts.append((clat + r / 111 * math.sin(t), clng + r / (111 * math.cos(math.radians(clat))) * math.cos(t) * 1.3))
    return _encode(pts)


def runs(c, today, rng):
    acts, first = [], today - timedelta(days=180)
    d, k = first, 0
    fitness = 9.6 * 60      # easy pace, seconds per mile, improving over the months
    while d < today:
        wd = d.weekday()
        if wd in (1, 3, 6) and rng.random() > 0.1:
            prog = 1 - (today - d).days / 180          # 0 six months ago, 1 today
            lo, hi = {1: (3.2, 4.5), 3: (4, 6), 6: (5.5 + 3 * prog, 7 + 4 * prog)}[wd]
            miles = round(rng.uniform(lo, hi), 2)
            pace = fitness - prog * 40 + rng.gauss(0, 12) - (25 if wd == 3 else 0)
            race = (today - d).days == 38
            if race:
                miles, pace = 6.21, fitness - prog * 40 - 70
            k += 1
            dist = miles * MILE
            moving = int(dist / MILE * pace)
            splits = [{"n": i + 1, "distance_m": min(MILE, dist - i * MILE), "moving_s": int(min(MILE, dist - i * MILE) / MILE * (pace + rng.gauss(0, 8))),
                       "elev_m": round(rng.uniform(-6, 9), 1), "avg_hr": int(rng.uniform(146, 166))}
                      for i in range(math.ceil(miles))]
            efforts = [{"name": n, "distance_m": m, "elapsed_s": int(m / MILE * (pace - (8 if m < 3000 else 0)))}
                       for n, m in (("1/2 mile", 804.67), ("1 mile", MILE), ("2 mile", 2 * MILE), ("5K", 5000), ("10K", 10000))
                       if m <= dist]
            st = datetime(d.year, d.month, d.day, 6 if wd != 6 else 8, rng.choice([5, 20, 40]))
            acts.append({"id": str(9100000000 + k), "date": _iso(d), "sport": "Run",
                         "name": "Riverside 10K" if race else ("Long run" if wd == 6 else ("Tempo run" if wd == 3 else "Easy run")),
                         "start_local": st.isoformat(), "distance_m": round(dist, 1), "moving_s": moving,
                         "elapsed_s": moving + int(rng.uniform(20, 240)), "elev_m": round(miles * rng.uniform(6, 14), 1),
                         "avg_hr": int(rng.uniform(148, 162)), "max_hr": int(rng.uniform(172, 186)),
                         "calories": int(miles * 105), "race": race, "polyline": _route(miles, rng),
                         "splits": splits, "best_efforts": efforts})
        d += timedelta(days=1)
    for i in range(0, len(acts), 50):
        c.post("/api/strava/import", json={"activities": acts[i:i + 50]})
    race_day = _monday(today) + timedelta(weeks=9, days=6)
    c.post("/api/raceplans", json={"race": "half", "race_date": _iso(race_day), "runs_per_week": 4, "long_day": 6,
                                   "current_weekly_mi": 18, "current_long_mi": 8, "goal_seconds": 6900,
                                   "start_date": _iso(_monday(today) - timedelta(weeks=3)), "name": "Spring half marathon"})


FOODS = {  # per 100 g: kcal, protein, carbs, fat, fiber, sugar, sodium_mg (approximate, for the demo)
    "Oatmeal, cooked": (71, 2.5, 12, 1.5, 1.7, 0.3, 4), "Banana": (89, 1.1, 23, 0.3, 2.6, 12, 1),
    "Greek yogurt, plain nonfat": (59, 10, 3.6, 0.4, 0, 3.2, 36), "Blueberries": (57, 0.7, 14, 0.3, 2.4, 10, 1),
    "Scrambled eggs": (149, 10, 1.6, 11, 0, 1.4, 145), "Whole wheat toast": (247, 13, 41, 3.4, 7, 6, 400),
    "Chicken breast, grilled": (165, 31, 0, 3.6, 0, 0, 74), "Brown rice, cooked": (123, 2.7, 26, 1, 1.6, 0.2, 4),
    "Broccoli, steamed": (35, 2.4, 7, 0.4, 3.3, 1.4, 41), "Salmon, baked": (206, 22, 0, 12, 0, 0, 61),
    "Sweet potato, baked": (90, 2, 21, 0.2, 3.3, 6.5, 36), "Turkey sandwich": (220, 14, 24, 7, 2, 3, 640),
    "Mixed green salad": (20, 1.5, 3.5, 0.2, 1.8, 1.2, 30), "Pasta with tomato sauce": (130, 4.5, 24, 2, 2, 3.5, 210),
    "Almonds": (579, 21, 22, 50, 12.5, 4.4, 1), "Apple": (52, 0.3, 14, 0.2, 2.4, 10, 1),
    "Protein shake": (70, 12, 3, 1, 0.5, 1.5, 90), "Burrito bowl": (150, 8, 18, 5, 3, 1.5, 320),
}
MENU = {
    "breakfast": [[("Oatmeal, cooked", 240), ("Banana", 118), ("Almonds", 20)], [("Greek yogurt, plain nonfat", 200), ("Blueberries", 100)],
                  [("Scrambled eggs", 150), ("Whole wheat toast", 60)]],
    "lunch": [[("Turkey sandwich", 230), ("Apple", 180)], [("Burrito bowl", 420)], [("Chicken breast, grilled", 140), ("Mixed green salad", 150), ("Brown rice, cooked", 160)]],
    "dinner": [[("Salmon, baked", 170), ("Sweet potato, baked", 200), ("Broccoli, steamed", 120)],
               [("Pasta with tomato sauce", 350), ("Mixed green salad", 100)], [("Chicken breast, grilled", 170), ("Brown rice, cooked", 200), ("Broccoli, steamed", 100)]],
    "snacks": [[("Protein shake", 350)], [("Apple", 180), ("Almonds", 25)], [("Greek yogurt, plain nonfat", 170)]],
}


def food(c, today, rng):
    c.put("/api/food/goals", json={"kcal": 2400, "protein_g": 160, "carbs_g": 260, "fat_g": 75})
    keys = ("kcal", "protein", "carbs", "fat", "fiber", "sugar", "sodium_mg")
    for i in range(20, -1, -1):
        d = today - timedelta(days=i)
        for meal, options in MENU.items():
            if i == 0 and meal in ("dinner", "snacks"):
                continue
            if meal == "snacks" and rng.random() < 0.3:
                continue
            for name, grams in rng.choice(options):
                c.post("/api/food/entries", json={"date": _iso(d), "meal": meal, "grams": round(grams * rng.uniform(0.85, 1.15)),
                                                  "food": {"name": name, "source": "demo", "per100g": dict(zip(keys, FOODS[name]))}})


# ---------------------------------------------------------------- education
def _term(d):
    return ("Spring" if d.month <= 5 else "Summer" if d.month <= 7 else "Fall") + f" {d.year}"


def _terms(today):
    """Four terms of a two-year stretch ending with the current school year."""
    fall = today.year if today.month >= 8 else today.year - 1
    return [f"Fall {fall - 1}", f"Spring {fall}", f"Fall {fall}", f"Spring {fall + 1}"]


GENERIC = [  # term index, title, credits, grade (past terms only), extra
    (0, "Constitutional Law I", 4, "B+", {"tags": []}), (0, "Contracts", 4, "A-", {}), (0, "Torts", 4, "B+", {}),
    (0, "Legal Research and Writing", 2, "A", {}),
    (1, "Civil Procedure", 4, "A-", {}), (1, "Criminal Law", 3, "B+", {}), (1, "Property", 4, "A", {}),
    (1, "Legislation and Regulation", 3, "A-", {}),
    (2, "Evidence", 4, None, {"professor": "Prof. Demo", "meetings_text": "MW 10:30 AM-12:25 PM", "exam_date_offset": 34, "exam_time": "9:00 AM"}),
    (2, "Corporations", 4, None, {"professor": "Prof. Sample", "meetings_text": "TTh 1:30-3:25 PM", "exam_date_offset": 38, "exam_time": "1:30 PM"}),
    (2, "Professional Responsibility", 2, None, {"tags": ["pr"], "meetings_text": "F 9:00-10:50 AM", "exam_date_offset": 41, "exam_time": "9:00 AM"}),
    (2, "Negotiations Seminar", 3, None, {"tags": ["experiential"], "experiential_credits": 3, "meetings_text": "W 3:30-5:30 PM", "exam_type": "Paper"}),
    (3, "Federal Income Taxation", 4, None, {}), (3, "Administrative Law", 3, None, {}),
    (3, "Writing Seminar: Technology and Privacy", 2, None, {"tags": ["ulwr"]}), (3, "Mergers and Acquisitions", 3, None, {}),
]


def education(c, today, rng, schedule):
    terms = _terms(today)
    if schedule and schedule.get("courses"):
        prof = {k: v for k, v in (schedule.get("profile") or {}).items() if k in ("school", "program", "terms", "grad_term")}
        c.post("/api/edu/import", json={"replace": True, "profile": prof | {"target_gpa": 3.6},
                                        "courses": schedule["courses"]})
        if schedule.get("syllabi"):
            c.post("/api/edu/syllabi/import", json={"syllabi": schedule["syllabi"][:40]})
    else:
        cur = 2
        courses = []
        for ti, title, credits, grade, extra in GENERIC:
            co = {"term": terms[ti], "title": title, "credits": credits, "graded": True,
                  "status": "completed" if ti < cur else ("enrolled" if ti == cur else "planned"),
                  **{k: v for k, v in extra.items() if k != "exam_date_offset"}}
            if grade:
                co |= {"grade": grade, "grade_status": "actual"}
            if extra.get("exam_date_offset"):
                co["exam_date"] = _iso(today + timedelta(days=extra["exam_date_offset"]))
            courses.append(co)
        c.post("/api/edu/import", json={"replace": True, "courses": courses,
                                        "profile": {"terms": terms, "target_gpa": 3.6, "transfer_credits": 0, "grad_term": terms[-1]}})
    tasks = [("Finish the outline for the first half of the course", 3, True), ("Read the assigned cases for Thursday", 1, False),
             ("Draft the seminar paper thesis paragraph", 6, True), ("Practice exam under timed conditions", 12, False),
             ("Email the clinic supervisor about hours", 0, False), ("Update resume for spring interviews", 9, False)]
    for title, days, important in tasks:
        c.post("/api/edu/tasks", json={"title": title, "due_date": _iso(today + timedelta(days=days)), "important": important,
                                       **({"my_day": True} if days <= 1 else {})})


# ---------------------------------------------------------------- goals
def goals(c, today):
    y0, y1 = date(today.year, 1, 1), date(today.year, 12, 31)
    items = [
        {"title": "Reach $45,000 net worth", "area": "finance", "category": "Saving",
         "measure": {"kind": "number", "metric": "fin.net_worth", "target": 45000},
         "start_date": _iso(today - timedelta(days=120)), "due_date": _iso(y1 + timedelta(days=180)),
         "why": "A cushion before the first full-time job."},
        {"title": "Bench press 225 lb", "area": "health", "category": "Strength",
         "measure": {"kind": "number", "metric": "health.lift_top:Bench Press", "target": 225},
         "start_date": _iso(today - timedelta(days=84)), "due_date": _iso(today + timedelta(days=150))},
        {"title": "Run 500 miles this year", "area": "health", "category": "Running",
         "measure": {"kind": "number", "metric": "health.run_miles_since_start", "target": 500},
         "start_date": _iso(max(y0, today - timedelta(days=180))), "due_date": _iso(y1)},
        {"title": "Average 7.5 hours of sleep", "area": "health", "category": "Sleep",
         "measure": {"kind": "number", "metric": "health.sleep_30d", "target": 7.5},
         "start_date": _iso(today - timedelta(days=60)), "due_date": _iso(today + timedelta(days=60))},
        {"title": "Finish a half marathon", "area": "health", "category": "Running",
         "measure": {"kind": "steps"}, "start_date": _iso(today - timedelta(days=21)), "due_date": _iso(today + timedelta(days=68)),
         "steps": [{"title": "Pick a race and register", "done": True, "done_on": _iso(today - timedelta(days=20))},
                   {"title": "Build to a 10-mile long run", "due": _iso(today + timedelta(days=30))},
                   {"title": "Run a tune-up 10K", "done": True, "done_on": _iso(today - timedelta(days=38))},
                   {"title": "Taper and race", "due": _iso(today + timedelta(days=68))}]},
        {"title": "Read 12 books this year", "area": "personal", "category": "Reading",
         "measure": {"kind": "number", "unit": "books", "start": 0, "target": 12},
         "start_date": _iso(y0), "due_date": _iso(y1)},
        {"title": "Outline every course before finals", "area": "education", "category": "Study",
         "measure": {"kind": "steps"}, "start_date": _iso(today - timedelta(days=30)), "due_date": _iso(today + timedelta(days=30)),
         "steps": [{"title": "Evidence outline", "done": True, "done_on": _iso(today - timedelta(days=4))},
                   {"title": "Corporations outline", "due": _iso(today + timedelta(days=10))},
                   {"title": "Professional Responsibility outline", "due": _iso(today + timedelta(days=20))}]},
    ]
    c.post("/api/goals/import", json={"goals": items})
    books = next(g for g in c.get("/api/goals").get_json()["goals"] if g["title"].startswith("Read 12"))
    for n, days in ((3, 200), (6, 120), (9, 25)):
        c.post(f"/api/goals/{books['id']}/log", json={"value": n, "date": _iso(today - timedelta(days=days))})


APARTMENTS = [  # made-up listings on made-up streets; example.com links
    ("1418 Larkspur Ave, Unit 3", "Pearl", 2950, 1420, 3, 2, (45.5291, -122.6848), "Larkspur stop", 0.2, ["in-unit laundry", "gym"], 0, "saved"),
    ("77 Juniper Row", "Nob Hill", 3400, 1680, 3, 2.5, (45.5268, -122.6985), "Juniper stop", 0.4, ["roof deck", "parking"], 0, "touring"),
    ("2201 Alder Commons #5B", "Goose Hollow", 2600, 1150, 3, 1, (45.5185, -122.6920), "Alder stop", 0.1, ["dishwasher"], 7, None),
    ("530 Wren Court", "Pearl", 3650, 1900, 3, 2, (45.5310, -122.6810), "Wren stop", 0.5, ["pool", "gym", "doorman"], 0, None),
    ("9 Fernhill Lane", "Nob Hill", 2400, 980, 3, 1, (45.5300, -122.7010), "Fernhill stop", 0.7, [], 7, "passed"),
    ("1600 Cedar Terrace", "Pearl", 3100, 1550, 3, 2, (45.5275, -122.6830), "Cedar stop", 0.3, ["modern kitchen"], 0, None),
]


BIKE_DOCKS = {  # made-up stations
    "Larkspur Ave & 12th": (45.5291, -122.6848), "Union Station Plaza": (45.5289, -122.6765),
    "Juniper Row & 21st": (45.5268, -122.6985), "Riverfront Park": (45.5175, -122.6705),
    "Campus Library": (45.5115, -122.6840), "Alder St & 9th": (45.5185, -122.6810),
}
BIKE_TRIPS = [("Larkspur Ave & 12th", "Campus Library", 0.55), ("Campus Library", "Larkspur Ave & 12th", 0.5),
              ("Larkspur Ave & 12th", "Riverfront Park", 0.12), ("Juniper Row & 21st", "Union Station Plaza", 0.1),
              ("Alder St & 9th", "Campus Library", 0.08), ("Riverfront Park", "Alder St & 9th", 0.05)]


def bike_rides(c, today, rng):
    rides, start = [], today - timedelta(days=150)
    for n in range(150):
        day = start + timedelta(days=n)
        if day.weekday() >= 5 and rng.random() < 0.6 or rng.random() < 0.35:
            continue
        for _ in range(rng.choice((1, 2, 2))):
            a, b = rng.choices([(x, y) for x, y, _ in BIKE_TRIPS], weights=[w for *_, w in BIKE_TRIPS])[0]
            ebike = rng.random() < 0.4
            mins = rng.randint(7, 19)
            h, m = rng.randint(8, 19), rng.randint(0, 59)
            fee = round(0.1 * mins, 2) if ebike else 0.0
            rides.append({"date": _iso(day), "start_station": a, "end_station": b, "minutes": mins,
                          "start_time": f"{(h - 1) % 12 + 1}:{m:02d}{'am' if h < 12 else 'pm'}",
                          "bike": "ebike" if ebike else "classic", "total": fee, "saved": round(1.0 + 0.05 * mins + (0.15 * mins if ebike else 0), 2),
                          "start_lat": BIKE_DOCKS[a][0], "start_lon": BIKE_DOCKS[a][1], "end_lat": BIKE_DOCKS[b][0], "end_lon": BIKE_DOCKS[b][1]})
    c.post("/api/travel/bike/rides/import", json={"rides": rides})
    c.put("/api/travel/bike/membership", json={"price": 95, "start": _iso(start), "end": _iso(start + timedelta(days=364))})


def apartments(c, today):
    c.put("/api/apartments/criteria", json={"city": "Portland", "max_rent": 3200, "stretch_rent": 3600, "min_beds": 3,
                                           "big_sqft": 1500, "move_in": _iso(date(today.year + 1, 6, 1))})
    last = today - timedelta(days=(today.weekday() + 1) % 7)            # the latest Sunday
    for weeks in (1, 0):
        day = _iso(last - timedelta(days=7 * weeks))
        rows = [{"url": f"https://example.com/listing/demo-{i}", "address": a, "neighborhood": n, "rent": r, "sqft": sq, "beds": b,
                 "baths": ba, "lat": ll[0], "lon": ll[1], "nearest_stop": st, "stop_mi": mi, "amenities": am,
                 "available": _iso(date(today.year + 1, 6, 1)), "source": "Demo"}
                for i, (a, n, r, sq, b, ba, ll, st, mi, am, age, _) in enumerate(APARTMENTS) if (age == 7) == (weeks == 1) or weeks == 0]
        if weeks == 0:                                    # a price cut between the two emails
            next(r for r in rows if r["address"] == APARTMENTS[2][0])["rent"] -= 150
        body = "\n".join(f"{r['address']}: ${r['rent']:,}, {r['beds']} bed / {r['baths']:g} bath, {r['sqft']:,} sq ft. {r['nearest_stop']} is {r['stop_mi']} mi away."
                         for r in rows)
        c.post("/api/apartments/import", json={"digest_date": day, "listings": rows, "save_photos": False,
                                               "digest": {"subject": f"Apartment digest: week of {date.fromisoformat(day).strftime('%b %-d')}",
                                                          "kind": "email", "body": f"New and notable this week (largest first)\n\n{body}\n\nMade-up demo email."}})
    ids = {x["url"]: x["id"] for x in c.get("/api/apartments").get_json()["listings"]}
    for i, a in enumerate(APARTMENTS):
        if a[-1]:
            c.patch(f"/api/apartments/{ids[f'https://example.com/listing/demo-{i}']}",
                    json={"status": a[-1], "rank": i, **({"rating": 5 - i} if a[-1] != "passed" else {})})


METRO_STOPS = ["Union Station", "Pearl Center", "Campus", "Riverfront", "Nob Hill"]


def metro(c, today, rng):
    """Made-up transit card history: weekday rides, a monthly benefit load, the odd bus."""
    trips, start = [], today - timedelta(days=120)
    for n in range(121):
        day = start + timedelta(days=n)
        if day.day == 1:
            trips.append({"date": _iso(day), "time": "00:05", "desc": "SmartBenefits load", "change": 50.0, "kind": "benefit"})
        if day.weekday() >= 5 or rng.random() < 0.55:
            continue
        a, b = rng.sample(METRO_STOPS, 2)
        trips.append({"date": _iso(day), "time": f"{rng.randint(8, 10):02d}:{rng.randint(0, 59):02d}", "desc": "Exit", "entry": a, "exit": b,
                      "change": -round(rng.choice((2.25, 2.25, 2.6, 3.1)), 2), "kind": "rail"})
        if rng.random() < 0.2:
            trips.append({"date": _iso(day), "time": "17:40", "desc": "Bus", "entry": "D4", "change": -2.25, "kind": "bus"})
    c.post("/api/travel/metro/import", json={"trips": trips})
    c.put("/api/travel/metro/benefit", json={"monthly": 50, "start": _iso(start.replace(day=1)), "label": "School transit benefit"})


def trips(c, today):
    """Two made-up trips: one coming up with bookings, one from last spring."""
    c.put("/api/trips/settings", json={"home_airport": "PDX", "airlines": ["Alaska", "Delta"]})
    s = today + timedelta(days=38)
    c.post("/api/trips", json={"name": "Winter break in Lisbon", "start": _iso(s), "end": _iso(s + timedelta(days=7)),
                               "places": "Lisbon; Porto", "travelers": 2, "notes": "Day trip to Sintra. Made-up demo trip.",
                               "bookings": [{"kind": "flight", "airline": "Delta", "flight_no": "DL 0000", "from": "PDX", "to": "LIS",
                                             "date": _iso(s), "time": "17:05", "cost": 912},
                                            {"kind": "stay", "title": "Apartment in Alfama", "date": _iso(s), "end_date": _iso(s + timedelta(days=5)),
                                             "cost": 640}]})
    c.post("/api/trips", json={"name": "Weekend in Seattle", "places": "Seattle"})
    p = today - timedelta(days=200)
    c.post("/api/trips", json={"name": "Spring trip to Austin", "start": _iso(p), "end": _iso(p + timedelta(days=4)), "places": "Austin",
                               "bookings": [{"kind": "flight", "airline": "Alaska", "from": "PDX", "to": "AUS", "date": _iso(p), "cost": 388}]})


def social(c, today):
    """Made-up Partiful-style events, sent through the .ics import so the demo exercises the real parser."""
    rows = [(-40, 19, "Housewarming on the Hill", "Sample Ave SE, Washington, DC", "going"),
            (-12, 20, "Friendsgiving potluck", "Sample St NW, Washington, DC", "going"),
            (3, 19, "Trivia night", "Sample Tavern, Washington, DC", "going"),
            (9, 18, "Winter formal", "Sample Hall, Washington, DC", "maybe"),
            (16, 21, "Holiday party", "Sample Loft, Washington, DC", "invited"),
            (34, 20, "Birthday dinner in Chicago", "Sample Rooftop, Chicago, IL", "going")]
    stat = {"going": "ACCEPTED", "maybe": "TENTATIVE", "invited": "NEEDS-ACTION"}
    ev = []
    for i, (days, hour, title, place, rsvp) in enumerate(rows):
        st = datetime.combine(today + timedelta(days=days), datetime.min.time()).replace(hour=hour)
        en = st + timedelta(hours=3)
        ev.append("\r\n".join([
            "BEGIN:VEVENT", f"UID:demo-social-{i}@example.com", f"DTSTART;TZID=America/New_York:{st:%Y%m%dT%H%M%S}",
            f"DTEND;TZID=America/New_York:{en:%Y%m%dT%H%M%S}", f"SUMMARY:{title}", f"LOCATION:{place.replace(',', chr(92) + ',')}",
            "DESCRIPTION:A made-up demo event.", "ORGANIZER;CN=Sample Host:mailto:host@example.com",
            f"ATTENDEE;PARTSTAT={stat[rsvp]}:mailto:you@example.com", "END:VEVENT"]))
    c.post("/api/social/import", json={"ics": "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "\r\n".join(ev) + "\r\nEND:VCALENDAR\r\n"})


def build(app, store, schedule=None, today=None):
    """Fill `store` with the demo profile. Runs the app's endpoints through a test client as the seed session."""
    import demo
    today = today or date.today()
    rng = random.Random(f"lifetracker-demo-{today.isoformat()}")
    c = demo.seed_client(app)
    finances(c, store, today, rng)
    with demo.seed_context(app):
        investments(store, today, rng)
    health(store, today, rng)
    training(c, today, rng)
    runs(c, today, rng)
    food(c, today, rng)
    education(c, today, rng, schedule)
    goals(c, today)
    apartments(c, today)
    bike_rides(c, today, rng)
    metro(c, today, rng)
    trips(c, today)
    social(c, today)
    return store

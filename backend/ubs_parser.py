"""Read UBS statement PDFs and UBS tax-form PDFs into plain dicts. No database access here.

Statements ("Household Statement w/ Summary" or single-account): one PDF can hold several accounts
(Resource Management Account, Roth IRA, Rollover IRA...). Pages are assigned to an account by the
account code printed in every page footer, never by page number. Some UBS PDFs store pages rotated
90 degrees, so lines are rebuilt from word positions instead of trusting the text stream order.

Tax forms ("Tax Forms for YYYY"): Consolidated 1099 (DIV / INT / B summary), 1099-R and 5498.

Everything returned is what the document says; nothing is estimated here.
"""
import io
import re
from datetime import date

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
NUM = r"-?\$?-?[\d,]+\.\d+"
MON = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"


# ---------------------------------------------------------------- text extraction
def pdf_pages(data):
    """bytes -> list of pages, each a list of text lines in reading order."""
    import gc
    import pdfplumber
    out = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for p in pdf.pages:
            out.append(_page_lines(p))
            p.close()              # drop the page's parsed layout right away; keeps memory flat on small servers
    gc.collect()
    return out


def _page_lines(pg, tol=3):
    words = pg.extract_words(x_tolerance=1.5, y_tolerance=2, use_text_flow=False)
    if sum(1 for w in words if w.get("direction") == "ttb") > len(words) / 2:
        # page content is rotated: each text line is a column; columns read right to left
        items = [(-w["x1"], w["top"], w["bottom"], w["text"]) for w in words if w.get("direction") == "ttb"]
    else:
        items = [(w["top"], w["x0"], w["x1"], w["text"]) for w in words if w.get("upright", True)]
    items.sort(key=lambda i: (round(i[0]), i[1]))
    rows = []
    for it in items:
        if rows and abs(rows[-1][0] - it[0]) <= tol:
            rows[-1][1].append(it)
        else:
            rows.append([it[0], [it]])
    out = []
    for _, its in rows:
        its.sort(key=lambda i: i[1])
        s, last = "", None
        for _, a, b, text in its:
            if last is not None:
                s += "   " if a - last > 12 else " "
            s += text
            last = b
        out.append(s)
    return out


def num(s):
    if s is None:
        return None
    s = s.replace("$", "").replace(",", "").strip()
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("-()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _nums(line):
    return [num(x) for x in re.findall(NUM, line)]


# ---------------------------------------------------------------- statements
ACCOUNT_TYPES = {"roth ira": "roth_ira", "ira rollover": "rollover_ira", "rollover ira": "rollover_ira",
                 "traditional ira": "traditional_ira", "ira": "traditional_ira", "sep ira": "sep_ira"}
ALLOC_KEYS = {"cash": "cash", "cash alternatives": "cash_alternatives", "equities": "equities",
              "fixed income": "fixed_income", "non-traditional": "non_traditional", "commodities": "commodities",
              "other": "other"}
CONTRIB_ROWS = ["Non-deductible", "Deductible", "Rollover conversion", "Roth conversion", "Rollover", "Regular",
                "Recharacterization", "Employer", "Employee", "Transfer"]


def is_statement(pages):
    head = " ".join(" ".join(p[:12]) for p in pages[:2])
    return "UBS" in head and ("Value of your account" in " ".join(sum(pages[:3], [])))


def parse_statement(data, pages=None):
    pages = pages or pdf_pages(data)
    if not is_statement(pages):
        raise ValueError("This doesn't look like a UBS account statement.")
    groups, order = {}, []
    for lines in pages:
        code = next((m.group(1) for l in lines[-4:] + lines[:6] for m in [re.search(r"\b(2T\d{7})0\b", l)] if m), None)
        if not code:
            continue   # disclosure pages
        if code not in groups:
            groups[code] = []
            order.append(code)
        groups[code].extend(lines)
    accounts = [_parse_account(code, groups[code]) for code in order]
    return {"kind": "ubs_statement", "accounts": [a for a in accounts if a]}


def _find(lines, pattern, flags=re.I):
    for l in lines:
        m = re.search(pattern, l, flags)
        if m:
            return m
    return None


def _two(lines, label):
    """'Label  X  Y' -> (X, Y): the first line starting with label that carries two money amounts."""
    for l in lines:
        m = re.match(r"\s*" + label + r"\s+(-?\$?-?[\d,]+\.\d{2})\s+(-?\$?-?[\d,]+\.\d{2})(?!\d|%)", l, re.I)
        if m:
            return num(m.group(1)), num(m.group(2))
    return None, None


def _parse_account(code, lines):
    text = "\n".join(lines)
    number = _find(lines, r"Account number:\s*(2T\s*\d{5}\s*\d{2})")
    number = re.sub(r"\s+", " ", number.group(1)) if number else f"{code[:2]} {code[2:7]} {code[7:]}"
    digits = re.sub(r"\D", "", number)
    atype = _find(lines, r"Account type:\s*(Roth IRA|IRA Rollover|Rollover IRA|Traditional IRA|SEP IRA|IRA)\b")
    if atype:
        acct_type = ACCOUNT_TYPES.get(atype.group(1).lower(), "ira")
    elif "Resource Management Account" in text:
        acct_type = "brokerage"
    else:
        acct_type = "brokerage"
    label = _find(lines, r"Account type:\s*(.+?)\s{2,}|Account type:\s*(.+)$")

    # period: "on August 31 ($)   on September 30 ($)"  (+ the year from the header label)
    hdr = _find(lines, rf"\b{MON}\s+\d{{4}}\s*-\s*{MON}\s+(\d{{4}})") or _find(lines, rf"^(?:.*\s)?(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{{4}})\s*$")
    year = int(re.findall(r"(\d{4})", hdr.group(0))[-1]) if hdr else date.today().year
    period_label = re.sub(r"\s+", " ", re.search(rf"({MON}\s+\d{{4}}\s*-\s*{MON}\s+\d{{4}}|\w+\s+\d{{4}})\s*$", hdr.group(0)).group(1)) if hdr else None
    on = _find(lines, rf"on\s+{MON}\s+(\d{{1,2}})\s*\(\$\)\s+on\s+{MON}\s+(\d{{1,2}})\s*\(\$\)")
    if on:
        m1, d1, m2, d2 = MONTHS[on.group(1)[:3].lower()], int(on.group(2)), MONTHS[on.group(3)[:3].lower()], int(on.group(4))
        closing = date(year, m2, d2)
        opening = date(year if m1 <= m2 else year - 1, m1, d1)
    else:
        closing = opening = None

    out = {"number": number, "mask": digits[-4:], "code": code, "type": acct_type,
           "type_label": (label.group(1) or label.group(2)).strip() if label else "Resource Management Account",
           "period_label": period_label, "opening_date": opening and opening.isoformat(),
           "closing_date": closing and closing.isoformat()}

    v = _find(lines, rf"^Value of your account\s+({NUM})\s+({NUM})")
    out["opening_value"], out["closing_value"] = (num(v.group(1)), num(v.group(2))) if v else (None, None)
    if out["closing_value"] is None:
        v = _find(lines, rf"^Value of your account\s+({NUM})\s*$")
        out["closing_value"] = num(v.group(1)) if v else None

    # year-to-date "Sources of your account growth"
    ytd = {}
    m = _find(lines, rf"at year end (\d{{4}})\s+({NUM})")
    if m:
        ytd["start_year_end"] = int(m.group(1)); ytd["start_value"] = num(m.group(2))
    for key, pat in (("net_deposits", rf"withdrawals\s+({NUM})\s*$"), ("div_int", rf"interest income\s+({NUM})\s*$"),
                     ("market_change", rf"market value\s+({NUM})\s*$")):
        m = _find(lines, pat)
        if m:
            ytd[key] = num(m.group(1))
    out["ytd"] = ytd

    # "Change in the value of your account": period and year-to-date columns
    ch = {}
    ch["opening"], _ = _two(lines, r"Opening account value")
    ch["deposits"], ch["deposits_ytd"] = _two(lines, r"(?:Deposits, including investments\s*)?transferred in")
    ch["withdrawals"], ch["withdrawals_ytd"] = _two(lines, r"Withdrawals?(?:,? including[^\d$-]*| and other[^\d$-]*| from[^\d$-]*)?")
    ch["div_int"], ch["div_int_ytd"] = _two(lines, r"Dividend and interest income")
    ch["fees"], ch["fees_ytd"] = _two(lines, r"(?:Fees|Account fees|Advisory fees|Fees and charges)")
    ch["market_change"], ch["market_change_ytd"] = _two(lines, r"Change in market value")
    ch["closing"], _ = _two(lines, r"Closing account value")
    out["change"] = {k: v for k, v in ch.items() if v is not None}

    # gains and losses: "Short term  period  ytd  unrealized"
    rg = {}
    for key, pat in (("st", r"Short term"), ("lt", r"Long term")):
        m = _find(lines, rf"{pat}\s+({NUM})\s+({NUM})(?:\s+({NUM}))?")
        if m:
            rg[key] = {"period": num(m.group(1)), "ytd": num(m.group(2)), "unrealized": num(m.group(3)) if m.group(3) else None}
    out["gains"] = rg

    tax = {}
    for key, pat in (("dividends", rf"^(?:Taxable dividends|Dividends)\s+({NUM})\s+({NUM})\s*$"),
                     ("interest", rf"^(?:Taxable interest|Interest)\s+({NUM})\s+({NUM})\s*$")):
        m = _find(lines, pat)
        if m:
            tax[key] = num(m.group(1)); tax[key + "_ytd"] = num(m.group(2))
    out["income"] = tax

    alloc = {}
    for l in lines:
        m = re.match(r"\s*(?:[A-G]\s+)?(Cash and sweep balances|Cash and money balances|Cash alternatives|Equities|Fixed income|Non-traditional|Commodities|Other)\s+(" + NUM + r")\s+([\d.]+)%", l)
        if m:
            k = "cash" if m.group(1).lower().startswith("cash and") else ALLOC_KEYS[m.group(1).lower()]
            alloc[k] = num(m.group(2))
    out["allocation"] = alloc

    marks = []
    for l in lines:
        m = re.match(r"\s*(S&P 500|Russell 3000|MSCI - Europe, Australia & Far East|Barclays Capital U\.S\. Aggregate Bond Index|Dow Jones[^\d-]*|NASDAQ[^\d-]*)\s+(-?[\d.]+)%\s+(-?[\d.]+)%", l)
        if m and not any(x["index"] == m.group(1).strip() for x in marks):
            marks.append({"index": m.group(1).strip(), "period": float(m.group(2)), "ytd": float(m.group(3))})
    out["benchmarks"] = marks
    m = _find(lines, r"Percentage change\s*$")
    bm_month = _find(lines, r"^Index\s+(\w+ \d{4})\s+Year to date")
    out["benchmark_month"] = bm_month.group(1) if bm_month else None

    out["holdings"], out["eai_total"] = _holdings(lines)
    out["cash"] = alloc.get("cash")
    out["activity"] = _activity(lines, closing or date(year, 12, 31), out["holdings"])
    out["contributions"] = _contributions(lines)
    out["cost_basis_method"] = (_find(lines, r"default closing method is ([A-Za-z ,]+?)\.") or [None, None])[1]
    return out


def _holdings(lines):
    """Securities with their tax lots. A security's name is the run of capitalised lines before 'Symbol:'."""
    hold, cur, name_buf, eai_total = [], None, [], None
    started = False
    for l in lines:
        s = l.strip()
        if re.match(r"(Equities|Fixed income|Mutual funds|Closed end funds|Exchange traded|Options|Non-traditional)", s):
            started = True
        if not started:
            continue
        m = re.match(r"Symbol:\s*([A-Z0-9.\-]+)", s)
        if m:
            cur = {"symbol": m.group(1), "name": " ".join(name_buf).strip() or m.group(1), "lots": []}
            hold.append(cur); name_buf = []
            continue
        m = re.match(rf"Trade date:\s*{MON}\s+(\d{{1,2}}),\s*(\d{{2,4}})\s+(.*)$", s)
        if m and cur is not None:
            mon, d, y = MONTHS[m.group(1)[:3].lower()], int(m.group(2)), int(m.group(3))
            y = y + 2000 if y < 100 else y
            ns = _nums(m.group(4))
            term = re.search(r"\b(LT|ST)\b\s*$", s)
            if len(ns) >= 7:
                cur["lots"].append({"trade_date": date(y, mon, d).isoformat(), "quantity": ns[0], "avg_price": ns[1],
                                    "client_investment": ns[2], "cost_basis": ns[3], "price": ns[4], "value": ns[5],
                                    "unrealized": ns[6], "term": term.group(1) if term else None})
            continue
        m = re.match(r"EAI:\s*\$?([\d,.]+)\s+Current yield:\s*([\d.]+)%", s)
        if m:
            # the EAI line closes the most recent security (it can land on the next page)
            if hold:
                hold[-1]["eai"] = num(m.group(1)); hold[-1]["yield_pct"] = float(m.group(2))
            name_buf = []
            continue
        m = re.match(r"Total estimated annual income:\s*\$?([\d,.]+)", s)
        if m:
            eai_total = num(m.group(1)); started = False
            continue
        if re.match(r"(Security total|Total\s|Your total assets|Purchase price|Number Average|Holding\s|continued|Your assets|\(continued\)|\d+\s+\d+$|4\s+4$)", s) \
                or "Account name" in s or "Account number" in s or "Account type" in s or re.match(r"^(ANQ|ab$|Retirement Account|Resource Management)", s):
            if re.match(r"(Security total|Total\s)", s):
                name_buf = []
            continue
        if re.match(r"^[A-Z0-9][A-Z0-9 &.,'/\-]+$", s) and len(s) > 2 and not re.match(rf"^{MON}\s+\d{{4}}$", s, re.I):
            name_buf.append(s)
        elif re.match(rf"^({MON}\s+\d{{4}}|\w+ \d{{4}})", s):
            continue
        else:
            name_buf = [] if not s.isupper() else name_buf
    for h in hold:
        lots = h["lots"]
        h["quantity"] = round(sum(x["quantity"] for x in lots), 6)
        h["cost_basis"] = round(sum(x["cost_basis"] for x in lots), 2)
        h["value"] = round(sum(x["value"] for x in lots), 2)
        h["unrealized"] = round(sum(x["unrealized"] for x in lots), 2)
        h["price"] = lots[0]["price"] if lots else None
    return [h for h in hold if h["lots"]], eai_total


ACT_TYPES = {"deposit": "deposit", "withdrawal": "withdrawal", "dividend": "dividend", "interest": "interest",
             "transfer": "transfer_in", "journal": "transfer_in", "bought": "buy", "sold": "sell", "fee": "fee",
             "contribution": "contribution", "distribution": "distribution", "reinvest": "reinvest"}


def _activity(lines, closing, holdings):
    """Cash activity, dividends, trades and pending trades. Sweep moves (cash shuffled into the bank
    sweep) are internal and skipped; sweep *interest* is kept."""
    out, i = [], 0
    names = {re.sub(r"[^A-Z0-9]", "", h["name"].upper()): h["symbol"] for h in holdings}

    def year_for(mon):
        return closing.year if mon <= closing.month else closing.year - 1

    def symbol_from(desc, j):
        nxt = lines[j + 1].strip() if j + 1 < len(lines) else ""
        nxt2 = lines[j + 2].strip() if j + 2 < len(lines) else ""
        for cand in (nxt, nxt2):
            m = re.match(r"SYMBOL:\s*([A-Z0-9.\-]+)", cand, re.I)
            if m:
                return m.group(1).upper()
        key = re.sub(r"[^A-Z0-9]", "", desc.upper())
        best = max(names, key=lambda n: len(n) if n and (n in key or key in n) else -1, default=None)
        return names[best] if best and (best in key or key in best) else None

    in_sweep = False
    for j, l in enumerate(lines):
        s = l.strip()
        if s.startswith("Sweep balance activities"):
            in_sweep = True
        elif re.match(r"(Account activity|Investment transactions|Dividend and interest income$|Unsettled transaction|Deposits and other)", s):
            in_sweep = False
        # pending (unsettled) trades: "Sep 30, 26  Oct 01, 26  Bought NAME  qty  price  amount"
        m = re.search(rf"{MON}\s+(\d{{1,2}}),\s*(\d{{2}})\s+{MON}\s+(\d{{1,2}}),\s*(\d{{2}})\s+(Bought|Sold)\s+(.+?)\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)\s+({NUM})\s*$", s)
        if m:
            td = date(2000 + int(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
            sd = date(2000 + int(m.group(6)), MONTHS[m.group(4)[:3].lower()], int(m.group(5)))
            desc = m.group(8)
            out.append({"date": td.isoformat(), "settle_date": sd.isoformat(), "type": ACT_TYPES[m.group(7).lower()],
                        "description": desc, "symbol": symbol_from(desc, j), "quantity": num(m.group(9)),
                        "price": num(m.group(10)), "amount": num(m.group(11)), "pending": True})
            continue
        m = re.search(rf"(?:^|\s){MON}\s+(\d{{1,2}})\s+(Deposit|Withdrawal|Dividend|Interest|Transfer|Journal|Bought|Sold|Fee|Contribution|Distribution|Reinvest\w*)\s+(.+?)\s+({NUM})\s*$", s)
        if not m:
            continue
        mon, d, act, rest, amt = MONTHS[m.group(1)[:3].lower()], int(m.group(2)), m.group(3), m.group(4), num(m.group(5))
        if (in_sweep or "SWEEP" in rest.upper()) and act in ("Deposit", "Withdrawal"):
            continue
        if act == "Balance":
            continue
        typ = ACT_TYPES.get(act.lower().rstrip("ed").rstrip("e") if act.lower().startswith("reinvest") else act.lower(), "other")
        rec = {"date": date(year_for(mon), mon, d).isoformat(), "type": typ, "description": re.sub(r"\s+", " ", rest).strip(), "amount": amt}
        if typ in ("buy", "sell"):
            ns = re.findall(r"([\d,]+\.\d+)", rest)
            if len(ns) >= 2:
                rec["quantity"], rec["price"] = num(ns[-2]), num(ns[-1])
                rec["description"] = re.sub(r"\s+[\d,]+\.\d+\s+[\d,]+\.\d+$", "", rec["description"]).strip()
            rec["symbol"] = symbol_from(rec["description"], j)
        elif typ == "dividend":
            q = re.search(r"PAID ON\s+([\d,.]+)\s*$", rest)
            if q:
                rec["quantity"] = num(q.group(1))
                rec["description"] = rest[:q.start()].strip()
            rec["symbol"] = symbol_from(rec["description"], j)
        if typ == "transfer_in" and amt < 0:
            rec["type"] = "transfer_out"
        if typ == "deposit" and re.search(r"JOURNAL", rest, re.I):
            rec["type"] = "transfer_in"
        out.append(rec)
    return out


def _contributions(lines):
    hdr = _find(lines, r"Contribution type\s+Current period \(\$\)\s+Tax year (\d{4}) \(\$\)\s+Tax year (\d{4})")
    if not hdr:
        return {}
    y1, y2 = hdr.group(1), hdr.group(2)
    out = {}
    for l in lines:
        for row in CONTRIB_ROWS:
            m = re.search(rf"\b{row}\s+({NUM})\s+({NUM})\s+({NUM})\s*$", l, re.I)
            if m:
                key = row.lower().replace("-", "_").replace(" ", "_")
                out.setdefault(y1, {})[key] = num(m.group(2))
                out.setdefault(y2, {})[key] = num(m.group(3))
                break
    return out


# ---------------------------------------------------------------- tax forms
def is_tax_forms(pages):
    t = " ".join(" ".join(p[:40]) for p in pages[:4])
    return bool(re.search(r"Consolidated Form 1099|Form 1099-R|Form 5498|1099-DIV", t))


def parse_tax_forms(data, pages=None):
    pages = pages or pdf_pages(data)
    if not is_tax_forms(pages):
        raise ValueError("This doesn't look like a UBS tax-form PDF (1099 / 1099-R / 5498).")
    text = "\n".join("\n".join(p) for p in pages)
    ym = re.search(r"\b(20\d{2})\s+(?:Consolidated Form 1099|Form 1099-R|Form 5498)", text)
    year = int(ym.group(1)) if ym else date.today().year - 1
    forms = []

    def box(pattern, src=text):
        m = re.search(pattern, src, re.I)
        return num(m.group(1)) if m else None

    if re.search(r"Consolidated Form 1099", text):
        acct = re.search(r"Account Number:\s*(2T\s*\d{5})", text)
        forms.append({"form": "1099-DIV", "account_hint": acct.group(1) if acct else None,
                      "ordinary_dividends": box(r"1a-\s*Total ordinary dividends[^\n]*?\s([\d,]+\.\d{2})"),
                      "qualified_dividends": box(r"1b-\s*Qualified dividends\s+([\d,]+\.\d{2})"),
                      "capital_gain_distributions": box(r"2a-\s*Total capital gain distributions[^\n]*?\s([\d,]+\.\d{2})"),
                      "federal_withheld": box(r"1099-DIV total withheld\s+([\d,]+\.\d{2})"),
                      "foreign_tax": box(r"7-\s*Foreign tax paid\s+([\d,]+\.\d{2})")})
        forms.append({"form": "1099-INT", "account_hint": acct.group(1) if acct else None,
                      "interest": box(r"1-\s*Interest income \(not included in line 3\)\s+([\d,]+\.\d{2})"),
                      "us_treasury_interest": box(r"3-\s*Interest on US Savings Bonds[^\d]*([\d,]+\.\d{2})"),
                      "tax_exempt_interest": box(r"8-\s*Tax-exempt interest[^\d]*([\d,]+\.\d{2})"),
                      "federal_withheld": box(r"1099-INT total withheld\s+([\d,]+\.\d{2})")})
        b = {}
        for term, pat in (("short", r"^Short\s+[ABC] .*$"), ("long", r"^Long\s+[DEF] .*$")):
            tot = [0.0, 0.0, 0.0]
            for l in text.split("\n"):
                if re.match(pat, l.strip()):
                    ns = _nums(l)
                    if len(ns) >= 3:
                        tot = [tot[0] + ns[0], tot[1] + ns[1], tot[2] + (ns[4] if len(ns) > 4 else ns[0] - ns[1])]
            b[term] = {"proceeds": round(tot[0], 2), "cost": round(tot[1], 2), "gain": round(tot[2], 2)}
        forms.append({"form": "1099-B", "account_hint": acct.group(1) if acct else None, **b})

    seen_r = set()
    for m in re.finditer(r"Form 1099-R(.{0,2500})", text, re.S):
        chunk = m.group(1)
        gross = box(r"1 Gross distribution\s+\$?([\d,]+\.\d{2})", chunk)
        if gross is None:
            continue
        acct = re.search(r"Account number:\s*(2T\s*\d{5})", chunk)
        code = re.search(r"7 Distribution code\(s\)\s+([0-9A-Z]{1,2})\b", chunk)
        key = (acct.group(1) if acct else None, gross)
        if key in seen_r:
            continue    # the form repeats as Copy B / C / 2
        seen_r.add(key)
        forms.append({"form": "1099-R", "account_hint": key[0], "gross_distribution": gross,
                      "taxable_amount": box(r"2a Taxable amount\s+\$?([\d,]+\.\d{2})", chunk),
                      "federal_withheld": box(r"4 Federal income tax withheld\s+\$?([\d,]+\.\d{2})", chunk),
                      "distribution_code": code.group(1) if code else None})

    for m in re.finditer(r"Form 5498\s+OMB No.*?(?=Form 5498\s+OMB No|\Z)", text, re.S):
        chunk = m.group(0)
        acct = re.search(r"Account number:\s+(2T\s*\d{5})", chunk)
        forms.append({"form": "5498", "account_hint": acct.group(1) if acct else None,
                      "ira_contributions": box(r"\n1\s+IRA contributions[^\n]*?\$([\d,]+\.\d{2})", chunk),
                      "rollover_contributions": box(r"\n2\s+Rollover contributions\s+\$([\d,]+\.\d{2})", chunk),
                      "roth_conversion": box(r"\n3\s+Roth IRA conversion amount\s+\$([\d,]+\.\d{2})", chunk),
                      "fmv": box(r"\n5\s+Fair market value of account\s+\$([\d,]+\.\d{2})", chunk),
                      "roth_contributions": box(r"\n10\s+Roth IRA contributions\s+\$([\d,]+\.\d{2})", chunk),
                      "plan_type": (re.search(r"Plan type\s+(\w+)", chunk) or [None, None])[1]})
    return {"kind": "ubs_tax_forms", "year": year, "forms": forms}


def parse_any(data):
    pages = pdf_pages(data)
    if is_tax_forms(pages) and not is_statement(pages):
        return parse_tax_forms(data, pages)
    return parse_statement(data, pages)

"""Ledger tests: no double counting across PDF / CSV / Plaid, transfer pairing, statement reconciliation, taxes.

Run from backend/:  python -m pytest -q tests
The UBS statement tests need the real PDFs, which never go in the repo. Point LIFETRACKER_FIXTURES at a folder
holding them (any file names; statements must contain "Statement", tax forms "Tax_Forms"); those tests are
skipped otherwise. The synthetic tests always run.
"""
import glob
import os
import sys
from functools import wraps

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import fake_firestore  # noqa: E402

fake_firestore.install()

from flask import Flask, g  # noqa: E402

import earnings  # noqa: E402
import finance  # noqa: E402
import fintax  # noqa: E402
import ledger as L  # noqa: E402
import portfolio as P  # noqa: E402

FIX = os.environ.get("LIFETRACKER_FIXTURES", "")
STATEMENTS = sorted(glob.glob(os.path.join(FIX, "*Statement*.pdf"))) if FIX else []
TAX_FORMS = sorted(glob.glob(os.path.join(FIX, "*Tax_Forms*.pdf"))) if FIX else []
needs_pdfs = pytest.mark.skipif(len(STATEMENTS) < 4, reason="set LIFETRACKER_FIXTURES to the folder with the UBS PDFs")


@pytest.fixture()
def app():
    db = fake_firestore.Client()
    ur = lambda: db.collection("users").document("me")

    def owner(fn):
        @wraps(fn)
        def w(*a, **k):
            g.uid = "me"
            return fn(*a, **k)
        return w
    a = Flask(__name__)
    earnings.db, earnings.user_ref = db, ur
    finance.init(a, db, fake_firestore.sys.modules["google.cloud.firestore"], owner, ur)
    a.db = db
    return a


def active_totals():
    acts = L.load_activity()
    tx = L.load_transactions("2000-01-01", "2999-12-31")
    return (len(acts), round(sum(x["amount"] for x in acts), 2), len(tx), round(sum(t["amount"] for t in tx), 2))


def add_tx(tid, account, d, amount, src, merchant="X", category="Other"):
    L.col("transactions").document(tid).set({"account": account, "date": d, "amount": amount, "src": src,
                                              "merchant": merchant, "category": category, "source": src})


# ---------------------------------------------------------------- synthetic (always run)
def test_same_source_identical_charges_are_not_merged(app):
    with app.app_context():
        L.ensure_defaults()
        add_tx("a", "chase_card", "2026-08-01", 5.25, "chase_csv", "COFFEE")
        add_tx("b", "chase_card", "2026-08-01", 5.25, "chase_csv", "COFFEE")
        L.dedupe_transactions("chase_card", "2026-08-01", "2026-08-01")
        assert len(L.load_transactions("2026-08-01", "2026-08-01")) == 2


def test_csv_and_plaid_copy_count_once_and_plaid_wins(app):
    with app.app_context():
        L.ensure_defaults()
        add_tx("csv1", "chase_card", "2026-08-01", 42.10, "chase_csv", "DINNER")
        add_tx("csv2", "chase_card", "2026-08-01", 42.10, "chase_csv", "DINNER")      # two real dinners
        add_tx("pl1", "chase_card", "2026-08-02", 42.10, "plaid", "Dinner Place")      # Plaid has both, posted a day later
        add_tx("pl2", "chase_card", "2026-08-02", 42.10, "plaid", "Dinner Place")
        L.dedupe_transactions("chase_card", "2026-08-01", "2026-08-02")
        rows = L.load_transactions("2026-07-01", "2026-09-01")
        assert sorted(r["id"] for r in rows) == ["pl1", "pl2"]
        # Plaid removes one of its copies -> the CSV row it had replaced counts again
        L.col("transactions").document("pl2").delete()
        L.dedupe_transactions("chase_card", "2026-08-01", "2026-08-02")
        assert len(L.load_transactions("2026-07-01", "2026-09-01")) == 2


def test_far_apart_or_different_amounts_are_not_matched(app):
    with app.app_context():
        L.ensure_defaults()
        add_tx("c", "chase_checking", "2026-08-01", 100.0, "chase_csv")
        add_tx("p", "chase_checking", "2026-08-09", 100.0, "plaid")
        add_tx("q", "chase_checking", "2026-08-01", 100.01, "plaid")
        L.dedupe_transactions("chase_checking", "2026-08-01", "2026-08-09")
        assert len(L.load_transactions("2026-07-01", "2026-09-01")) == 3


def test_different_accounts_never_match(app):
    with app.app_context():
        L.ensure_defaults()
        add_tx("c", "chase_checking", "2026-08-01", 20.0, "chase_csv")
        add_tx("v", "venmo", "2026-08-01", 20.0, "venmo_csv")
        L.dedupe_all()
        assert len(L.load_transactions("2026-07-01", "2026-09-01")) == 2


# ---------------------------------------------------------------- with the real UBS statements
def _upload(client, paths):
    files = [(open(p, "rb"), os.path.basename(p)) for p in paths]
    r = client.post("/api/fin/upload", data={"files": files}, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


@needs_pdfs
def test_statements_import_once_reimport_is_a_noop(app):
    c = app.test_client()
    with app.app_context():
        out = _upload(c, STATEMENTS)
        assert not [r for r in out["results"] if r.get("error")]
        accts = {a["type"] for a in L.accounts() if a["institution"] == "UBS"}
        assert accts == {"brokerage", "roth_ira", "rollover_ira"}
        before = active_totals()
        _upload(c, STATEMENTS)
        _upload(c, list(reversed(STATEMENTS)))
        assert active_totals() == before


@needs_pdfs
def test_reconciliation_to_the_cent(app):
    c = app.test_client()
    with app.app_context():
        _upload(c, STATEMENTS)
        rec = P.reconciliation()
        assert len(rec) >= 9
        bad = [(r["account_name"], r["period"], k["check"], k["diff"]) for r in rec for k in r["checks"] if not k["ok"] and not k.get("partial_ok")]
        assert not bad, bad


@needs_pdfs
def test_plaid_sync_over_the_same_period_changes_nothing(app):
    c = app.test_client()
    with app.app_context():
        _upload(c, STATEMENTS)
        before = active_totals()
        # Plaid reports the same activity (dates can shift a day or two): every PDF row gets a Plaid twin
        for a in L.load_activity():
            d = L._shift(a["date"], 1)
            L.col("inv_activity").document("plaid_" + a["id"]).set({**{k: v for k, v in a.items() if k != "id"}, "date": d, "src": "plaid", "statement": None})
        for acct in {a["account"] for a in L.load_activity(include_superseded=True)}:
            L.dedupe_activity(acct, "2026-01-01", "2026-12-31")
        after = active_totals()
        assert after[0] == before[0] and after[1] == before[1]
        assert all(a["src"] == "plaid" for a in L.load_activity())
        # and the PDFs uploaded again on top of Plaid still don't add anything
        _upload(c, STATEMENTS)
        assert active_totals()[:2] == before[:2]


@needs_pdfs
def test_transfer_from_chase_is_paired_and_not_spending(app):
    c = app.test_client()
    with app.app_context():
        _upload(c, STATEMENTS)
        dep = [a for a in L.load_activity() if L.CHASE_IN.search(a.get("description") or "")]
        assert dep, "expected a deposit that came from Chase"
        d = dep[0]
        # the same money leaving Chase checking, as a CSV row posted a day earlier
        add_tx("chase_out", "chase_checking", L._shift(d["date"], -1), d["amount"], "chase_csv", "Online Transfer to UBS", "Other")
        L.pair_transfers()
        t = L.col("transactions").document("chase_out").get().to_dict()
        assert t["category"] == "Transfer" and t["transfer_pair"] == d["id"]
        assert L.col("inv_activity").document(d["id"]).get().to_dict()["type"] == "transfer_in"
        cv = finance.cash_view("all", L._shift(d["date"], -5), L._shift(d["date"], 5))
        assert cv["spent"] == 0 and cv["transfers"] == d["amount"]


@needs_pdfs
def test_trust_distribution_is_income_not_a_transfer(app):
    c = app.test_client()
    with app.app_context():
        _upload(c, STATEMENTS)
        trust = [a for a in L.load_activity() if a["type"] == "trust_distribution"]
        assert len(trust) == 1
        st = next(s for s in L.statements() if s["opening_date"] < trust[0]["date"] <= s["closing_date"] and s["account"] == trust[0]["account"])
        assert trust[0]["amount"] == st["change"]["deposits"]          # all of that month's deposits
        assert finance.income_view(int(trust[0]["date"][:4]))["trust_total"] == trust[0]["amount"]


@needs_pdfs
def test_multi_month_retirement_periods_dont_double(app):
    c = app.test_client()
    with app.app_context():
        _upload(c, STATEMENTS)
        roth = next(a["id"] for a in L.accounts() if a["type"] == "roth_ira")
        divs = [a for a in L.load_activity(account=roth) if a["type"] == "dividend"]
        keys = [(a["date"], a["symbol"], a["amount"]) for a in divs]
        assert len(keys) == len(set(keys))
        # Roth dividends + interest Jun-Sep on file = sum of the statements' own period income
        sts = P.period_returns(roth)
        assert round(sum(p["dividends"] or 0 for p in sts if p["start"] >= "2026-05-29"), 2) == \
            round(sum(a["amount"] for a in L.load_activity("2026-05-30", "2026-09-30", account=roth) if a["type"] in ("dividend", "interest")), 2)


@needs_pdfs
def test_net_worth_and_taxes_compute(app):
    c = app.test_client()
    with app.app_context():
        _upload(c, STATEMENTS + TAX_FORMS)
        latest = {}
        for s in L.statements():
            latest[s["account"]] = s["closing_value"]
        nw = P.net_worth()
        assert nw["net_worth"] == round(sum(latest.values()), 2)
        t = fintax.compute(2026)
        trust = sum(a["amount"] for a in L.load_activity() if a["type"] == "trust_distribution")
        assert t["trust"]["taxable"] == trust and t["federal"]["agi"] >= trust
        roth = next(a["id"] for a in L.accounts() if a["type"] == "roth_ira")
        st = L.statements(roth)[-1]
        assert t["roth"]["contributed"] == st["contributions"]["2026"]["non_deductible"]
        if TAX_FORMS:
            r = P.roth_detail(roth, fintax.settings())
            form = next(d.to_dict() for d in L.col("tax_docs").stream() if d.to_dict()["form"] == "1099-R")
            assert any(x["amount"] == form["gross_distribution"] for x in r["conversions"])

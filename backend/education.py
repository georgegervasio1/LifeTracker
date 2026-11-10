"""Education: courses, grades & GPA, degree audit, and course planning.

Data (per user, under users/{uid}/):
  education/profile       -> school, program, terms, target GPA, transfer credits, requirement config
  edu_courses/{id}        -> one doc per course in any term (taken, current, planned or being considered)
  edu_syllabi/{id}        -> syllabi and their deadlines (see syllabus.py)

All GPA math lives in compute() so the API, the UI and (later) the AI assistant agree.
"""
import math
import re
from flask import Blueprint, jsonify, request
import schools

bp = Blueprint("education", __name__)
db = firestore = require_owner = user_ref = None

# Georgetown Law grade points (students matriculating Fall 1998+; A+ = 4.33 since Summer 2020).
GRADE_POINTS = {"A+": 4.33, "A": 4.00, "A-": 3.67, "B+": 3.33, "B": 3.00, "B-": 2.67,
                "C+": 2.33, "C": 2.00, "C-": 1.67, "D": 1.00, "F": 0.00}
NON_GPA_GRADES = {"P", "H", "CR", "AP", "NG", "AU", "W", "IP"}   # earn credit (except W/AU/IP) but not in GPA
NO_CREDIT_GRADES = {"W", "AU", "F"}

STATUSES = ["completed", "enrolled", "planned", "waitlisted", "considering", "dropped"]
COUNTS_TOWARD_DEGREE = {"completed", "enrolled", "planned"}      # what the audit and projection assume you'll take
TAGS = {"pr": "Professional Responsibility", "ulwr": "Upperclass Legal Writing",
        "experiential": "Experiential", "ilo8": "Critical perspectives (ILO #8)"}
ALL_TAGS = {**schools.ALL_TAGS, "major": "Counts toward my major"}
ALL_GRADES = set(GRADE_POINTS) | set(NON_GPA_GRADES)
for _s in schools.SCHOOLS.values():
    ALL_GRADES |= set(_s["grade_points"]) | set(_s["non_gpa_grades"])

DEFAULT_PROFILE = {
    "school": "Georgetown University Law Center",
    "program": "J.D.",
    "terms": ["Fall 2025", "Spring 2026", "Fall 2026", "Spring 2027"],
    "target_gpa": 3.70,
    "transfer_credits": 0,
    "requirements": {
        "total_credits": 85,
        "law_center_credits": 54,
        "experiential_credits": 6,
        "min_gpa": 2.00,
        "require_pr": True,
        "require_ulwr": True,
        "require_ilo8": True,
        "pf_cap": 7,              # upperclass J.D. pass/fail option: 7 elective credits, one course per semester
    },
    # Published GULC graduation-honors cutoffs, for reference only (honors are class-rank based).
    "honors_reference": [
        {"label": "Cum laude (top third)", "examples": [{"year": "2015", "gpa": 3.56}, {"year": "2022", "gpa": 3.72}]},
        {"label": "Magna cum laude (top 10%)", "examples": [{"year": "2015", "gpa": 3.73}, {"year": "2022", "gpa": 3.86}]},
    ],
}


def init(app, _db, _firestore, _require_owner, _user_ref):
    global db, firestore, require_owner, user_ref
    db, firestore, require_owner, user_ref = _db, _firestore, _require_owner, _user_ref
    _register_routes()
    app.register_blueprint(bp)


# GULC 2026-2027 academic calendar (Registrar, Aug 7 2026). 1L-only items (orientation, Legal Practice) left out.
# kind: classes | deadline | holiday | exams | break | other
ACADEMIC_CALENDAR = {
    "Fall 2026": [
        ("2026-08-17", None, "Tuition due (continuing & transfer students)", "deadline"),
        ("2026-08-24", None, "Classes begin", "classes"),
        ("2026-09-01", None, "Last day to add/drop", "deadline"),
        ("2026-09-07", None, "Labor Day: no classes", "holiday"),
        ("2026-10-12", None, "Mid-semester holiday: no classes", "holiday"),
        ("2026-10-13", None, "Monday classes meet instead of Tuesday", "other"),
        ("2026-10-20", "2026-10-27", "Minicourse / Bookends exam period", "exams"),
        ("2026-11-24", None, "Last day of classes", "classes"),
        ("2026-11-25", "2026-11-29", "Thanksgiving break", "break"),
        ("2026-11-30", "2026-12-02", "Rescheduled classes & reading days", "other"),
        ("2026-12-03", "2026-12-14", "Final exam period", "exams"),
        ("2026-12-14", None, "Papers due (unless professor sets another date)", "deadline"),
        ("2026-12-15", "2026-12-18", "Exam deferral dates", "exams"),
    ],
    "Spring 2027": [
        ("2027-01-04", None, "Week One minicourses begin", "classes"),
        ("2027-01-05", None, "Spring tuition due", "deadline"),
        ("2027-01-11", None, "Classes begin", "classes"),
        ("2027-01-18", None, "Martin Luther King Jr. Day: no classes", "holiday"),
        ("2027-01-19", None, "Last day to add/drop", "deadline"),
        ("2027-01-21", "2027-01-28", "Week One exam period", "exams"),
        ("2027-02-15", None, "Presidents Day: no classes", "holiday"),
        ("2027-02-16", None, "Monday classes meet instead of Tuesday", "other"),
        ("2027-02-28", "2027-03-07", "Spring break", "break"),
        ("2027-03-16", "2027-03-23", "Minicourse / Bookends exam period", "exams"),
        ("2027-03-26", "2027-03-28", "Easter break", "break"),
        ("2027-04-21", None, "Last day of classes (Friday schedule)", "classes"),
        ("2027-04-22", "2027-04-26", "Rescheduled classes & reading days", "other"),
        ("2027-04-27", "2027-05-11", "Final exam period", "exams"),
        ("2027-05-11", None, "Papers due (unless professor sets another date)", "deadline"),
        ("2027-05-12", "2027-05-14", "Exam deferral dates", "exams"),
        ("2027-05-20", "2027-05-23", "Commencement", "other"),
        ("2027-06-02", None, "Degrees conferred", "other"),
    ],
}


# Registrar's Fall 2026 in-class final exam schedule (upperclass courses; 1L sections and Legal Practice left out).
# (course as listed, professor, date, time)
FINALS_SCHEDULE = {"Fall 2026": [
    ("Arbitration Practice in China", "Tao", "2026-10-24", "10:00 AM"),
    ("Preliminary and Parallel Corp Litigation", "LeGrow", "2026-10-24", "10:00 AM"),
    *[(t, pr, "2026-12-04", "9:00 AM") for t, pr in [("Constitutional Law II", "Seidman"), ("Corporations", "Sale"), ("Intelligence Law", "Donohue"),
      ("Public Health Law and Ethics", "Gostin"), ("Professional Responsibility", "Coffey")]],
    *[(t, pr, "2026-12-04", "1:30 PM") for t, pr in [("Bankruptcy Law", "Flax"), ("Commercial Finance Transaction", "Levitin"), ("Environmental Law", "Buzbee"),
      ("Evidence", "Iscoe"), ("Federal Income Taxation", "Satterthwaite"), ("Innovation, Tech & Intl Fin Reg", "Brummer")]],
    *[(t, pr, "2026-12-05", "10:00 AM") for t, pr in [("Beyond the IPO", "Lopez"), ("Corp Income Tax Law I", "Decker"), ("Crypto Law Seminar", "McCarty"),
      ("Law of Public Utilities", "Hempling"), ("Intl Organizations", "Bischoff"), ("Intl Trade Law & Regulation", "Brightbill")]],
    *[(t, pr, "2026-12-07", "6:00 PM") for t, pr in [("Antitrust", "Pitt"), ("Civil Rights: Section 1983", "Lopez"), ("Employment Discrimination", "Simmons"),
      ("Evidence", "Fisher"), ("Professional Responsibility", "Gomez")]],
    *[(t, pr, "2026-12-08", "1:30 PM") for t, pr in [("Corporations", "Diamond"), ("Federal Income Taxation", "Grinberg"), ("Prof Resp Amer Legal Profession", "Rostain"),
      ("Copyright Law", "Garcia"), ("Corporate Finance", "Flax")]],
    *[(t, pr, "2026-12-08", "6:00 PM") for t, pr in [("Devel & Finance Infra Projects", "Cohen"), ("Essentials of Fintech Law", "Scopino"),
      ("Fed Courts and Fed System", "Raab"), ("National Security & Law of the Sea", "Oliver")]],
    *[(t, pr, "2026-12-09", "9:00 AM") for t, pr in [("Corporations", "Langevoort"), ("Evidence", "Rostain"), ("Lab of Dem: State & Local Gov", "Chertoff"),
      ("Securities Regulation", "Wishnick")]],
    *[(t, pr, "2026-12-09", "1:30 PM") for t, pr in [("Corporations", "De Amicis"), ("Criminal Law", "Butler"), ("Intl Tax", "Grinberg"),
      ("Comparative Law: China in Context", "Jia"), ("Professional Responsibility Law in US", "Cheng")]],
    *[(t, pr, "2026-12-10", "6:00 PM") for t, pr in [("Corporations", "Goldsmith Romero"), ("Family Law II", "Holloway"), ("Emerging Companies", "Rattigan"),
      ("Intl Project Finance", "Hansen"), ("Patent Law", "Micallef"), ("Law of Investment", "O'Brien")]],
    *[(t, pr, "2026-12-11", "9:00 AM") for t, pr in [("Bus Essentials: Mini-MBA", "Hills"), ("Employment Law", "Rogers"), ("Intl Bus Transactions", "DeAmicis"),
      ("Federal Courts", "Lederman")]],
    *[(t, pr, "2026-12-11", "1:30 PM") for t, pr in [("Business Torts", "Klass"), ("Election Law", "Smith"), ("Constitutional Law II", "Barnett"),
      ("Payment Systems", "Levitin")]],
    *[(t, pr, "2026-12-14", "9:00 AM") for t, pr in [("Corporate Nat'l Security Law", "Carter"), ("Evidence", "Pardo"), ("Mergers and Acquisitions", "Thompson"),
      ("Information Privacy Law", "Cohen")]],
    *[(t, pr, "2026-12-14", "1:30 PM") for t, pr in [("Gender and Sexuality", "Mezey"), ("Legislation", "Krishnakumar"), ("Federal White Collar Crime", "O'Sullivan"),
      ("Advance Criminal Procedure", "Gunja")]],
    *[(t, pr, "2026-12-14", "6:00 PM") for t, pr in [("Securities Regulation", "Cordisco"), ("Securities Regulation", "Cavanagh")]],
]}
_ABBR = {"corp": "corporate", "corporations": "corporation", "intl": "international", "fed": "federal", "bus": "business",
         "devel": "development", "prof": "professional", "resp": "responsibility", "amer": "american", "natl": "national",
         "tech": "technology", "fin": "financial", "reg": "regulation", "infra": "infrastructure", "crim": "criminal",
         "dem": "democracy", "gov": "government", "advance": "advanced"}
_STOP = {"law", "laws", "the", "of", "and", "for", "in", "to", "a", "an", "lawyer", "lawyers", "us", "on", "seminar"}


def _tokens(title):
    out = set()
    for w in re.findall(r"[a-z0-9]+", (title or "").lower().replace("'", "").replace("-", " ")):
        w = _ABBR.get(w, w)
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if w not in _STOP:
            out.add(w)
    return out


def exam_candidates(course):
    """Registrar exam slots that could be this course: every meaningful word of the course title appears
    in the listed name; narrowed by professor when the course has one."""
    want = _tokens(course.get("title"))
    if not want:
        return []
    found = [{"title": t, "professor": pr, "date": d, "time": tm}
             for t, pr, d, tm in FINALS_SCHEDULE.get(course.get("term"), []) if want <= _tokens(t)]
    prof = (course.get("professor") or "").lower()
    if prof and len(found) > 1:
        narrowed = [f for f in found if f["professor"].lower() in prof or prof in f["professor"].lower()]
        found = narrowed or found
    return found


def calendar_for(courses, pack=None):
    """Key dates per term plus a 'final' event for every current/planned course with an exam date.
    Two finals in the same slot are flagged (GULC lets you reschedule an exam conflict)."""
    pack = pack or schools.GULC
    osu = pack["id"] == "osu"
    out = {}
    for term, items in (pack.get("calendar") or ACADEMIC_CALENDAR).items():
        out[term] = [{"date": d, "end": e, "label": l, "kind": k} for d, e, l, k in items]
    slots = {}
    for c in courses:
        if c.get("status") not in ("enrolled", "planned", "completed"):
            continue
        date, time, source = c.get("exam_date"), c.get("exam_time"), "you"
        paper = (c.get("exam_type") or "").lower() == "paper"
        if osu and not date and not paper and c.get("status") != "completed" and (c.get("exam_type") or "") != "None":
            slot = schools.osu_final_for(c.get("term"), (c.get("meetings") or [])[:1])
            if slot:
                date, time, source = slot["date"], slot["time"], "registrar"
        elif not osu and not date and not paper and c.get("graded", True) is not False and c.get("term") in FINALS_SCHEDULE:
            cands = exam_candidates(c)
            if len(cands) == 1:
                date, time, source = cands[0]["date"], cands[0]["time"], "registrar"
            elif cands:
                c["exam_options"] = cands      # several sections: let the student pick
        if date:
            ev = {"date": date, "end": None, "label": f"{'Paper due' if paper else 'Final'}: {c.get('title')}", "kind": "final",
                  "time": time, "course_id": c.get("id"), "exam_type": c.get("exam_type"), "source": source}
            out.setdefault(c.get("term") or "Other", []).append(ev)
            slots.setdefault((date, (time or "").upper()), []).append(ev)
    for evs in slots.values():
        evs = [ev for ev in evs if ev["label"].startswith("Final:")]    # papers don't clash with exams
        if len(evs) > 1:
            for ev in evs:
                ev["conflict"] = [x["label"].split(": ", 1)[1] for x in evs if x is not ev]
    for evs in out.values():
        evs.sort(key=lambda x: (x["date"], x["kind"] != "final", _time_key(x.get("time"))))
    return out


def _time_key(t):
    return _minutes(_to_24h(t)) if t and _to_24h(t) else 0


# ---------------------------------------------------------------- parsing helpers
DAY_CODES = {"M": "M", "T": "T", "W": "W", "R": "R", "TH": "R", "F": "F", "S": "S", "U": "U"}


def parse_meetings(text):
    """'MW 11:10-12:35, T 5:45pm-7:45pm' -> [{'days': ['M','W'], 'start': '11:10', 'end': '12:35'}, ...]
    Times without am/pm are read as class hours: 7:00-11:59 am, otherwise pm."""
    out = []
    for part in re.split(r"[;,]", text or ""):
        m = re.match(r"\s*([MTWRFSUH/]+?)\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*[-–]\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*$",
                     part.strip(), re.I)
        if not m:
            continue
        raw_days = m.group(1).upper().replace("/", "")
        days, i = [], 0
        while i < len(raw_days):
            if raw_days[i:i + 2] == "TH":
                days.append("R"); i += 2
            else:
                if raw_days[i] in DAY_CODES:
                    days.append(DAY_CODES[raw_days[i]])
                i += 1
        start, end = _to_24h(m.group(2)), _to_24h(m.group(3))
        if days and start and end:
            if _minutes(end) <= _minutes(start) and int(end[:2]) < 12:   # "5:45-7:45" -> end is evening too
                end = f"{int(end[:2]) + 12:02d}{end[2:]}"
            out.append({"days": days, "start": start, "end": end})
    return out


def _to_24h(tok):
    tok = tok.strip().lower()
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", tok)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h != 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    elif not ap and 1 <= h <= 6:          # bare 1:20 / 5:45 are afternoon/evening classes
        h += 12
    return f"{h:02d}:{mi:02d}"


def _minutes(t):
    return int(t[:2]) * 60 + int(t[3:])


def meetings_overlap(a, b):
    for x in a or []:
        for y in b or []:
            if set(x["days"]) & set(y["days"]) and _minutes(x["start"]) < _minutes(y["end"]) and _minutes(y["start"]) < _minutes(x["end"]):
                return True
    return False


# ---------------------------------------------------------------- the engine
def r2(x):
    """Round half-up to two decimals (GULC reports averages this way; Python's round() is half-even)."""
    return None if x is None else math.floor(x * 100 + 0.5 + 1e-9) / 100


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def school_defaults(pack):
    if pack["id"] == "gulc":
        return DEFAULT_PROFILE
    return {"school": pack["name"], "program": pack["program"], "terms": pack["terms"], "target_gpa": None,
            "transfer_credits": 0, "requirements": dict(pack["requirements"]), "honors_reference": []}


def compute(profile, courses):
    pack = schools.get((profile or {}).get("school_id"))
    base = school_defaults(pack)
    p = {**base, **(profile or {})}
    req = {**base["requirements"], **(p.get("requirements") or {})}
    GRADE_POINTS = pack["grade_points"]
    NON_GPA_GRADES = set(pack["non_gpa_grades"])
    NO_CREDIT_GRADES = set(pack["no_credit_grades"])
    pf_grade = pack["pf_grade"]
    terms = list(p["terms"])
    for c in courses:
        if c.get("term") and c["term"] not in terms:
            terms.append(c["term"])

    def pf_option_used(c):
        """Elected explicitly, or a finished course graded P that isn't marked as a mandatory pass/fail course."""
        if c.get("pf_elected"):
            return True
        return (c.get("status") == "completed" and (c.get("grade") or "").upper() == pf_grade
                and not c.get("pf_mandatory") and _num(c.get("credits")) > 0)

    def pf_pending(c):          # elected P/F, no final grade yet -> plan on a P (no GPA effect)
        return bool(c.get("pf_elected")) and c.get("grade_status") != "actual"

    def graded(c):
        return (c.get("grade") or "") in GRADE_POINTS and not pf_pending(c)

    # ---- per-term and cumulative GPA (actual = official grades only; projected adds expected grades)
    term_rows, cum = [], {"act_qp": 0.0, "act_gc": 0.0, "proj_qp": 0.0, "proj_gc": 0.0}
    for t in terms:
        tc = [c for c in courses if c.get("term") == t and c.get("status") in COUNTS_TOWARD_DEGREE]
        row = {"term": t, "credits": 0.0, "act_qp": 0.0, "act_gc": 0.0, "proj_qp": 0.0, "proj_gc": 0.0}
        for c in tc:
            cr = _num(c.get("credits"))
            if (c.get("grade") or "") not in NO_CREDIT_GRADES:
                row["credits"] += cr
            if graded(c):
                pts = GRADE_POINTS[c["grade"]] * cr
                row["proj_qp"] += pts; row["proj_gc"] += cr
                if c.get("grade_status") == "actual":
                    row["act_qp"] += pts; row["act_gc"] += cr
        for k in ("act_qp", "act_gc", "proj_qp", "proj_gc"):
            cum[k] += row[k]
        row["gpa_actual"] = r2(row["act_qp"] / row["act_gc"]) if row["act_gc"] else None
        row["gpa_projected"] = r2(row["proj_qp"] / row["proj_gc"]) if row["proj_gc"] else None
        row["status"] = ("completed" if tc and all(c.get("status") == "completed" for c in tc)
                         else "enrolled" if any(c.get("status") == "enrolled" for c in tc)
                         else "planned" if tc else "empty")
        term_rows.append(row)

    gpa_actual = r2(cum["act_qp"] / cum["act_gc"]) if cum["act_gc"] else None
    gpa_projected = r2(cum["proj_qp"] / cum["proj_gc"]) if cum["proj_gc"] else None

    # ---- what average do you need on the remaining graded credits to reach the target?
    target = _num(p.get("target_gpa"), None) if p.get("target_gpa") is not None else None
    gmax = pack["gpa_max"]
    remaining = [c for c in courses if c.get("status") in ("enrolled", "planned")
                 and c.get("grade_status") != "actual" and not pf_pending(c)
                 and (c.get("graded", True) and (c.get("grade") or "") not in NON_GPA_GRADES)]
    rem_gc = sum(_num(c.get("credits")) for c in remaining)
    needed = None
    if rem_gc and target is not None:
        avg = (target * (cum["act_gc"] + rem_gc) - cum["act_qp"]) / rem_gc
        needed = {"average": round(avg, 3), "graded_credits": rem_gc, "achievable": avg <= gmax,
                  "already_met": avg <= 0, "plan": _grade_plan(remaining, avg, pack)}

    # ---- degree audit
    def status_of(cs):
        if not cs:
            return "missing"
        return "met" if any(c.get("status") == "completed" for c in cs) else "on_track"

    active = [c for c in courses if c.get("status") in COUNTS_TOWARD_DEGREE and (c.get("grade") or "") not in NO_CREDIT_GRADES]
    earned = sum(_num(c.get("credits")) for c in active if c.get("status") == "completed")
    in_prog = sum(_num(c.get("credits")) for c in active if c.get("status") == "enrolled")
    planned = sum(_num(c.get("credits")) for c in active if c.get("status") == "planned")
    transfer = _num(p.get("transfer_credits"))
    total_req = _num(req["total_credits"], 85)
    lc_req = _num(req.get("law_center_credits"), 54)

    def credit_item(key, label, have_done, have_prog, have_plan, need):
        total = have_done + have_prog + have_plan
        return {"key": key, "label": label, "type": "credits", "need": need, "done": have_done,
                "in_progress": have_prog, "planned": have_plan,
                "status": "met" if have_done >= need else "on_track" if total >= need else "missing",
                "short_by": max(0.0, need - total)}

    major = None
    if pack["id"] == "osu":
        major_id = p.get("major_id") or pack["default_major"]
        major = pack["majors"].get(major_id) or pack["majors"]["other"]
        total_req = _num(major.get("total_credits"), total_req) if not (profile or {}).get("requirements", {}).get("total_credits") else total_req
        audit = osu_audit(pack, major, req, active, earned, in_prog, planned, transfer, total_req, credit_item,
                          GRADE_POINTS, NON_GPA_GRADES)
    else:
        audit = [credit_item("total", f"Total credits ({int(total_req)})", earned + transfer, in_prog, planned, total_req),
                 credit_item("law_center", f"Law Center credits ({int(lc_req)})", earned, in_prog, planned, lc_req)]

    if pack["id"] == "gulc":
        exp_courses = [c for c in active if "experiential" in (c.get("tags") or [])]
        exp_val = lambda c: _num(c.get("experiential_credits"), _num(c.get("credits")))
        e_item = credit_item("experiential", f"Experiential credits ({int(req['experiential_credits'])})",
                             sum(exp_val(c) for c in exp_courses if c.get("status") == "completed"),
                             sum(exp_val(c) for c in exp_courses if c.get("status") == "enrolled"),
                             sum(exp_val(c) for c in exp_courses if c.get("status") == "planned"),
                             _num(req["experiential_credits"], 6))
        e_item["courses"] = [c.get("title") for c in exp_courses]
        audit.append(e_item)

        for key, flag in (("pr", "require_pr"), ("ulwr", "require_ulwr"), ("ilo8", "require_ilo8")):
            if req.get(flag):
                cs = [c for c in active if key in (c.get("tags") or [])]
                audit.append({"key": key, "label": TAGS[key], "type": "course", "status": status_of(cs),
                              "courses": [f"{c.get('title')} ({c.get('term')})" for c in cs]})

    # ---- pass/fail option budget (credits count whether you end up with a P or keep the letter grade)
    cap = _num(req.get("pf_cap"), pack["pf"]["cap"])
    pf = [c for c in courses if pf_option_used(c) and c.get("status") not in ("dropped", "waitlisted")]
    pf_prior = _num(p.get("pf_prior_credits"))      # option credits used in terms not tracked here
    pf_used = pf_prior + sum(_num(c.get("credits")) for c in pf if c.get("status") == "completed")
    pf_prog = sum(_num(c.get("credits")) for c in pf if c.get("status") == "enrolled")
    pf_plan = sum(_num(c.get("credits")) for c in pf if c.get("status") in ("planned", "considering"))
    pf_total = pf_used + pf_prog + pf_plan

    def pf_label(c):
        g = c.get("grade") if c.get("grade_status") == "actual" else None
        g = c.get("grade") if c.get("grade_status") == "actual" or c.get("status") == "completed" else None
        outcome = ("kept " + g) if g in GRADE_POINTS else ("P" if g == pf_grade else "planned")
        return {"id": c.get("id"), "title": c.get("title"), "term": c.get("term"), "credits": _num(c.get("credits")),
                "status": c.get("status"), "outcome": outcome, "inferred": not c.get("pf_elected")}
    pass_fail = {"cap": cap, "used": pf_used, "prior": pf_prior, "in_progress": pf_prog, "planned": pf_plan,
                 "remaining": cap - pf_total, "courses": [pf_label(c) for c in pf]}
    audit.append({"key": "pf", "label": f"{pack['pf']['label']} ({int(cap)} credits max)", "type": "pf",
                  "status": "missing" if pf_total > cap else "met", **pass_fail})

    min_gpa = _num(req.get("min_gpa"), 2.0)
    audit.append({"key": "min_gpa", "label": f"Minimum GPA ({min_gpa:.2f})", "type": "gpa",
                  "status": ("on_track" if gpa_actual is None and pack["id"] != "gulc"
                             else "met" if (gpa_actual or 0) >= min_gpa else "missing"), "value": gpa_actual})

    # ---- planner checks per term (credit totals + time conflicts)
    planner = {}
    for t in terms:
        tc = [c for c in courses if c.get("term") == t and c.get("status") != "dropped"]
        committed = [c for c in tc if c.get("status") in ("enrolled", "planned", "completed")]
        conflicts = []
        for i, a in enumerate(tc):
            for b in tc[i + 1:]:
                if meetings_overlap(a.get("meetings"), b.get("meetings")):
                    conflicts.append({"a": a.get("id"), "b": b.get("id"),
                                      "a_title": a.get("title"), "b_title": b.get("title"),
                                      "both_committed": a in committed and b in committed})
        planner[t] = {
            "committed_credits": sum(_num(c.get("credits")) for c in committed),
            "graded_credits": sum(_num(c.get("credits")) for c in committed if c.get("graded", True) and (c.get("grade") or "") not in NON_GPA_GRADES),
            "pf_credits": sum(_num(c.get("credits")) for c in committed if not c.get("graded", True) or (c.get("grade") or "") in NON_GPA_GRADES),
            "considering": sum(1 for c in tc if c.get("status") == "considering"),
            "conflicts": conflicts,
            "pf_elected": [c.get("title") for c in committed + [x for x in tc if x.get("status") == "considering"] if c.get("pf_elected")],
        }

    credits_after_plan = earned + transfer + in_prog + planned
    return {
        "profile": p, "grade_points": GRADE_POINTS, "terms": term_rows,
        "gpa_actual": gpa_actual, "gpa_projected": gpa_projected,
        "graded_credits_actual": cum["act_gc"], "quality_points_actual": round(cum["act_qp"], 2),
        "needed": needed, "audit": audit, "planner": planner, "pass_fail": pass_fail,
        "calendar": calendar_for(courses, pack),
        "school": schools.public(pack, p.get("major_id")), "schools": schools.school_list(),
        "credits": {"earned": earned + transfer, "in_progress": in_prog, "planned": planned,
                    "required": total_req, "remaining_after_plan": max(0.0, total_req - credits_after_plan)},
    }


def _status(cs):
    if not cs:
        return "missing"
    return "met" if all(c.get("status") == "completed" for c in cs) else "on_track"


def _ref(c):
    return f"{c.get('code') or c.get('title')} ({c.get('term')})"


def major_audit(major, active):
    """Score a major's requirement groups against the student's courses (matched by course code). A course fills at
    most one group; fixed lists go first, then level rules from the most specific to the broadest."""
    used, out = set(), []
    by_code = [c for c in active if schools.split_code(c.get("code"))]

    def take(want, alts=()):
        for w in [want, *alts]:
            for c in by_code:
                if id(c) not in used and schools.code_matches(c.get("code"), w):
                    used.add(id(c))
                    return c
        return None

    groups = list(major.get("groups") or [])
    fixed = [g for g in groups if g["rule"] in ("all", "choose", "list")]
    level = sorted((g for g in groups if g["rule"] == "level"),
                   key=lambda g: (-g.get("min", 0), g.get("max") is None, -len(g.get("exclude") or [])))
    results = {}
    for g in fixed:
        alts = g.get("alts") or {}
        if g["rule"] == "all":
            found = {w: take(w, alts.get(w, ())) for w in g["courses"]}
            have = [c for c in found.values() if c]
            todo = [w for w, c in found.items() if not c]
            st = "missing" if todo else _status(have)
            results[g["key"]] = {"status": st, "have": have, "todo": todo, "need": len(g["courses"])}
        elif g["rule"] == "choose":
            have = []
            for w in g["courses"]:
                if len(have) >= g["n"]:
                    break
                c = take(w)
                if c:
                    have.append(c)
            st = "missing" if len(have) < g["n"] else _status(have)
            results[g["key"]] = {"status": st, "have": have, "todo": [] if len(have) >= g["n"] else g["courses"], "need": g["n"]}
        else:
            have = [c for c in (take(w) for w in g["courses"]) if c]
            results[g["key"]] = {"status": "info", "have": have, "todo": [], "need": None}
    for g in level:
        have = []
        for c in by_code:
            if len(have) >= g["n"]:
                break
            subj, num, _ = schools.split_code(c.get("code"))
            if id(c) in used or subj != g["subject"] or num < g.get("min", 0) or (g.get("max") and num > g["max"]):
                continue
            if any(schools.code_matches(c.get("code"), x) for x in g.get("exclude") or []):
                continue
            used.add(id(c)); have.append(c)
        st = "missing" if len(have) < g["n"] else _status(have)
        results[g["key"]] = {"status": st, "have": have, "todo": [], "need": g["n"]}
    for g in groups:
        r = results[g["key"]]
        item = {"key": "major_" + g["key"], "label": g["label"], "type": "course", "status": r["status"],
                "courses": [_ref(c) for c in r["have"]], "group": "major"}
        if g["rule"] == "all":
            item["todo"] = r["todo"]
        elif g["rule"] in ("choose", "list"):
            item["options"] = g["courses"]
        if r["need"]:
            item["count"] = f"{len(r['have'])} of {r['need']}"
        out.append(item)
    return out, used


def osu_audit(pack, major, req, active, earned, in_prog, planned, transfer, total_req, credit_item, GP, NON_GPA):
    """Ohio State: total hours, hours at Ohio State, GE (Autumn 2022+ program), the major, GPA rules."""
    outside = {"K", "EM"}             # transfer and exam credit don't count as Ohio State enrollment

    def hrs(status, test=lambda c: True):
        return sum(_num(c.get("credits")) for c in active if c.get("status") == status and test(c))
    at_osu = lambda c: (c.get("grade") or "") not in outside
    audit = [credit_item("total", f"Total credit hours ({int(total_req)})", earned + transfer, in_prog, planned, total_req)]
    res = _num(req.get("residency_credits"), 30)
    audit.append(credit_item("residency", f"Hours through Ohio State ({int(res)})", hrs("completed", at_osu),
                             hrs("enrolled", at_osu), hrs("planned", at_osu), res))
    tagged = lambda t: [c for c in active if t in (c.get("tags") or [])]

    def course_part(tag):
        cs = tagged(tag)
        return {"label": pack["tags"][tag].split(": ", 1)[-1], "status": _status(cs), "courses": [_ref(c) for c in cs]}

    def theme_part(tag, need=4):
        cs = tagged(tag)
        cr = sum(_num(c.get("credits")) for c in cs)
        st = "missing" if cr < need else _status(cs)
        return {"label": pack["tags"][tag].split(": ", 1)[-1] + f" ({need}+ hours)", "status": st,
                "courses": [_ref(c) for c in cs], "count": f"{cr:g} of {need} hours"}

    def group(key, label, parts):
        sts = {p["status"] for p in parts}
        st = "missing" if "missing" in sts else "on_track" if "on_track" in sts else "met"
        done = sum(1 for p in parts if p["status"] == "met")
        return {"key": key, "label": label, "type": "group", "status": st, "parts": parts,
                "count": f"{done} of {len(parts)} done", "group": "ge"}

    audit.append(group("ge_bookends", "GE Bookends: Launch and Reflection seminars",
                       [course_part("ge_launch"), course_part("ge_reflect")]))
    audit.append(group("ge_foundations", "GE Foundations: one course in each of seven areas",
                       [course_part(t) for t in ("ge_redg", "ge_sbs", "ge_hcs", "ge_wil", "ge_lvpa", "ge_ns", "ge_mqr")]))
    audit.append(group("ge_themes", "GE Themes: Citizenship plus one theme you choose",
                       [theme_part("ge_cit"), theme_part("ge_theme")]))

    m_items, used = major_audit(major, active)
    extra = [c for c in active if "major" in (c.get("tags") or []) and id(c) not in used]
    if extra or not major.get("groups"):
        m_items.append({"key": "major_extra", "label": "Other major courses you marked", "type": "course",
                        "status": _status(extra) if extra else ("missing" if not major.get("groups") else "met"),
                        "courses": [_ref(c) for c in extra], "group": "major"})
    audit.extend(m_items)
    if major.get("major_gpa"):
        mc = [c for c in active if (id(c) in used or "major" in (c.get("tags") or []))
              and c.get("grade_status") == "actual" and (c.get("grade") or "") in GP]
        qp, gc = sum(GP[c["grade"]] * _num(c.get("credits")) for c in mc), sum(_num(c.get("credits")) for c in mc)
        v = r2(qp / gc) if gc else None
        audit.append({"key": "major_gpa", "label": f"Major GPA ({major['major_gpa']:.2f})", "type": "gpa",
                      "status": "met" if v is not None and v >= major["major_gpa"] else "on_track" if v is None else "missing",
                      "value": v, "note": "Courses counted toward the major here."})
    return audit


def _grade_plan(remaining, avg, pack=None):
    """Turn a required average into concrete grades: start everything at the lowest letter that
    could work, then upgrade the biggest courses first until the target is covered."""
    pack = pack or schools.GULC
    GRADE_POINTS = pack["grade_points"]
    if avg > pack["gpa_max"] or avg <= 0:
        return None
    ladder = pack["plan_ladder"]
    courses = sorted(remaining, key=lambda c: -_num(c.get("credits")))
    total_cr = sum(_num(c.get("credits")) for c in courses)
    need_qp = avg * total_cr
    base = next((g for g in ladder if GRADE_POINTS[g] <= avg and (ladder.index(g) == len(ladder) - 1 or GRADE_POINTS[ladder[ladder.index(g) + 1]] > avg)), "B")
    plan = {c.get("id") or c.get("title"): base for c in courses}
    qp = sum(GRADE_POINTS[base] * _num(c.get("credits")) for c in courses)
    up = ladder[min(ladder.index(base) + 1, len(ladder) - 1)]
    for c in courses:
        if qp >= need_qp - 1e-9:
            break
        k = c.get("id") or c.get("title")
        qp += (GRADE_POINTS[up] - GRADE_POINTS[plan[k]]) * _num(c.get("credits"))
        plan[k] = up
    if qp < need_qp - 1e-9:
        return None
    return [{"id": c.get("id"), "title": c.get("title"), "credits": _num(c.get("credits")),
             "grade": plan[c.get("id") or c.get("title")]} for c in courses]


# ---------------------------------------------------------------- storage helpers
COURSE_FIELDS = {"term", "title", "code", "crn", "credits", "grade", "grade_status", "graded", "status", "tags",
                 "experiential_credits", "priority", "professor", "meetings_text", "exam_type", "category",
                 "seats", "waitlist", "notes", "pf_eligible", "pf_elected", "pf_mandatory", "exam_date", "exam_time",
                 "location", "component"}


def clean_course(b, partial=False):
    out = {}
    for k in COURSE_FIELDS & set(b.keys()):
        v = b[k]
        if k in ("credits", "experiential_credits"):
            v = None if v in (None, "") else float(v)
            if v is not None and not 0 <= v <= 20:
                raise ValueError("credits must be between 0 and 20")
        elif k == "priority":
            v = int(v or 0)
            if not 0 <= v <= 3:
                raise ValueError("priority must be 0-3")
        elif k == "grade":
            v = (str(v).strip().upper() if v else None)
            if v and v not in ALL_GRADES:
                raise ValueError(f"unknown grade {v!r}")
        elif k == "grade_status":
            if v not in (None, "actual", "projected"):
                raise ValueError("grade_status must be actual or projected")
        elif k == "status":
            if v not in STATUSES:
                raise ValueError(f"status must be one of {STATUSES}")
        elif k == "tags":
            v = [t for t in (v or []) if t in ALL_TAGS]
        elif k in ("graded", "pf_eligible", "pf_elected", "pf_mandatory"):
            v = bool(v)
        elif k == "exam_date":
            v = (str(v).strip() or None) if v else None
            if v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                raise ValueError("exam_date must be YYYY-MM-DD")
        else:
            v = None if v is None else str(v).strip()[:500]
        out[k] = v
    if "meetings_text" in out:
        out["meetings"] = parse_meetings(out["meetings_text"])
    if not partial:
        if not out.get("title"):
            raise ValueError("course title is required")
        if not out.get("term"):
            raise ValueError("term is required")
        out.setdefault("status", "considering")
        out.setdefault("tags", [])
        out.setdefault("graded", True)
    return out


REQ_KEYS = set(DEFAULT_PROFILE["requirements"]) | set(schools.OSU["requirements"])


def _profile_ref():
    return user_ref().collection("education").document("profile")


def load_all():
    snap = _profile_ref().get()
    profile = snap.to_dict() if snap.exists else {}
    courses = [d.to_dict() | {"id": d.id} for d in user_ref().collection("edu_courses").stream()]
    return profile, courses


def education_state():
    profile, courses = load_all()
    order = {t: i for i, t in enumerate((profile or {}).get("terms") or schools.get((profile or {}).get("school_id"))["terms"])}
    courses.sort(key=lambda c: (order.get(c.get("term"), 99), STATUSES.index(c.get("status", "considering")),
                                -int(c.get("priority") or 0), c.get("title", "")))
    computed = compute(profile, courses)
    from syllabus import calendar_events, load_syllabi
    syllabi = load_syllabi(courses)
    for term, evs in calendar_events(syllabi, courses).items():
        cal = computed["calendar"].setdefault(term, [])
        finals = {(e.get("course_id"), e["date"]) for e in cal if e["kind"] == "final"}
        cal.extend(e for e in evs if (e.get("course_id"), e["date"]) not in finals)   # don't repeat a final already shown
        cal.sort(key=lambda x: (x["date"], x["kind"] != "final", _time_key(x.get("time"))))
    return {"courses": courses, "computed": computed, "syllabi": syllabi, "has_data": bool(courses or profile),
            "needs_school": not (profile or {}).get("school_id") and not courses}


# ---------------------------------------------------------------- routes
def _register_routes():
    @bp.get("/api/edu")
    @require_owner
    def get_state():
        return jsonify(education_state())

    @bp.put("/api/edu/profile")
    @require_owner
    def put_profile():
        b = request.get_json(force=True) or {}
        upd = {}
        cur = (_profile_ref().get().to_dict() or {})
        pack = schools.get(b.get("school_id") or cur.get("school_id"))
        if "school_id" in b:
            if b["school_id"] not in schools.SCHOOLS:
                return jsonify(error="unknown school"), 400
            upd["school_id"] = b["school_id"]
            if b["school_id"] != (cur.get("school_id") or "gulc"):     # switching: drop the old school's defaults
                d = school_defaults(pack)
                upd.update(school=d["school"], program=d["program"], terms=d["terms"], requirements={})
        if "major_id" in b:
            if not pack.get("majors") or b["major_id"] not in pack["majors"]:
                return jsonify(error="unknown major"), 400
            upd["major_id"] = b["major_id"]
            upd["program"] = pack["majors"][b["major_id"]]["program"]
        if "target_gpa" in b:
            if b["target_gpa"] in (None, ""):
                upd["target_gpa"] = None
            else:
                t = float(b["target_gpa"])
                if not 0 < t <= pack["gpa_max"]:
                    return jsonify(error=f"target GPA must be between 0 and {pack['gpa_max']:.2f}"), 400
                upd["target_gpa"] = t
        if "transfer_credits" in b:
            upd["transfer_credits"] = float(b["transfer_credits"] or 0)
        if "pf_prior_credits" in b:
            v = float(b["pf_prior_credits"] or 0)
            if not 0 <= v <= 20:
                return jsonify(error="P/F credits used must be between 0 and 20"), 400
            upd["pf_prior_credits"] = v
        if "terms" in b and isinstance(b["terms"], list):
            upd["terms"] = [str(t)[:40] for t in b["terms"]][:16]
        for k in ("school", "program", "grad_term"):
            if k in b:
                upd[k] = str(b[k])[:120]
        if "requirements" in b and isinstance(b["requirements"], dict):
            upd["requirements"] = {k: b["requirements"][k] for k in REQ_KEYS if k in b["requirements"]}
        _profile_ref().set(upd, merge=True)
        return jsonify(education_state())

    @bp.get("/api/edu/catalog/search")
    @require_owner
    def catalog_search():
        """Live class search for schools that publish one (Ohio State). Public catalog data, nothing stored."""
        import osu_classes
        profile, _ = load_all()
        if schools.get(profile.get("school_id")).get("catalog") != "osu":
            return jsonify(error="class search isn't available for your school"), 400
        try:
            return jsonify(courses=osu_classes.search(request.args.get("q"), request.args.get("term")))
        except Exception as e:
            return jsonify(error=f"Ohio State's class search didn't answer ({type(e).__name__}). Try again in a minute."), 502

    @bp.post("/api/edu/courses")
    @require_owner
    def add_course():
        try:
            data = clean_course(request.get_json(force=True) or {})
        except ValueError as e:
            return jsonify(error=str(e)), 400
        ref = user_ref().collection("edu_courses").document()
        ref.set(data)
        return jsonify(education_state() | {"id": ref.id})

    @bp.patch("/api/edu/courses/<cid>")
    @require_owner
    def patch_course(cid):
        try:
            data = clean_course(request.get_json(force=True) or {}, partial=True)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        ref = user_ref().collection("edu_courses").document(cid)
        if not ref.get().exists:
            return jsonify(error="course not found"), 404
        ref.set(data, merge=True)
        return jsonify(education_state())

    @bp.delete("/api/edu/courses/<cid>")
    @require_owner
    def delete_course(cid):
        user_ref().collection("edu_courses").document(cid).delete()
        return jsonify(education_state())

    @bp.post("/api/edu/import")
    @require_owner
    def import_education():
        """Bulk load {profile, courses}. replace=true clears existing courses first."""
        b = request.get_json(force=True) or {}
        if b.get("merge"):
            return _merge_import(b)
        try:
            courses = [clean_course(c) for c in (b.get("courses") or [])][:300]
        except ValueError as e:
            return jsonify(error=f"Import failed: {e}"), 400
        col = user_ref().collection("edu_courses")
        if b.get("replace"):
            for d in col.stream():
                col.document(d.id).delete()
        batch = db.batch()
        for c in courses:
            batch.set(col.document(), c)
        batch.commit()
        if isinstance(b.get("profile"), dict):
            prof = {k: v for k, v in b["profile"].items() if k in DEFAULT_PROFILE or k in ("grad_term", "pf_prior_credits", "school_id", "major_id")}
            _profile_ref().set(prof, merge=True)
        return jsonify(education_state() | {"imported": len(courses)})


def _merge_import(b):
    """Update courses in place, matched by term + title (case-insensitive); add any that don't exist.
    Only the fields present in the file change, so edits made in the app are kept."""
    col = user_ref().collection("edu_courses")
    existing = {((d.to_dict().get("term") or "").lower(), (d.to_dict().get("title") or "").strip().lower()): d.id
                for d in col.stream()}
    updated = added = 0
    try:
        for c in (b.get("courses") or [])[:300]:
            key = ((c.get("term") or "").lower(), (c.get("title") or "").strip().lower())
            if key in existing:
                col.document(existing[key]).set(clean_course(c, partial=True), merge=True); updated += 1
            else:
                col.document().set(clean_course(c)); added += 1
    except ValueError as e:
        return jsonify(error=f"Import failed: {e}"), 400
    if isinstance(b.get("profile"), dict):
        prof = {k: v for k, v in b["profile"].items() if k in DEFAULT_PROFILE or k in ("grad_term", "pf_prior_credits", "school_id", "major_id")}
        if isinstance(prof.get("requirements"), dict):
            prof["requirements"] = {k: v for k, v in prof["requirements"].items() if k in REQ_KEYS}
        _profile_ref().set(prof, merge=True)
    return jsonify(education_state() | {"imported": updated + added, "updated": updated, "added": added})

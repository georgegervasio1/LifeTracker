"""Education: courses, grades & GPA, degree audit, and course planning.

Data (per user, under users/{uid}/):
  education/profile       -> school, program, terms, target GPA, transfer credits, requirement config
  edu_courses/{id}        -> one doc per course in any term (taken, current, planned or being considered)

All GPA math lives in compute() so the API, the UI and (later) the AI assistant agree.
"""
import math
import re
from flask import Blueprint, jsonify, request

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


def compute(profile, courses):
    p = {**DEFAULT_PROFILE, **(profile or {})}
    req = {**DEFAULT_PROFILE["requirements"], **(p.get("requirements") or {})}
    terms = list(p["terms"])
    for c in courses:
        if c.get("term") and c["term"] not in terms:
            terms.append(c["term"])

    def graded(c):
        return (c.get("grade") or "") in GRADE_POINTS

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
    target = _num(p.get("target_gpa"), 3.7)
    remaining = [c for c in courses if c.get("status") in ("enrolled", "planned")
                 and c.get("grade_status") != "actual" and (c.get("graded", True) and (c.get("grade") or "") not in NON_GPA_GRADES)]
    rem_gc = sum(_num(c.get("credits")) for c in remaining)
    needed = None
    if rem_gc:
        avg = (target * (cum["act_gc"] + rem_gc) - cum["act_qp"]) / rem_gc
        needed = {"average": round(avg, 3), "graded_credits": rem_gc, "achievable": avg <= 4.33,
                  "already_met": avg <= 0, "plan": _grade_plan(remaining, avg)}

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
    lc_req = _num(req["law_center_credits"], 54)

    def credit_item(key, label, have_done, have_prog, have_plan, need):
        total = have_done + have_prog + have_plan
        return {"key": key, "label": label, "type": "credits", "need": need, "done": have_done,
                "in_progress": have_prog, "planned": have_plan,
                "status": "met" if have_done >= need else "on_track" if total >= need else "missing",
                "short_by": max(0.0, need - total)}

    audit = [credit_item("total", f"Total credits ({int(total_req)})", earned + transfer, in_prog, planned, total_req),
             credit_item("law_center", f"Law Center credits ({int(lc_req)})", earned, in_prog, planned, lc_req)]

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

    min_gpa = _num(req["min_gpa"], 2.0)
    audit.append({"key": "min_gpa", "label": f"Minimum GPA ({min_gpa:.2f})", "type": "gpa",
                  "status": "met" if (gpa_actual or 0) >= min_gpa else "missing", "value": gpa_actual})

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
        }

    credits_after_plan = earned + transfer + in_prog + planned
    return {
        "profile": p, "grade_points": GRADE_POINTS, "terms": term_rows,
        "gpa_actual": gpa_actual, "gpa_projected": gpa_projected,
        "graded_credits_actual": cum["act_gc"], "quality_points_actual": round(cum["act_qp"], 2),
        "needed": needed, "audit": audit, "planner": planner,
        "credits": {"earned": earned + transfer, "in_progress": in_prog, "planned": planned,
                    "required": total_req, "remaining_after_plan": max(0.0, total_req - credits_after_plan)},
    }


def _grade_plan(remaining, avg):
    """Turn a required average into concrete grades: start everything at the lowest letter that
    could work, then upgrade the biggest courses first until the target is covered."""
    if avg > 4.33 or avg <= 0:
        return None
    ladder = ["B", "B+", "A-", "A", "A+"]
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
                 "seats", "waitlist", "notes", "pf_eligible"}


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
            if v and v not in GRADE_POINTS and v not in NON_GPA_GRADES:
                raise ValueError(f"unknown grade {v!r}")
        elif k == "grade_status":
            if v not in (None, "actual", "projected"):
                raise ValueError("grade_status must be actual or projected")
        elif k == "status":
            if v not in STATUSES:
                raise ValueError(f"status must be one of {STATUSES}")
        elif k == "tags":
            v = [t for t in (v or []) if t in TAGS]
        elif k in ("graded", "pf_eligible"):
            v = bool(v)
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


def _profile_ref():
    return user_ref().collection("education").document("profile")


def load_all():
    snap = _profile_ref().get()
    profile = snap.to_dict() if snap.exists else {}
    courses = [d.to_dict() | {"id": d.id} for d in user_ref().collection("edu_courses").stream()]
    return profile, courses


def education_state():
    profile, courses = load_all()
    order = {t: i for i, t in enumerate((profile or {}).get("terms") or DEFAULT_PROFILE["terms"])}
    courses.sort(key=lambda c: (order.get(c.get("term"), 99), STATUSES.index(c.get("status", "considering")),
                                -int(c.get("priority") or 0), c.get("title", "")))
    return {"courses": courses, "computed": compute(profile, courses), "has_data": bool(courses or profile)}


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
        if "target_gpa" in b:
            t = float(b["target_gpa"])
            if not 0 < t <= 4.33:
                return jsonify(error="target GPA must be between 0 and 4.33"), 400
            upd["target_gpa"] = t
        if "transfer_credits" in b:
            upd["transfer_credits"] = float(b["transfer_credits"] or 0)
        if "terms" in b and isinstance(b["terms"], list):
            upd["terms"] = [str(t)[:40] for t in b["terms"]][:16]
        for k in ("school", "program", "grad_term"):
            if k in b:
                upd[k] = str(b[k])[:120]
        if "requirements" in b and isinstance(b["requirements"], dict):
            upd["requirements"] = {k: b["requirements"][k] for k in DEFAULT_PROFILE["requirements"] if k in b["requirements"]}
        _profile_ref().set(upd, merge=True)
        return jsonify(education_state())

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
            prof = {k: v for k, v in b["profile"].items() if k in DEFAULT_PROFILE or k == "grad_term"}
            _profile_ref().set(prof, merge=True)
        return jsonify(education_state() | {"imported": len(courses)})

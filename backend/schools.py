"""School packs: everything in Education that depends on the school (grading scale, requirements, academic calendar,
finals, pass/fail rules, majors). A user's education profile picks one with `school_id`; profiles without one are the
original Georgetown Law setup ("gulc"). Only public, published rules live here, never a student's own data.

Each pack is a dict; `get(school_id)` returns it. Requirement groups for a major (OSU) use the shapes understood by
education.major_audit():
  {"key", "label", "rule": "all", "courses": [...]}                every listed course
  {"key", "label", "rule": "choose", "n": 1, "courses": [...]}     n of the listed courses
  {"key", "label", "rule": "level", "n": 3, "subject": "ECON", "min": 3000, "max": None, "exclude": [...]}
                                                                    n courses in a subject at or above a level
  {"key", "label", "rule": "list", "courses": [...]}               choices with no published count (shown, not scored)
A course code like "ECON 2001" matches any section suffix ("ECON 2001.01", "ECON 2001.03H").
"""
import re

# ---------------------------------------------------------------- Georgetown Law (the original setup)
GULC = {
    "id": "gulc",
    "name": "Georgetown University Law Center",
    "short": "Georgetown Law",
    "program": "J.D.",
    "terms": ["Fall 2025", "Spring 2026", "Fall 2026", "Spring 2027"],
    # Grade points for students matriculating Fall 1998+; A+ = 4.33 since Summer 2020.
    "grade_points": {"A+": 4.33, "A": 4.00, "A-": 3.67, "B+": 3.33, "B": 3.00, "B-": 2.67,
                     "C+": 2.33, "C": 2.00, "C-": 1.67, "D": 1.00, "F": 0.00},
    "non_gpa_grades": ["P", "H", "CR", "AP", "NG", "AU", "W", "IP"],
    "no_credit_grades": ["W", "AU", "F"],
    "pass_grades": ["P", "H", "CR"],          # offered in the grade pickers next to the letters
    "pf_grade": "P",
    "gpa_max": 4.33,
    "plan_ladder": ["B", "B+", "A-", "A", "A+"],
    "top_grades": ["A", "A+"],
    "tags": {"pr": "Professional Responsibility", "ulwr": "Upperclass Legal Writing",
             "experiential": "Experiential", "ilo8": "Critical perspectives (ILO #8)"},
    "tag_short": {"pr": "PR", "ulwr": "Writing", "experiential": "Experiential", "ilo8": "ILO #8"},
    "pf": {"cap": 7, "one_per_term": True, "ineligible_tags": ["pr", "ulwr"], "label": "Pass/fail option",
           "note": "Credits count against the limit whether you get a Pass or keep the letter grade. One P/F course per "
                   "semester; not for required courses, writing seminars, clinics or supervised research."},
    "audit_intro": "Georgetown Law J.D. requirements, checked against completed, current and planned courses. Tag courses "
                   "in the Planner to count them toward a requirement. Confirm with the Registrar's degree audit before relying on it.",
    "honors_note": "Georgetown Law awards cum laude to the top third and magna cum laude to the top 10% of the class, so "
                   "cutoffs move each year. Published examples:",
    "finals_note": "Exam dates come from the Registrar's Fall 2026 schedule. Edit a course to change one.",
    "catalog": None,
    "majors": None,
}

# ---------------------------------------------------------------- The Ohio State University (Columbus)
# Sources (read 2026-11-10):
#   Grades: registrar.osu.edu Transcript Key + Buckeye's Guide to Academic Policies (Sept 2026). No A+, D- or F; E = 0.0.
#   Degree: Buckeye's Guide: 120 semester hours minimum, 30 hours through Ohio State enrollment, 2.0 cumulative to graduate.
#   GE (students entering Autumn 2022+): advising.osu.edu/general-education-requirements and ugeducation.osu.edu GE structure.
#   PA/NP: ugeducation.osu.edu grades page, "up to 20 credit hours of electives" for eligible students.
#   Calendar: registrar.osu.edu 5-year academic calendar (updated 2026-03-03).
#   Finals: registrar.osu.edu Autumn 2026 finals schedule (Columbus, Dec 11-17, 2026).
OSU_GE_TAGS = {
    "ge_launch": "GE Launch Seminar",
    "ge_redg": "GE Foundation: Race, Ethnicity and Gender Diversity",
    "ge_sbs": "GE Foundation: Social and Behavioral Sciences",
    "ge_hcs": "GE Foundation: Historical and Cultural Studies",
    "ge_wil": "GE Foundation: Writing and Information Literacy",
    "ge_lvpa": "GE Foundation: Literary, Visual and Performing Arts",
    "ge_ns": "GE Foundation: Natural Sciences",
    "ge_mqr": "GE Foundation: Mathematical and Quantitative Reasoning or Data Analysis",
    "ge_cit": "GE Theme: Citizenship for a Diverse and Just World",
    "ge_theme": "GE Theme: A second theme of your choice",
    "ge_reflect": "GE Reflection Seminar",
}
OSU_GE_SHORT = {"ge_launch": "GE Launch", "ge_redg": "GE REGD", "ge_sbs": "GE SBS", "ge_hcs": "GE HCS", "ge_wil": "GE Writing",
                "ge_lvpa": "GE Arts", "ge_ns": "GE Science", "ge_mqr": "GE Math", "ge_cit": "GE Citizenship",
                "ge_theme": "GE Theme", "ge_reflect": "GE Reflection"}
# Words in a class-search attribute description that identify a GE category (auto-tagging; the student can change it).
OSU_GE_MATCH = [("ge_launch", r"launch"), ("ge_reflect", r"reflection"),
                ("ge_redg", r"race.*ethnicity.*gender"), ("ge_sbs", r"social.*behavioral"),
                ("ge_hcs", r"historical.*cultural|historical or cultural"), ("ge_wil", r"writing.*information"),
                ("ge_lvpa", r"literary.*visual|visual.*performing"), ("ge_ns", r"natural science"),
                ("ge_mqr", r"mathematical|quantitative reasoning|data analysis"), ("ge_cit", r"citizenship"),
                ("ge_theme", r"theme")]

FISHER_CORE = ["ENGLISH 1110", "MATH 1131", "STAT 1430", "ECON 2001", "ECON 2002", "CSE 2111",
               "ACCTMIS 2200", "ACCTMIS 2300", "BUSOBA 2320", "BUSOBA 2321"]
FISHER_ADV_WRITING = ["ECON 2367.01", "ECON 2367.02", "ENGLISH 2367.01", "ENGLISH 2367.01H", "ENGLISH 2367.02",
                      "ENGLISH 2367.04", "ENGLISH 3304", "ENGLISH 3305", "POLITSC 2367"]
FISHER_NOTE = ("Fisher BSBA rules for students who start the program Spring 2027 or earlier (fisher.osu.edu curriculum "
               "page, Oct 2026). Fisher is publishing new rules for Autumn 2027. Calculus can be MATH 1131 or 1151; "
               "a C or better is required.")

OSU_MAJORS = {
    "fin": {
        "label": "Finance (BSBA, Fisher College of Business)",
        "program": "B.S.B.A., Finance",
        "total_credits": 120,
        "note": FISHER_NOTE + " Fisher's page lists the advanced finance courses without saying how many you need; "
                "check your advising report for that count.",
        "groups": [
            {"key": "fisher_core", "label": "Business foundation and core", "rule": "all", "courses": FISHER_CORE,
             "alts": {"MATH 1131": ["MATH 1151"]}},
            {"key": "fisher_writing", "label": "Advanced writing (one course)", "rule": "choose", "n": 1, "courses": FISHER_ADV_WRITING},
            {"key": "fin_prereq", "label": "Finance prerequisites", "rule": "all", "courses": ["BUSMHR 2292", "BUSFIN 3220"]},
            {"key": "fin_core", "label": "Finance required courses", "rule": "all",
             "courses": ["BUSFIN 4211", "BUSFIN 4221", "BUSFIN 4201", "BUSFIN 4250"]},
            {"key": "fin_adv", "label": "Advanced finance courses", "rule": "list",
             "courses": ["BUSFIN 4219", "BUSFIN 4229", "BUSFIN 4239", "BUSFIN 4255"]},
        ],
    },
    "econ_ba": {
        "label": "Economics (BA, Arts and Sciences)",
        "program": "B.A., Economics",
        "total_credits": 121,
        "major_gpa": 2.0,
        "note": "Economics BA as approved for Autumn 2026 (OAA informational item, Feb 2026). Arts and Sciences requires a "
                "2.0 GPA in the major as well as overall.",
        "groups": [
            {"key": "econ_pre", "label": "Prerequisites: College Algebra, statistics, Principles of Micro", "rule": "all",
             "courses": ["MATH 1130", "STAT 1450", "ECON 2001"],
             "alts": {"MATH 1130": ["MATH 1148", "MATH 1131", "MATH 1151"], "STAT 1450": ["ECON 3400"]}},
            {"key": "econ_core", "label": "Major core", "rule": "all",
             "courses": ["ECON 2002", "ECON 4001", "ECON 4002", "ECON 4400"]},
            {"key": "econ_writing", "label": "Major writing (one course)", "rule": "choose", "n": 1,
             "courses": ["ECON 2367", "ECON 5130", "ECON 5140"]},
            {"key": "econ_el3", "label": "Three ECON electives at 3000 level or higher (not 3400)", "rule": "level", "n": 3,
             "subject": "ECON", "min": 3000, "exclude": ["ECON 3400", "ECON 4001", "ECON 4002", "ECON 4400"]},
            {"key": "econ_el5", "label": "One 5000-level ECON course (not 5130, 5140, 5193, 5194, 5410, 5420)", "rule": "level",
             "n": 1, "subject": "ECON", "min": 5000, "max": 5999,
             "exclude": ["ECON 5130", "ECON 5140", "ECON 5193", "ECON 5194", "ECON 5410", "ECON 5420"]},
            {"key": "econ_adv", "label": "One advanced ECON elective (5000 level or higher)", "rule": "level", "n": 1,
             "subject": "ECON", "min": 5000},
        ],
    },
    "econ_bs": {
        "label": "Economics (BS, Arts and Sciences)",
        "program": "B.S., Economics",
        "total_credits": 121,
        "major_gpa": 2.0,
        "note": "Economics BS as approved for Autumn 2026 (OAA informational item, Feb 2026). Arts and Sciences requires a "
                "2.0 GPA in the major as well as overall.",
        "groups": [
            {"key": "econ_pre", "label": "Prerequisites: Calculus I, STAT 2450, Principles of Micro", "rule": "all",
             "courses": ["MATH 1151", "STAT 2450", "ECON 2001"],
             "alts": {"MATH 1151": ["MATH 1141", "MATH 1181H", "MATH 4181H"]}},
            {"key": "econ_core", "label": "Major core", "rule": "all",
             "courses": ["ECON 2002", "ECON 4001", "ECON 4002", "ECON 5410", "ECON 5420"]},
            {"key": "econ_writing", "label": "Major writing (one course)", "rule": "choose", "n": 1,
             "courses": ["ECON 2367", "ECON 5130", "ECON 5140"]},
            {"key": "econ_el3", "label": "Three ECON electives at 3000 level or higher (not 3400 or 4400)", "rule": "level", "n": 3,
             "subject": "ECON", "min": 3000, "exclude": ["ECON 3400", "ECON 4400", "ECON 4001", "ECON 4002"]},
            {"key": "econ_el5", "label": "One 5000-level ECON course (not 5130, 5140, 5193, 5194, 5410, 5420)", "rule": "level",
             "n": 1, "subject": "ECON", "min": 5000, "max": 5999,
             "exclude": ["ECON 5130", "ECON 5140", "ECON 5193", "ECON 5194", "ECON 5410", "ECON 5420"]},
            {"key": "econ_adv", "label": "One advanced ECON elective (5000 level or higher)", "rule": "level", "n": 1,
             "subject": "ECON", "min": 5000},
        ],
    },
    "other": {
        "label": "Another major (add its required courses yourself)",
        "program": "Bachelor's degree",
        "total_credits": 120,
        "note": "Add the courses your major sheet requires under Major courses, and they're tracked like the others.",
        "groups": [],
    },
}

OSU = {
    "id": "osu",
    "name": "The Ohio State University",
    "short": "Ohio State",
    "program": "Bachelor's degree",
    "terms": ["Autumn 2026", "Spring 2027", "Autumn 2027", "Spring 2028",
              "Autumn 2028", "Spring 2029", "Autumn 2029", "Spring 2030"],
    "grade_points": {"A": 4.0, "A-": 3.7, "B+": 3.3, "B": 3.0, "B-": 2.7, "C+": 2.3, "C": 2.0, "C-": 1.7,
                     "D+": 1.3, "D": 1.0, "E": 0.0},
    # Not in the point-hour ratio. PA/S/EM/K/P/PE earn hours; NP, U, W, R (audit), I and NG don't (yet).
    "non_gpa_grades": ["PA", "NP", "S", "U", "W", "EM", "K", "R", "I", "P", "PE", "NG"],
    "no_credit_grades": ["E", "NP", "U", "W", "R", "I", "NG"],
    "pass_grades": ["PA", "NP", "S", "U", "EM", "K", "W"],
    "pf_grade": "PA",
    "gpa_max": 4.0,
    "plan_ladder": ["B", "B+", "A-", "A"],
    "top_grades": ["A"],
    "tags": OSU_GE_TAGS,
    "tag_short": OSU_GE_SHORT,
    "pf": {"cap": 20, "one_per_term": False, "ineligible_tags": [], "label": "Pass/non-pass (PA/NP) electives",
           "note": "Eligible students can take up to 20 credit hours of electives PA/NP. Decide by the deadline to withdraw "
                   "without a W. PA/NP courses can't count for most major and GE requirements; check with your advisor."},
    "audit_intro": "Ohio State degree requirements: university rules, the General Education (GE) program for students "
                   "entering Autumn 2022 or later, and your major. Courses count toward the major by course number; tag GE "
                   "courses in the Planner (courses added from class search are tagged for you when Ohio State lists a GE "
                   "category). Confirm with your Degree Audit in Buckeye Link before relying on it.",
    "honors_note": "",
    "finals_note": "Exam times come from the Registrar's Autumn 2026 finals schedule, matched to each class's first meeting "
                   "day and start time. Thursday or Friday-first classes and classes starting before 8:00 am are set by the "
                   "department; check your syllabus.",
    "catalog": "osu",           # live class search through content.osu.edu (see osu_classes.py)
    "majors": OSU_MAJORS,
    "default_major": "fin",
    "requirements": {"total_credits": 120, "residency_credits": 30, "min_gpa": 2.0, "pf_cap": 20},
}

OSU_CALENDAR = {
    "Autumn 2026": [
        ("2026-08-18", None, "Initial fee due", "deadline"),
        ("2026-08-25", None, "Classes begin", "classes"),
        ("2026-09-07", None, "Labor Day: no classes", "holiday"),
        ("2026-09-08", None, "Enrollment census date", "deadline"),
        ("2026-10-12", None, "Last day of first-session classes", "classes"),
        ("2026-10-13", "2026-10-14", "First-session finals", "exams"),
        ("2026-10-15", "2026-10-16", "Autumn break: no classes", "break"),
        ("2026-10-19", None, "Second-session classes begin", "classes"),
        ("2026-11-11", None, "Veterans Day: no classes", "holiday"),
        ("2026-11-25", "2026-11-27", "Thanksgiving break", "break"),
        ("2026-12-09", None, "Last day of classes", "classes"),
        ("2026-12-11", "2026-12-17", "Final exams", "exams"),
        ("2026-12-20", None, "Autumn commencement", "other"),
        ("2026-12-28", "2026-12-31", "Winter recess", "break"),
    ],
    "Spring 2027": [
        ("2027-01-04", None, "Initial fee due", "deadline"),
        ("2027-01-11", None, "Classes begin", "classes"),
        ("2027-01-18", None, "Martin Luther King Jr. Day: no classes", "holiday"),
        ("2027-01-25", None, "Enrollment census date", "deadline"),
        ("2027-02-26", None, "Last day of first-session classes", "classes"),
        ("2027-03-01", "2027-03-02", "First-session finals", "exams"),
        ("2027-03-03", None, "Second-session classes begin", "classes"),
        ("2027-03-15", "2027-03-19", "Spring break", "break"),
        ("2027-04-26", None, "Last day of classes", "classes"),
        ("2027-04-28", "2027-05-04", "Final exams", "exams"),
        ("2027-05-09", None, "Spring commencement", "other"),
    ],
    "Summer 2027": [
        ("2027-05-03", None, "Initial fee due", "deadline"),
        ("2027-05-10", None, "Summer classes begin", "classes"),
        ("2027-05-31", None, "Memorial Day: no classes", "holiday"),
        ("2027-06-18", None, "Juneteenth: no classes", "holiday"),
        ("2027-07-05", None, "Independence Day observed: no classes", "holiday"),
        ("2027-07-29", None, "Last day of summer classes", "classes"),
        ("2027-08-02", "2027-08-04", "Summer finals", "exams"),
        ("2027-08-08", None, "Summer commencement", "other"),
    ],
}


def _m(t):
    """'8:50am' / '12:10pm' -> minutes after midnight."""
    m = re.match(r"(\d{1,2}):(\d{2})\s*(am|pm)", t.strip().lower())
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    h = h % 12 + (12 if ap == "pm" else 0)
    return h * 60 + mi


def _rows(day, rows):
    out = []
    for rng, date, time in rows:
        lo, _, hi = rng.partition("-")
        out.append((day, _m(lo), _m(hi) if hi else 24 * 60, date, time))
    return out


# Autumn 2026, Columbus: (first meeting day, start-time range) -> exam date and time.
OSU_FINALS = {"Autumn 2026": (
    _rows("M", [("8:00am-8:49am", "2026-12-14", "8:00 AM"), ("8:50am-9:39am", "2026-12-14", "10:00 AM"),
                ("9:40am-10:29am", "2026-12-15", "10:00 AM"), ("10:30am-11:19am", "2026-12-16", "10:00 AM"),
                ("11:20am-12:09pm", "2026-12-17", "10:00 AM"), ("12:10pm-12:59pm", "2026-12-17", "2:00 PM"),
                ("1:00pm-1:49pm", "2026-12-15", "12:00 PM"), ("1:50pm-2:39pm", "2026-12-16", "2:00 PM"),
                ("2:40pm-3:29pm", "2026-12-11", "12:00 PM"), ("3:30pm-4:19pm", "2026-12-17", "4:00 PM"),
                ("4:20pm-5:09pm", "2026-12-14", "6:00 PM"), ("5:10pm-5:59pm", "2026-12-11", "6:00 PM"),
                ("6:00pm", "2026-12-14", "8:00 PM")])
    + _rows("T", [("8:00am-8:49am", "2026-12-15", "8:00 AM"), ("8:50am-9:39am", "2026-12-11", "8:00 AM"),
                  ("9:40am-10:29am", "2026-12-11", "10:00 AM"), ("10:30am-11:19am", "2026-12-14", "12:00 PM"),
                  ("11:20am-12:09pm", "2026-12-14", "2:00 PM"), ("12:10pm-12:59pm", "2026-12-15", "2:00 PM"),
                  ("1:00pm-1:49pm", "2026-12-11", "2:00 PM"), ("1:50pm-2:39pm", "2026-12-11", "4:00 PM"),
                  ("2:40pm-3:29pm", "2026-12-15", "4:00 PM"), ("3:30pm-4:19pm", "2026-12-16", "4:00 PM"),
                  ("4:20pm-5:09pm", "2026-12-15", "6:00 PM"), ("5:10pm-5:59pm", "2026-12-16", "8:00 PM"),
                  ("6:00pm", "2026-12-11", "8:00 PM")])
    + _rows("W", [("8:00am-9:19am", "2026-12-16", "8:00 AM"), ("9:20am-10:39am", "2026-12-17", "8:00 AM"),
                  ("10:40am-11:59am", "2026-12-17", "12:00 PM"), ("12:00pm-1:19pm", "2026-12-14", "4:00 PM"),
                  ("1:20pm-2:39pm", "2026-12-16", "12:00 PM"), ("2:40pm-3:59pm", "2026-12-16", "6:00 PM"),
                  ("4:00pm-5:19pm", "2026-12-17", "6:00 PM"), ("5:20pm-6:39pm", "2026-12-17", "8:00 PM"),
                  ("6:40pm", "2026-12-15", "8:00 PM")])
)}
OSU_EXAM_LENGTH = "1 h 45 min"


def osu_final_for(term, meetings):
    """The registrar's exam slot for a class, from its first meeting of the week. None when the schedule doesn't cover
    it (no schedule for the term, Thursday/Friday-first, before 8:00 am, no meeting times)."""
    rows = OSU_FINALS.get(term)
    if not rows or not meetings:
        return None
    order = "MTWRFSU"
    first = min(meetings, key=lambda m: (min(order.index(d) for d in m["days"]), m["start"]))
    day = min(first["days"], key=order.index)
    start = int(first["start"][:2]) * 60 + int(first["start"][3:])
    for d, lo, hi, date, time in rows:
        if d == day and lo <= start <= hi:
            return {"date": date, "time": time}
    return None


SCHOOLS = {"gulc": GULC, "osu": OSU}
GULC["calendar"] = None          # filled in by education.py (kept there with the finals list)
OSU["calendar"] = OSU_CALENDAR
ALL_TAGS = {**GULC["tags"], **OSU["tags"]}


def get(school_id):
    return SCHOOLS.get(school_id or "gulc", GULC)


def public(pack, major_id=None):
    """What the browser needs to draw a school-aware Education section."""
    out = {k: pack[k] for k in ("id", "name", "short", "program", "gpa_max", "tags", "tag_short", "pass_grades", "pf_grade",
                               "audit_intro", "honors_note", "finals_note", "top_grades")}
    out["pf"] = pack["pf"]
    out["catalog"] = bool(pack.get("catalog"))
    if pack.get("majors"):
        out["majors"] = [{"id": k, "label": v["label"]} for k, v in pack["majors"].items()]
        m = pack["majors"].get(major_id or pack.get("default_major"))
        out["major"] = {"id": major_id or pack.get("default_major"), "label": m["label"], "note": m.get("note", "")} if m else None
    return out


def school_list():
    return [{"id": s["id"], "name": s["name"], "program": s["program"]} for s in SCHOOLS.values()]


_CODE = re.compile(r"^\s*([A-Za-z]+)\s*(\d{4})(?:\.(\w+))?\s*$")


def split_code(code):
    """'econ 2001.01' -> ('ECON', 2001, '01'); None for anything else."""
    m = _CODE.match((code or "").replace("  ", " "))
    return (m.group(1).upper(), int(m.group(2)), (m.group(3) or "").upper()) if m else None


def code_matches(have, want):
    """A course code satisfies a listed one: same subject and number; a listed suffix (.01, H) must match too."""
    a, b = split_code(have), split_code(want)
    if not a or not b:
        return False
    if a[:2] != b[:2]:
        return False
    return not b[2] or a[2] == b[2]


def ge_tags_from(descriptions):
    """Class-search attribute descriptions -> GE tags (best effort; the student can edit)."""
    tags = []
    for d in descriptions or []:
        low = (d or "").lower()
        if "ge" not in low and "gen" not in low:
            continue
        for tag, pat in OSU_GE_MATCH:
            if re.search(pat, low) and tag not in tags:
                if tag == "ge_theme" and "citizenship" in low:
                    continue
                tags.append(tag)
                break
    return tags

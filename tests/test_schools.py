"""School packs: Ohio State grading, GE + major audit, finals by meeting time, class-search parsing. Made-up courses
only; no network."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import education, schools, osu_classes

E = education


def course(code, term="Autumn 2026", status="completed", grade=None, cr=3, tags=(), meet=None, **k):
    c = {"id": code, "title": code, "code": code, "term": term, "status": status, "credits": cr, "tags": list(tags),
         "grade": grade, "grade_status": "actual" if grade and status == "completed" else "projected", "graded": True, **k}
    if meet:
        c["meetings_text"] = meet
        c["meetings"] = E.parse_meetings(meet)
    return c


# 1. Georgetown stays the default for an existing profile with no school_id.
g = E.compute({}, [course("LAW 001", term="Fall 2025", grade="A+")])
assert g["school"]["id"] == "gulc" and g["gpa_actual"] == 4.33 and g["profile"]["target_gpa"] == 3.70
assert any(a["key"] == "law_center" for a in g["audit"])

# 2. Ohio State: 4.0 scale, E counts as 0 with no credit, PA earns hours outside the GPA.
osu = {"school_id": "osu", "major_id": "econ_ba"}
cs = [course("ECON 2001.01", grade="A", tags=["ge_sbs"]), course("MATH 1130", grade="B+", cr=4, tags=["ge_mqr"]),
      course("STAT 1450", grade="E"), course("ARTEDUC 2367", grade="PA", tags=["ge_lvpa"]),
      course("GENED 1201", grade="A", cr=1, tags=["ge_launch"])]
r = E.compute(osu, cs)
assert r["school"]["id"] == "osu" and r["school"]["gpa_max"] == 4.0
# (4*3 + 3.3*4 + 0*3 + 4*1) / 11 = 29.2 / 11 = 2.6545 -> 2.65
assert r["gpa_actual"] == 2.65, r["gpa_actual"]
assert r["credits"]["earned"] == 3 + 4 + 3 + 1, r["credits"]     # E earns nothing; PA earns 3
assert r["credits"]["required"] == 121                           # Economics BA total
assert r["profile"]["target_gpa"] is None and r["needed"] is None  # no target until the student sets one
keys = {a["key"]: a for a in r["audit"]}
assert keys["ge_foundations"]["count"] == "3 of 7 done"
pre = keys["major_econ_pre"]
assert pre["status"] == "missing" and pre["todo"] == ["STAT 1450"], pre      # the E doesn't count
assert keys["pf"]["used"] == 3 and keys["pf"]["cap"] == 20                       # PA used the PA/NP budget
assert keys["residency"]["need"] == 30

# 3. Electives: a 5000-level course fills the 5000-level slot before the broad 3000+ slot; exclusions hold.
cs2 = cs + [course(x, status="planned") for x in ("ECON 2002.01", "ECON 4001.01", "ECON 4002.01", "ECON 4400",
                                                     "ECON 2367.01", "ECON 3400", "ECON 3130", "ECON 4130", "ECON 5130",
                                                     "ECON 5200", "ECON 5410", "ECON 3500")]
keys = {a["key"]: a for a in E.compute(osu, cs2)["audit"]}
assert keys["major_econ_core"]["status"] == "on_track"
assert keys["major_econ_writing"]["status"] == "on_track" and keys["major_econ_writing"]["courses"][0].startswith("ECON 2367")
assert keys["major_econ_el5"]["courses"] == ["ECON 5200 (Autumn 2026)"], keys["major_econ_el5"]
assert keys["major_econ_adv"]["courses"] and keys["major_econ_adv"]["courses"][0].startswith(("ECON 5130", "ECON 5410"))
assert all("3400" not in x for x in keys["major_econ_el3"]["courses"]) and keys["major_econ_el3"]["count"] == "3 of 3"

# 4. Finance (Fisher): calculus alternative and the unscored advanced list.
fin = E.compute({"school_id": "osu", "major_id": "fin"}, [course("MATH 1151", cr=5, status="enrolled"), course("BUSFIN 4229", status="planned")])
keys = {a["key"]: a for a in fin["audit"]}
assert "MATH 1131" not in keys["major_fisher_core"]["todo"] and "ENGLISH 1110" in keys["major_fisher_core"]["todo"]
assert keys["major_fin_adv"]["status"] == "info" and keys["major_fin_adv"]["courses"]
assert fin["credits"]["required"] == 120

# 5. Finals from the first meeting of the week (Autumn 2026 schedule); TR and Friday-first classes.
cal = E.calendar_for([course("ECON 2001.01", status="enrolled", meet="MWF 9:10am-10:05am"),
                      course("BUSFIN 3220", status="enrolled", meet="TR 12:45pm-2:05pm"),
                      course("ARTEDUC 2367", status="enrolled", meet="F 1:50pm-3:45pm"),
                      course("STAT 1430", status="enrolled", meet="MWF 9:10am-10:05am, R 3:00pm-3:55pm")], schools.OSU)
finals = {e["course_id"]: e for e in cal["Autumn 2026"] if e["kind"] == "final"}
assert finals["ECON 2001.01"]["date"] == "2026-12-14" and finals["ECON 2001.01"]["time"] == "10:00 AM"
assert finals["BUSFIN 3220"]["date"] == "2026-12-15" and finals["BUSFIN 3220"]["time"] == "2:00 PM"
assert "ARTEDUC 2367" not in finals                         # Friday-first: set by the department
assert finals["STAT 1430"]["conflict"] == ["ECON 2001.01"]  # same slot as ECON 2001 -> flagged
assert any(e["label"] == "Classes begin" and e["date"] == "2026-08-25" for e in cal["Autumn 2026"])
assert any(e["label"] == "Spring break" for e in cal["Spring 2027"])

# 6. Course validation accepts Ohio State marks and GE tags, and keeps unknown tags out.
c = E.clean_course({"title": "x", "term": "Autumn 2026", "grade": "pa", "tags": ["ge_sbs", "nope", "major"], "location": "Hagerty Hall 180"})
assert c["grade"] == "PA" and c["tags"] == ["ge_sbs", "major"] and c["location"] == "Hagerty Hall 180"

# 7. Class search JSON -> Planner-ready courses; exact course filter; term codes.
assert osu_classes.term_code("Autumn 2026") == "1268" and osu_classes.term_code("Spring 2027") == "1272"
assert osu_classes.term_code("Summer 2027") == "1274" and osu_classes.term_code("Fall 2026") == "1268"
sample = {"data": {"courses": [
    {"course": {"title": "Principles of Microeconomics", "subject": "ECON", "catalogNumber": "2001.01", "minUnits": 3, "maxUnits": 3,
                "courseAttributes": [{"name": "GE", "value": "GEN", "description": "GEN Foundation: Social and Behavioral Sciences"}]},
     "sections": [{"classNumber": "1234", "section": "0010", "component": "Lecture", "enrollmentStatus": "Open", "waitlistTotal": 0,
                   "meetings": [{"startTime": "9:10 am", "endTime": "10:05 am", "monday": True, "wednesday": True, "friday": True,
                                 "buildingDescription": "Hagerty Hall 180", "instructors": [{"displayName": "Pat Lee", "role": "PI"}]}]},
                  {"classNumber": "1240", "section": "0011", "component": "Recitation", "enrollmentStatus": "Closed",
                   "meetings": [{"startTime": "12:40 pm", "endTime": "1:35 pm", "thursday": True}]}]},
    {"course": {"title": "Intermediate Micro", "subject": "ECON", "catalogNumber": "4001.01", "minUnits": 3, "maxUnits": 3}, "sections": []},
]}}
parsed = osu_classes.parse(sample)
p0 = parsed[0]
assert p0["code"] == "ECON 2001.01" and p0["credits"] == 3 and p0["ge_tags"] == ["ge_sbs"]
assert p0["sections"][0]["meetings_text"] == "MWF 9:10am-10:05am" and p0["sections"][0]["instructors"] == ["Pat Lee"]
assert E.parse_meetings(p0["sections"][0]["meetings_text"] + ", " + p0["sections"][1]["meetings_text"])[1] == {"days": ["R"], "start": "12:40", "end": "13:35"}
class Resp:
    def raise_for_status(s): pass
    def json(s): return sample
osu_classes._cache.clear()
got = osu_classes.search("ECON 2001", "Autumn 2026", get=lambda *a, **k: Resp())
assert [x["code"] for x in got] == ["ECON 2001.01"]

# 8. Code matching: suffixes optional unless the requirement names one.
assert schools.code_matches("ECON 2001.03H", "ECON 2001") and not schools.code_matches("ECON 2001", "ECON 2367.01")
assert schools.code_matches("econ 2367.01", "ECON 2367.01") and not schools.code_matches("ECONX 2001", "ECON 2001")

# 9. Current term, weekly meetings (holidays skipped, inside the term), Today's classes, the calendar file.
import datetime as dt
assert E.current_term(schools.OSU, dt.date(2026, 11, 10)) == "Autumn 2026"
assert E.current_term(schools.OSU, dt.date(2026, 12, 25)) == "Spring 2027"
micro = course("ECON 2001.01", status="enrolled", meet="MWF 9:10am-10:05am", location="Hagerty Hall 180")
wk = E.class_meetings(micro, schools.OSU, "2026-11-09", "2026-11-15")
assert [m["date"] for m in wk] == ["2026-11-09", "2026-11-13"], wk          # Nov 11 is Veterans Day
assert wk[0]["time"] == "9:10 AM – 10:05 AM"
allm = E.class_meetings(micro, schools.OSU, "0000-01-01", "9999-12-31")
assert allm[0]["date"] == "2026-08-26" and allm[-1]["date"] == "2026-12-09"   # first and last day of classes
assert not any(m["date"] in ("2026-09-07", "2026-11-27") for m in allm)
short = dict(micro, start_date="2026-10-19")                                  # second-session section
assert E.class_meetings(short, schools.OSU, "0000-01-01", "9999-12-31")[0]["date"] == "2026-10-19"
today = E.classes_on([dict(micro, id="m1"), course("STAT 1430", status="planned", meet="M 8:00am-8:55am")], {"school_id": "osu"}, dt.date(2026, 11, 9))
assert [(x["code"], x["location"]) for x in today] == [("ECON 2001.01", "Hagerty Hall 180")]
cal_file = E.ics({"school_id": "osu"}, [dict(micro, id="m1")], now=dt.datetime(2026, 11, 10))
assert cal_file.count("SUMMARY:ECON 2001.01 ECON 2001.01") == len(allm)
assert "DTSTART;TZID=America/New_York:20261109T091000" in cal_file and "LOCATION:Hagerty Hall 180" in cal_file
assert "SUMMARY:Final: ECON 2001.01" in cal_file and "DTSTART;TZID=America/New_York:20261214T100000" in cal_file
assert "DTEND;TZID=America/New_York:20261214T114500" in cal_file              # 1 h 45 min exam slot
assert all(len(l.encode()) <= 75 for l in cal_file.split("\r\n"))
assert E.clean_course({"title": "x", "term": "Autumn 2026", "start_date": "2026-08-25T00:00:00"})["start_date"] == "2026-08-25"
print("schools: Ohio State grading, GE + major audit, finals, class search, weekly classes, calendar file ok; Georgetown unchanged")

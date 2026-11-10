"""Ohio State class search: the public JSON behind classes.osu.edu (content.osu.edu/v2/classes/search, no key).

search(q, term) -> courses with their sections, meeting days and times, rooms and instructors, ready for the Planner.
Public catalog data only; cached in memory by (query, term), never per user."""
import re
import time
import requests
import schools

URL = "https://content.osu.edu/v2/classes/search"
_cache = {}
TTL = 3600
DAYS = [("monday", "M"), ("tuesday", "T"), ("wednesday", "W"), ("thursday", "R"), ("friday", "F"),
        ("saturday", "S"), ("sunday", "U")]


def term_code(term):
    """'Autumn 2026' -> '1268' (1 + two-digit year + 2 Spring / 4 Summer / 8 Autumn)."""
    m = re.match(r"\s*(autumn|fall|spring|summer)\s+(\d{4})\s*$", term or "", re.I)
    if not m:
        return None
    return f"1{m.group(2)[2:]}{ {'autumn': 8, 'fall': 8, 'spring': 2, 'summer': 4}[m.group(1).lower()] }"


def _t(s):
    """'9:10 am' -> '9:10am' (the Planner's meeting-time parser reads this form)."""
    return re.sub(r"\s+", "", (s or "").lower())


def _attrs(items):
    out = []
    for a in items or []:
        if isinstance(a, dict):
            out.append(" ".join(str(a.get(k) or "") for k in ("name", "value", "description")).strip())
        elif a:
            out.append(str(a))
    return out


def parse(data):
    """The API's JSON -> a compact list of courses."""
    out = []
    for item in ((data or {}).get("data") or {}).get("courses") or []:
        c = item.get("course") or {}
        code = f"{(c.get('subject') or '').upper()} {c.get('catalogNumber') or ''}".strip()
        lo, hi = c.get("minUnits"), c.get("maxUnits")
        sections, ge_src = [], _attrs(c.get("courseAttributes"))
        for s in item.get("sections") or []:
            meetings, instructors = [], []
            for m in s.get("meetings") or []:
                days = "".join(ch for key, ch in DAYS if m.get(key))
                for i in m.get("instructors") or []:
                    n = (i or {}).get("displayName")
                    if n and n not in instructors:
                        instructors.append(n)
                if days and m.get("startTime") and m.get("endTime"):
                    meetings.append({"days": days, "start": _t(m["startTime"]), "end": _t(m["endTime"]),
                                     "location": m.get("buildingDescription") or m.get("facilityDescription") or ""})
            ge_src += _attrs(s.get("attributes"))
            sections.append({
                "class_number": str(s.get("classNumber") or ""), "section": s.get("section") or "",
                "component": s.get("component") or "", "mode": s.get("instructionMode") or "",
                "status": s.get("enrollmentStatus") or "", "enrolled": s.get("enrollmentTotal"),
                "waitlist": s.get("waitlistTotal"), "session": s.get("sessionDescription") or "",
                "start_date": s.get("startDate"), "end_date": s.get("endDate"), "instructors": instructors,
                "meetings": meetings,
                "meetings_text": ", ".join(f"{m['days']} {m['start']}-{m['end']}" for m in meetings),
            })
        out.append({"code": code, "title": c.get("title") or "", "credits": lo if lo == hi else None,
                    "credits_range": [lo, hi], "description": (c.get("description") or "")[:600],
                    "ge_tags": schools.ge_tags_from(ge_src), "sections": sections})
    return out


def search(q, term, get=requests.get):
    q = (q or "").strip()[:60]
    code = term_code(term)
    if not q or not code:
        return []
    key = (q.lower(), code)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    r = get(URL, params={"q": q, "campus": "col", "term": code, "p": 1}, timeout=15)
    r.raise_for_status()
    courses = parse(r.json())
    want = schools.split_code(q.replace(" ", "")) or schools.split_code(q)
    if want:          # "ECON 2001" -> that course (any section suffix), not every fuzzy match
        exact = [c for c in courses if schools.split_code(c["code"]) and schools.split_code(c["code"])[:2] == want[:2]]
        courses = exact or courses
    courses = courses[:25]
    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = (time.time(), courses)
    return courses

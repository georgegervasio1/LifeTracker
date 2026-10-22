"""Offline test of edutasks.py (My Day, tasks, quick add, AI plan, Gmail) with made-up courses."""
import os as _os
HERE = _os.environ.get("LT_TESTS") or _os.path.dirname(_os.path.abspath(__file__))
src = open(_os.path.join(HERE, "test_health.py")).read()
exec(src.split("# ---- fake WHOOP HTTP")[0])          # fake Firestore + app setup
import base64, json, types
from datetime import date
import edutasks
from edutasks import parse_quick

D = date(2026, 10, 22)                                  # a Thursday
C = [{"id": "c1", "title": "Constitutional Law"}, {"id": "c2", "title": "Mini MBA for Lawyers"},
     {"id": "c3", "title": "Bankruptcy"}, {"id": "c4", "title": "Environmental Law"}, {"id": "c5", "title": "Gen AI and Big Law"}]
cases = [
    ("Read ch. 4 for Con Law by Friday 6pm", "Read ch. 4", "2026-10-23", "6:00 PM", "c1"),
    ("Outline bankruptcy tomorrow", "Outline", "2026-10-23", None, "c3"),
    ("mini mba problem set due Dec 4 at 9am", "Problem set", "2026-12-04", "9:00 AM", "c2"),
    ("Email the registrar", "Email the registrar", None, None, None),
    ("Env law paper draft 11/30", "Paper draft", "2026-11-30", None, "c4"),
    ("Call mom at 7:30 pm", "Call mom", "2026-10-22", "7:30 PM", None),
    ("Review for the law review meeting next monday", "Review for the law review meeting", "2026-11-02", None, None),
    ("Gen AI reflection in 3 days", "Reflection", "2026-10-25", None, "c5"),
    ("Pay rent on thursday", "Pay rent", "2026-10-22", None, None),
    ("Finish Jan 5 memo", "Finish memo", "2027-01-05", None, None),
]
for text, title, d, t, cid in cases:
    p = parse_quick(text, D, C)
    print(f"  {text!r:52} -> {p}")
    assert (p["title"], p["due_date"], p["due_time"], p["course_id"]) == (title, d, t, cid), (text, p)

c = main.app.test_client()
q = "?date=2026-10-22"
c.post("/api/edu/import", json={"replace": True, "profile": {"terms": ["Fall 2026", "Spring 2027"]}, "courses": [
    {"term": "Fall 2026", "title": "Constitutional Law", "credits": 4, "status": "enrolled", "exam_date": "2026-12-04", "exam_time": "9:00 AM", "exam_type": "In-class exam"},
    {"term": "Fall 2026", "title": "Bankruptcy", "credits": 2, "status": "enrolled", "exam_date": "2026-12-04", "exam_time": "1:30 PM", "exam_type": "In-class exam"},
    {"term": "Spring 2027", "title": "Evidence", "credits": 4, "status": "planned"}]})
r = c.post("/api/edu/syllabi/import", json={"syllabi": [{"course_title": "Constitutional Law", "term": "Fall 2026", "short": "Con Law",
    "sessions": [{"date": "2026-10-22", "time": "10:30 AM", "title": "Commerce Clause", "reading": "pp. 100-140"},
                 {"date": "2026-10-23", "title": "Spending power", "reading": "pp. 141-170"},
                 {"date": "2026-11-20", "title": "Later", "reading": "pp. 400-420"}],
    "deadlines": [{"date": "2026-11-02", "title": "Response paper", "kind": "paper"}]}]})
assert r.status_code == 200, r.get_json()

v = c.get("/api/edu/today" + q).get_json()
print("classes:", v["classes"]); print("suggestions:", [(s["key"], s["title"]) for s in v["suggestions"]])
assert v["term"] == "Fall 2026" and v["classes"][0]["title"] == "Commerce Clause"
assert [s["group"] for s in v["suggestions"]] == ["syllabus"] * 3 and v["next_final"]["days"] == 43
assert {x["title"] for x in v["courses"]} == {"Con Law", "Bankruptcy"}

# quick add on My Day; course + date parsed
r = c.post("/api/edu/tasks" + q, json={"text": "Outline con law by Friday 5pm", "my_day": True}).get_json()
t1 = r["task"]; assert t1["course_id"] and t1["due_date"] == "2026-10-23" and t1["my_day"] == "2026-10-22"
assert [t["title"] for t in r["my_day"]] == ["Outline"]
# add a suggestion as a task: it leaves the suggestions
s0 = v["suggestions"][0]
r = c.post("/api/edu/tasks" + q, json={"title": s0["title"], "due_date": s0["due_date"], "course_id": s0["course_id"],
                                       "source": "syllabus", "source_ref": s0["key"], "my_day": True}).get_json()
assert s0["key"] not in [s["key"] for s in r["suggestions"]] and len(r["my_day"]) == 2
r = c.post("/api/edu/tasks" + q, json={"title": "dup", "source_ref": s0["key"]}).get_json()
assert len(c.get("/api/edu/tasks" + q).get_json()["tasks"]) == 2      # same suggestion twice: no duplicate
# dismiss
r = c.post("/api/edu/suggestions/dismiss" + q, json={"key": r["suggestions"][0]["key"]}).get_json(); assert len(r["suggestions"]) == 1
# a task due soon but not on My Day shows up as an "upcoming" suggestion; overdue tasks join My Day
c.post("/api/edu/tasks" + q, json={"title": "Bluebook check", "due_date": "2026-10-24"})
c.post("/api/edu/tasks" + q, json={"title": "Late thing", "due_date": "2026-10-20"})
v = c.get("/api/edu/today" + q).get_json()
assert any(s["group"] == "upcoming" and s["title"] == "Bluebook check" for s in v["suggestions"])
assert v["counts"]["overdue"] == 1 and v["my_day"][0]["title"] == "Late thing"
# complete, undo, delete
r = c.patch(f"/api/edu/tasks/{t1['id']}" + q, json={"done": True}).get_json()
assert "Outline" not in [t["title"] for t in r["my_day"]]
r = c.patch(f"/api/edu/tasks/{t1['id']}" + q, json={"done": False, "my_day": False, "important": True}).get_json()
assert r["counts"]["important"] == 1 and "Outline" not in [t["title"] for t in r["my_day"]]
assert c.patch(f"/api/edu/tasks/{t1['id']}", json={"due_date": "12/4"}).status_code == 400
assert c.post("/api/edu/tasks", json={"title": "  "}).status_code == 400
c.delete(f"/api/edu/tasks/{t1['id']}" + q)

# ---- AI plan with a fake Claude
class Block:
    def __init__(s, inp): s.type, s.input = "tool_use", inp
class FakeClaude:
    def __init__(s, reply): s.reply, s.prompts = reply, []
    def create(s, **k): s.prompts.append(k["messages"][0]["content"]); return types.SimpleNamespace(content=[Block(s.reply)])
import assistant
os.environ["ANTHROPIC_API_KEY"] = "test"
tid = next(t["id"] for t in c.get("/api/edu/tasks" + q).get_json()["tasks"] if t["title"] == "Bluebook check")
fake = FakeClaude({"summary": "Readings before class, review later.", "blocks": [
    {"date": "2026-10-22", "start": "7:00 PM", "end": "8:30 PM", "title": "Read pp. 141-170", "course": "Constitutional Law", "kind": "reading"},
    {"date": "2026-10-23", "start": "2:00 PM", "end": "3:00 PM", "title": "Bluebook check", "task_id": tid, "kind": "task"},
    {"date": "2026-10-10", "start": "2:00 PM", "end": "3:00 PM", "title": "in the past", "kind": "task"},
    {"date": "2026-10-24", "start": "25:00", "end": "3:00 PM", "title": "bad time", "kind": "task"}]})
assistant.client = lambda: types.SimpleNamespace(messages=fake)
p = c.post("/api/edu/plan" + q, json={"days": 7}).get_json()["plan"]
print("plan:", [(b["date"], b["start"], b["title"]) for b in p["blocks"]])
assert len(p["blocks"]) == 2 and p["blocks"][0]["course_id"] and p["blocks"][1]["task_id"] == tid
assert "pp. 141-170" in fake.prompts[0] and "Bluebook check" in fake.prompts[0] and "Evidence" not in fake.prompts[0]
r = c.post("/api/edu/plan/accept" + q, json={"indexes": [0]}).get_json(); assert r["added"] == 1
assert c.post("/api/edu/plan/accept" + q, json={"indexes": [0]}).get_json()["added"] == 0
assert any(t["title"] == "Read pp. 141-170" and t["due_time"] == "7:00 PM" for t in r["my_day"])

# ---- Gmail with fake HTTP + fake Claude
b64 = lambda s: base64.urlsafe_b64encode(s.encode()).decode()
MAILS = {"m1": {"id": "m1", "threadId": "t1", "payload": {"headers": [{"name": "From", "value": "Registrar <reg@school.edu>"},
                {"name": "Subject", "value": "Exam accommodations form"}, {"name": "Date", "value": "Wed, 21 Oct 2026"}],
                "mimeType": "multipart/alternative", "parts": [{"mimeType": "text/html", "body": {"data": b64("<p>Submit the form by <b>Nov 1</b>.</p>")}}]}},
         "m2": {"id": "m2", "threadId": "t2", "payload": {"headers": [{"name": "From", "value": "Shop <deals@shop.com>"},
                {"name": "Subject", "value": "Sale"}], "mimeType": "text/plain", "body": {"data": b64("50% off")}}}}
calls = []
class R:
    def __init__(s, code, js): s.status_code, s._js, s.ok = code, js, code < 400
    def json(s): return s._js
    def raise_for_status(s): pass
def fake_get(url, headers=None, params=None, timeout=None):
    calls.append(url); assert headers["Authorization"] == "Bearer tok"
    if url.endswith("/messages"): return R(200, {"messages": [{"id": "m1"}, {"id": "m2"}]})
    return R(200, MAILS[url.rsplit("/", 1)[1]])
edutasks.requests = types.SimpleNamespace(get=fake_get, RequestException=Exception)
fake = FakeClaude({"items": [{"message_id": "m1", "kind": "deadline", "title": "Submit exam accommodations form", "date": "2026-11-01",
                              "why": "The registrar asks for the form by Nov 1."},
                             {"message_id": "m1", "kind": "event", "title": "old", "date": "2026-10-01"},
                             {"message_id": "zzz", "kind": "action", "title": "unknown message"}]})
r = c.post("/api/edu/gmail/scan" + q, json={"access_token": "tok"}).get_json()
print("gmail:", r["read"], r["new_items"], [(s["title"], s["from"]) for s in r["suggestions"] if s["group"] == "email"])
assert r["read"] == 2 and r["new_items"] == 1 and r["gmail"]["connected"]
em = next(s for s in r["suggestions"] if s["group"] == "email")
assert em["from"] == "Registrar" and em["link"].endswith("#all/t1") and r["suggestions"][0]["group"] == "email"
assert "Submit the form by" in fake.prompts[0] and "<b>" not in fake.prompts[0] and "tok" not in json.dumps(DATA, default=str)
n = len(calls); c.post("/api/edu/gmail/scan" + q, json={"access_token": "tok"}); assert len(calls) == n + 1   # already-read mail isn't fetched again
assert c.post("/api/edu/gmail/scan", json={}).status_code == 400
r = c.delete("/api/edu/gmail" + q).get_json(); assert not r["gmail"]["connected"] and not any(s["group"] == "email" for s in r["suggestions"])
print("ALL EDU TASK TESTS PASSED")

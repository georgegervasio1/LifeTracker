// Education → Today (My Day) and Tasks. Server logic lives in backend/edutasks.py.
import { getAuth, GoogleAuthProvider, reauthenticateWithPopup }
  from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import { enter, collapse } from "./motion.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n) => String(n).padStart(2, "0");
const localDate = (d = new Date()) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const addDays = (s, n) => { const d = new Date(s + "T00:00"); d.setDate(d.getDate() + n); return localDate(d); };
const fd = (s, o) => new Date(s + "T00:00").toLocaleDateString("en-US", o);
const GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly";
const CHECK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 12.5l4 4 8-9"/></svg>';
const STAR = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.25 4.15 1 5.85L12 16.9l-5.25 2.8 1-5.85L3.5 9.7l5.9-.9z"/></svg>';

let ctx;                      // { api, toast }
let V = null;                 // last /api/edu/today (or /tasks) response
let allTasks = [];
let tkList = "myday";         // Tasks tab: current list
let showDone = false;
let plan = null;

export function mountEduToday(c) {
  ctx = c;
  if (matchMedia("(max-width: 760px)").matches) $("mdAddInput").placeholder = "Add a task";
  $("mdAdd").onsubmit = (e) => { e.preventDefault(); quickAdd($("mdAddInput"), true); };
  $("tkAdd").onsubmit = (e) => { e.preventDefault(); quickAdd($("tkAddInput"), tkList === "myday", tkList.startsWith("course:") ? tkList.slice(7) : null); };
  $("mdSuggBtn").onclick = () => {
    const side = $("mdSide");
    if (matchMedia("(max-width: 900px)").matches) side.scrollIntoView({ behavior: "smooth", block: "start" });
    else document.querySelector(".myday-layout").classList.toggle("side-hidden");
  };
  $("mdDoneToggle").onclick = () => { showDone = !showDone; drawDone(); };
  $("mdPlanBtn").onclick = $("tkPlanBtn").onclick = openPlan;
  $("taskCancel").onclick = () => $("taskDialog").close();
  $("planClose").onclick = () => $("planDialog").close();
  $("planMake").onclick = makePlan;
  $("planAccept").onclick = acceptPlan;
}

const q = () => `?date=${localDate()}`;
async function call(path, opts) {
  const sep = path.includes("?") ? "&" : "?";
  return ctx.api(path + sep + `date=${localDate()}`, opts);
}

// ---------------------------------------------------------------- Today
export async function loadToday() {
  V = await ctx.api("/api/edu/today" + q());
  drawToday(true);
}

function timeOfDay(h) {
  return h < 5 ? "night" : h < 11 ? "morning" : h < 17 ? "day" : h < 21 ? "evening" : "night";
}

function drawToday(first) {
  const now = new Date(), tod = timeOfDay(now.getHours());
  $("myDay").dataset.tod = tod;
  $("mdGreet").textContent = { morning: "Good morning", day: "Good afternoon", evening: "Good evening", night: "Good evening" }[tod];
  $("mdDate").textContent = now.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  const n = V.next_final, c = V.counts;
  const chips = [];
  if (n) chips.push(`<span class="glass-chip"><strong>${n.days === 0 ? "Today" : n.days === 1 ? "Tomorrow" : `${n.days} days`}</strong> until ${esc(n.label.replace(/^(Final|Paper due): /, ""))} ${n.label.startsWith("Paper") ? "paper" : "final"}</span>`);
  if (c.overdue) chips.push(`<span class="glass-chip warn"><strong>${c.overdue}</strong> overdue</span>`);
  if (V.classes.length) chips.push(`<span class="glass-chip"><strong>${V.classes.filter((x) => !x.no_class).length}</strong> class${V.classes.length > 1 ? "es" : ""} today</span>`);
  $("mdStats").innerHTML = chips.join("");
  drawMyDayList(first);
  drawDone();
  drawSuggestions();
  drawBelow();
  if (first) {
    const md = $("myDay"); md.classList.remove("arrive"); void md.offsetWidth; md.classList.add("arrive");
    enter(document.querySelector('[data-panel="today"]'));
  }
}

function dueLabel(t) {
  if (!t.due_date) return "";
  const td = V?.date || localDate(), d = t.due_date;
  const day = d === td ? "Today" : d === addDays(td, 1) ? "Tomorrow" : d === addDays(td, -1) ? "Yesterday"
    : Math.abs((new Date(d) - new Date(td)) / 864e5) < 7 ? fd(d, { weekday: "long" }) : fd(d, { weekday: "short", month: "short", day: "numeric" });
  const range = t.block_end ? `${t.due_time}–${t.block_end}` : t.due_time;
  return `<span class="${d < td ? "due-late" : d === td ? "due-today" : ""}">${day}${range ? ` · ${esc(range)}` : ""}</span>`;
}

function taskRow(t, { onDay = true } = {}) {
  const course = V.course_names?.[t.course_id];
  const src = { syllabus: "From syllabus", email: "From email", plan: "Planned block" }[t.source];
  const meta = [course && esc(course), dueLabel(t), src && `<span class="t-src">${src}</span>`, t.notes && '<span class="t-src">Note</span>'].filter(Boolean);
  return `<div class="task ${t.done ? "is-done" : ""}" data-task="${t.id}" data-enter>
    <button class="t-check" data-check="${t.id}" aria-label="${t.done ? "Mark not done" : "Mark done"}">${CHECK}</button>
    <button class="t-body" data-open="${t.id}"><span class="t-title">${esc(t.title)}</span>${meta.length ? `<span class="t-meta">${meta.join('<i class="dot"></i>')}</span>` : ""}</button>
    ${onDay ? "" : `<button class="t-day ${t.my_day === V.date ? "on" : ""}" data-day="${t.id}" title="${t.my_day === V.date ? "Remove from My Day" : "Add to My Day"}">${t.my_day === V.date ? "On My Day" : "My Day"}</button>`}
    <button class="t-star ${t.important ? "on" : ""}" data-star="${t.id}" aria-label="${t.important ? "Not important" : "Important"}">${STAR}</button>
  </div>`;
}

function drawMyDayList() {
  const list = V.my_day;
  $("mdList").innerHTML = list.length ? list.map((t) => taskRow(t)).join("")
    : `<div class="myday-empty"><p>Nothing on your day yet.</p><p class="sub">Add a task below, or pick from Suggestions.</p></div>`;
  wire($("mdList"));
}

function drawDone() {
  const d = V.done_today || [];
  $("mdDoneWrap").classList.toggle("hidden", !d.length);
  $("mdDoneToggle").textContent = `${showDone ? "Hide" : "Show"} completed (${d.length})`;
  $("mdDone").classList.toggle("hidden", !showDone);
  $("mdDone").innerHTML = d.map((t) => taskRow(t)).join("");
  wire($("mdDone"));
}

function drawSuggestions() {
  const S = V.suggestions, g = V.gmail;
  $("mdSuggCount").textContent = S.length ? `${S.length}` : "";
  $("mdSuggBtn").textContent = S.length ? `Suggestions · ${S.length}` : "Suggestions";
  const ago = g.last_scan ? relTime(g.last_scan) : "";
  $("mdMail").innerHTML = g.connected
    ? `<div class="mail-row"><span>School email checked ${esc(ago)}</span><button class="text-btn" id="mdScan">Check now</button></div>`
    : `<div class="mail-card"><h4>Bring in school email</h4><p>LifeTracker reads your recent Georgetown email (read-only) and suggests events, deadlines and messages to answer. Nothing is sent, changed or kept besides the suggestions.</p><button class="btn btn-primary btn-sm" id="mdScan">Connect Gmail</button></div>`;
  $("mdScan").onclick = scanMail;
  const groups = [["email", "From school email"], ["syllabus", "From your syllabi"], ["upcoming", "Due soon"]];
  $("mdSugg").innerHTML = groups.map(([k, label]) => {
    const rows = S.filter((s) => s.group === k);
    if (!rows.length) return "";
    return `<div class="sugg-group"><p class="sugg-label">${label}</p>${rows.map((s) => `<div class="sugg" data-key="${esc(s.key)}">
      <button class="sugg-add" data-add="${esc(s.key)}" aria-label="Add to My Day">+</button>
      <div class="sugg-body"><span class="t-title">${esc(s.title)}</span>
        <span class="t-meta">${[s.from && esc(s.from), V.course_names?.[s.course_id] && esc(V.course_names[s.course_id]), dueLabel(s), s.link && `<a href="${esc(s.link)}" target="_blank" rel="noopener">Open email</a>`].filter(Boolean).join('<i class="dot"></i>')}</span>
        ${s.detail ? `<span class="sugg-detail">${esc(s.detail)}</span>` : ""}</div>
      <button class="sugg-x" data-dismiss="${esc(s.key)}" aria-label="Dismiss">×</button></div>`).join("")}</div>`;
  }).join("") || `<p class="empty small">No suggestions right now. Upload syllabi to get readings and deadlines here.</p>`;
  $("mdSugg").querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => addSuggestion(S.find((s) => s.key === b.dataset.add), b)));
  $("mdSugg").querySelectorAll("[data-dismiss]").forEach((b) => (b.onclick = async () => {
    await collapse(b.closest(".sugg"));
    try { V = await call("/api/edu/suggestions/dismiss", { method: "POST", body: JSON.stringify({ key: b.dataset.dismiss }) }); drawSuggestions(); }
    catch (e) { ctx.toast(e.message); }
  }));
}

function drawBelow() {
  $("mdClasses").innerHTML = V.classes.length ? V.classes.map((c) => `<div class="mini-row stack"><span><strong>${esc(c.course)}</strong>${c.time ? ` · ${esc(c.time)}` : ""}${c.no_class ? " · no class" : ""}</span>
      <span class="sub">${esc([c.title, c.reading && `Reading: ${c.reading}`].filter(Boolean).join(" · "))}</span></div>`).join("")
    : `<p class="empty small">${(V.course_names && Object.keys(V.course_names).length) ? "No classes from your syllabi today. Upload syllabi to see each day’s classes and readings." : "Import your courses to get started."}</p>`;
  $("mdUpcoming").innerHTML = V.upcoming.length ? V.upcoming.slice(0, 8).map((e) => `<div class="mini-row"><span><i class="k-dot k-${e.kind}"></i>${esc(e.label)}</span>
      <span class="tag">${fd(e.date, { weekday: "short", month: "short", day: "numeric" })}${e.end ? ` – ${fd(e.end, { month: "short", day: "numeric" })}` : ""}${e.time ? ` · ${esc(e.time)}` : ""}</span></div>`).join("")
    : `<p class="empty small">Nothing on the academic calendar in the next two weeks.</p>`;
  const F = V.finals || [];
  $("mdFinals").classList.toggle("hidden", !F.length);
  $("mdFinals").innerHTML = `<div class="card-head"><h3>Finals and papers</h3><span class="card-meta">${esc(V.term || "")}</span></div>
    <div class="finals-row">${F.map((e) => `<div class="final-tile"><span class="final-days">${e.days}</span><span class="final-unit">day${e.days === 1 ? "" : "s"}</span>
      <span class="final-name">${esc(e.label.replace(/^(Final|Paper due): /, ""))}</span><span class="final-when">${e.label.startsWith("Paper") ? "Paper due " : ""}${fd(e.date, { weekday: "short", month: "short", day: "numeric" })}${e.time ? ` · ${esc(e.time)}` : ""}</span></div>`).join("")}</div>`;
}

function relTime(isoStr) {
  const m = Math.round((Date.now() - new Date(isoStr)) / 6e4);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : new Date(isoStr).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

// ---------------------------------------------------------------- actions
function refresh(r) {
  V = r;
  if (r.tasks) allTasks = r.tasks;
  if (document.querySelector('[data-panel="today"].active')) drawToday(false);
  if (document.querySelector('[data-panel="tasks"].active')) loadTasks(true).catch((e) => ctx.toast(e.message));
}

async function quickAdd(input, onDay, courseId) {
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  try {
    const r = await call("/api/edu/tasks", { method: "POST", body: JSON.stringify({ text, my_day: onDay, ...(courseId ? { course_id: courseId } : {}) }) });
    refresh(r);
    const row = document.querySelector(`[data-task="${r.task.id}"]`);
    if (row) { row.classList.add("just-added"); row.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
  } catch (e) { input.value = text; ctx.toast(e.message); }
}

async function addSuggestion(s, btn) {
  if (!s) return;
  btn.closest(".sugg").classList.add("taking");
  try {
    const r = s.task_id
      ? await call(`/api/edu/tasks/${s.task_id}`, { method: "PATCH", body: JSON.stringify({ my_day: true }) })
      : await call("/api/edu/tasks", { method: "POST", body: JSON.stringify({ title: s.title, due_date: s.due_date, due_time: s.due_time, course_id: s.course_id,
          notes: [s.detail, s.subject && `Email: ${s.subject}`, s.link].filter(Boolean).join("\n") || null,
          source: s.group === "email" ? "email" : s.group === "syllabus" ? "syllabus" : "manual", source_ref: s.key, my_day: true }) });
    await collapse(btn.closest(".sugg"), 200);
    refresh(r);
    ctx.toast("Added to My Day");
  } catch (e) { btn.closest(".sugg").classList.remove("taking"); ctx.toast(e.message); }
}

function findTask(id) {
  return [...(V.my_day || []), ...(V.done_today || []), ...allTasks].find((t) => t.id === id);
}

function wire(root) {
  root.querySelectorAll("[data-check]").forEach((b) => (b.onclick = async () => {
    const t = findTask(b.dataset.check), row = b.closest(".task"), done = !t.done;
    row.classList.toggle("checking", done);
    row.classList.toggle("is-done", done);
    await new Promise((r) => setTimeout(r, done ? 420 : 0));
    try {
      const r = await call(`/api/edu/tasks/${t.id}`, { method: "PATCH", body: JSON.stringify({ done }) });
      if (done && !showDone && root.id !== "tkList") await collapse(row);
      refresh(r);
    } catch (e) { row.classList.remove("checking", "is-done"); ctx.toast(e.message); }
  }));
  root.querySelectorAll("[data-star]").forEach((b) => (b.onclick = async () => {
    const t = findTask(b.dataset.star);
    b.classList.toggle("on");
    try { refresh(await call(`/api/edu/tasks/${t.id}`, { method: "PATCH", body: JSON.stringify({ important: !t.important }) })); }
    catch (e) { ctx.toast(e.message); }
  }));
  root.querySelectorAll("[data-day]").forEach((b) => (b.onclick = async () => {
    const t = findTask(b.dataset.day);
    try { refresh(await call(`/api/edu/tasks/${t.id}`, { method: "PATCH", body: JSON.stringify({ my_day: t.my_day !== V.date }) })); }
    catch (e) { ctx.toast(e.message); }
  }));
  root.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => openTask(findTask(b.dataset.open))));
}

function openTask(t) {
  const f = $("taskForm");
  f.title.value = t.title || ""; f.due_date.value = t.due_date || ""; f.due_time.value = t.due_time || ""; f.notes.value = t.notes || "";
  f.my_day.checked = t.my_day === V.date; f.important.checked = !!t.important;
  $("taskCourse").innerHTML = `<option value="">None</option>` + (V.courses || []).map((c) => `<option value="${c.id}" ${c.id === t.course_id ? "selected" : ""}>${esc(c.title)}</option>`).join("");
  if (t.course_id && !(V.courses || []).some((c) => c.id === t.course_id)) $("taskCourse").insertAdjacentHTML("beforeend", `<option value="${t.course_id}" selected>${esc(V.course_names?.[t.course_id] || "Other course")}</option>`);
  $("taskSource").textContent = { syllabus: "Added from a syllabus.", email: "Added from school email.", plan: `A block from your sample schedule${t.block_end ? `, until ${t.block_end}` : ""}.` }[t.source] || "";
  f.onsubmit = async (e) => {
    e.preventDefault();
    const body = { title: f.title.value, due_date: f.due_date.value || null, due_time: f.due_time.value || null, notes: f.notes.value || null,
      course_id: f.course_id.value || null, my_day: f.my_day.checked, important: f.important.checked };
    try { refresh(await call(`/api/edu/tasks/${t.id}`, { method: "PATCH", body: JSON.stringify(body) })); $("taskDialog").close(); }
    catch (err) { ctx.toast(err.message); }
  };
  $("taskDelete").onclick = async () => {
    try { refresh(await call(`/api/edu/tasks/${t.id}`, { method: "DELETE" })); $("taskDialog").close(); ctx.toast("Task deleted"); }
    catch (err) { ctx.toast(err.message); }
  };
  $("taskDialog").showModal();
}

// ---------------------------------------------------------------- Tasks tab
export async function loadTasks(fetch = true) {
  if (fetch || !V) { const r = await ctx.api("/api/edu/tasks" + q()); V = r; allTasks = r.tasks; }
  const td = V.date, open = allTasks.filter((t) => !t.done);
  const lists = [["myday", "My Day", (t) => !t.done && (t.my_day === td || (t.due_date && t.due_date <= td))],
    ["important", "Important", (t) => !t.done && t.important], ["planned", "Planned", (t) => !t.done && t.due_date],
    ["all", "All tasks", (t) => !t.done], ["done", "Completed", (t) => t.done]];
  const courses = (V.courses || []).map((c) => [`course:${c.id}`, c.title, (t) => !t.done && t.course_id === c.id]);
  const every = [...lists, ...courses];
  if (!every.some(([k]) => k === tkList)) tkList = "myday";
  const count = (fn) => allTasks.filter(fn).length;
  $("tkNav").innerHTML = lists.map(([k, label, fn]) => `<button class="${k === tkList ? "active" : ""}" data-list="${k}"><span>${label}</span><span class="n">${count(fn) || ""}</span></button>`).join("")
    + (courses.length ? `<p class="tasks-nav-label">Courses</p>` + courses.map(([k, label, fn]) => `<button class="${k === tkList ? "active" : ""}" data-list="${k}"><span>${esc(label)}</span><span class="n">${count(fn) || ""}</span></button>`).join("") : "");
  $("tkNav").querySelectorAll("[data-list]").forEach((b) => (b.onclick = () => { tkList = b.dataset.list; loadTasks(false); }));
  const [, title, fn] = every.find(([k]) => k === tkList);
  const rows = allTasks.filter(fn).sort((a, b) => (tkList === "done" ? (b.done_at || "").localeCompare(a.done_at || "") : (a.due_date || "9999").localeCompare(b.due_date || "9999")));
  $("tkTitle").textContent = title;
  $("tkSub").textContent = tkList === "myday" ? new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" }) : `${rows.length} task${rows.length === 1 ? "" : "s"}`;
  $("tkAdd").classList.toggle("hidden", tkList === "done");
  $("tkAddInput").placeholder = tkList.startsWith("course:") ? `Add a task for ${title}` : tkList === "myday" ? "Add a task to My Day" : "Add a task";
  const groups = tkList === "done" ? [["Completed", rows]] : bucket(rows, td);
  $("tkList").innerHTML = groups.map(([label, list]) => list.length ? `<div class="task-group"><p class="sugg-label">${label}</p>${list.map((t) => taskRow(t, { onDay: tkList === "myday" })).join("")}</div>` : "").join("")
    || `<p class="empty">${tkList === "done" ? "Nothing completed yet." : open.length ? "Nothing in this list." : "No tasks yet. Add one above."}</p>`;
  wire($("tkList"));
  enter($("tkList"), { step: 25 });
}

function bucket(rows, td) {
  const wk = addDays(td, 7), b = { Overdue: [], Today: [], Tomorrow: [], "This week": [], Later: [], "No date": [] };
  rows.forEach((t) => {
    const d = t.due_date;
    (!d ? b["No date"] : d < td ? b.Overdue : d === td ? b.Today : d === addDays(td, 1) ? b.Tomorrow : d <= wk ? b["This week"] : b.Later).push(t);
  });
  return Object.entries(b);
}

// ---------------------------------------------------------------- AI sample schedule
async function openPlan() {
  $("planDialog").showModal();
  if (!plan) { try { plan = (await ctx.api("/api/edu/plan")).plan; } catch { plan = null; } }
  drawPlan();
}

function drawPlan(loading) {
  const out = $("planOut");
  $("planAccept").classList.toggle("hidden", !plan?.blocks?.length || loading);
  $("planNote").textContent = plan?.created_at && !loading ? `Made ${relTime(plan.created_at)}` : "";
  if (loading) { out.innerHTML = `<div class="plan-loading"><span></span><span></span><span></span><p>Reading your syllabi, tasks and finals…</p></div>`; return; }
  if (!plan?.blocks?.length) { out.innerHTML = plan ? `<p class="empty small">No blocks came back. Try again, or add more detail above.</p>` : ""; return; }
  const days = {};
  plan.blocks.forEach((b, i) => (days[b.date] ||= []).push([b, i]));
  out.innerHTML = (plan.summary ? `<p class="plan-summary">${esc(plan.summary)}</p>` : "") + Object.entries(days).map(([d, list]) => `<div class="plan-day" data-enter>
      <p class="sugg-label">${fd(d, { weekday: "long", month: "short", day: "numeric" })}</p>
      ${list.map(([b, i]) => `<label class="plan-block k-${b.kind}"><input type="checkbox" data-bi="${i}" checked /><span class="pb-time">${esc(b.start)} – ${esc(b.end)}</span>
        <span class="pb-title">${esc(b.title)}${b.course_id && V?.course_names?.[b.course_id] ? `<span class="t-meta">${esc(V.course_names[b.course_id])}</span>` : ""}</span></label>`).join("")}</div>`).join("");
  enter(out, { step: 30 });
}

async function makePlan() {
  const btn = $("planMake"); btn.disabled = true; drawPlan(true);
  try {
    plan = (await call("/api/edu/plan", { method: "POST", body: JSON.stringify({ days: +$("planDays").value, prefs: $("planPrefs").value }) })).plan;
  } catch (e) { ctx.toast(e.message); }
  btn.disabled = false; btn.textContent = "Make a new one"; drawPlan();
}

async function acceptPlan() {
  const idx = [...$("planOut").querySelectorAll("[data-bi]:checked")].map((x) => +x.dataset.bi);
  if (!idx.length) return ctx.toast("Choose at least one block");
  try {
    const r = await call("/api/edu/plan/accept", { method: "POST", body: JSON.stringify({ indexes: idx }) });
    $("planDialog").close(); refresh(r);
    ctx.toast(r.added ? `Added ${r.added} block${r.added === 1 ? "" : "s"} to your tasks` : "Those blocks are already in your tasks");
  } catch (e) { ctx.toast(e.message); }
}

// ---------------------------------------------------------------- Gmail
let mailToken = null;     // short-lived, kept in memory only
async function gmailToken(force) {
  if (mailToken && !force && mailToken.exp > Date.now()) return mailToken.value;
  const auth = getAuth(), p = new GoogleAuthProvider();
  p.addScope(GMAIL_SCOPE);
  p.setCustomParameters({ login_hint: auth.currentUser.email || "" });
  const res = await reauthenticateWithPopup(auth.currentUser, p);
  const cred = GoogleAuthProvider.credentialFromResult(res);
  if (!cred?.accessToken) throw new Error("Google didn't grant Gmail access");
  mailToken = { value: cred.accessToken, exp: Date.now() + 50 * 6e4 };
  return mailToken.value;
}

async function scanMail() {
  const b = $("mdScan"); b.disabled = true; const label = b.textContent; b.textContent = "Reading email…";
  try {
    let r;
    try { r = await call("/api/edu/gmail/scan", { method: "POST", body: JSON.stringify({ access_token: await gmailToken(), days: 14 }) }); }
    catch (e) {
      if (!/expired|denied|Gmail access/i.test(e.message)) throw e;
      r = await call("/api/edu/gmail/scan", { method: "POST", body: JSON.stringify({ access_token: await gmailToken(true), days: 14 }) });
    }
    refresh(r);
    ctx.toast(r.new_items ? `Found ${r.new_items} thing${r.new_items === 1 ? "" : "s"} in ${r.read} new emails` : `Read ${r.read} new emails. Nothing to act on.`);
  } catch (e) {
    ctx.toast(/popup-closed|cancelled/.test(e.code || e.message) ? "Gmail connection cancelled" : e.message);
    b.disabled = false; b.textContent = label;
  }
}

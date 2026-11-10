// Developer dashboard (#/admin): Overview (usage), Users (who signed up and what they picked), Tasks (the developer's
// idea / task list) and Access (open sign-up, approvals). Counts and account rows only, never anyone's section data.
// Data from backend/access.py (/api/admin/users, /access, /signup) and backend/devtools.py (/api/admin/usage, /tasks).
let ctx = null;
export function initAdmin(c) { ctx = c; }

const ROLE = { owner: "Owner", admin: "Developer", member: "Member", pending: "Waiting" };
const SERVICE = { finances: "Finances", health: "Health", education: "Education", goals: "Goals", sports: "Sports",
  travel: "Travel", social: "Social", golf: "Golf", movies: "Movies", recipes: "Recipes", chess: "Chess" };
const CORE = ["finances", "health", "education", "goals", "sports", "travel", "social"];
const SECTION = { ...SERVICE, home: "Home", settings: "Settings", intro: "Service picker" };
const KIND = { idea: "Idea", task: "Task", bug: "Bug" };
const STATUS = { doing: "In progress", todo: "To do", idea: "Ideas", done: "Done" };
const STATUS_ONE = { doing: "In progress", todo: "To do", idea: "Idea", done: "Done" };
const PRIORITY = { high: "High", medium: "Medium", low: "Low" };
const AREA = { general: "General", developer: "Developer", design: "Design", backend: "Backend", ...SERVICE };

const when = (t) => (t ? new Date(t * 1000).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—");
const day = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
const n0 = (v) => (v || 0).toLocaleString("en-US");
const dur = (m) => (!m ? "0 min" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60 ? `${m % 60} min` : ""}`.trim());
const hm = (m) => (m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60 ? `${m % 60}m` : ""}`.trim());
const tok = (v) => (v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e4 ? `${Math.round(v / 1e3)}k` : n0(v));

let users = null, usage = null, tasks = null, chart = null;
let taskFilter = "open", editing = null;
const DAYS = 30;

export async function loadAdmin(tab = "overview") {
  const { $, esc } = ctx, root = $("adminRoot");
  if (!root.firstChild) root.innerHTML = `<p class="hint dev-loading">Loading…</p>`;
  try {
    if (tab === "overview") [users, usage] = await Promise.all([fetchUsers(), ctx.api(`/api/admin/usage?days=${DAYS}`)]);
    else if (tab === "users" || tab === "access") users = await fetchUsers();
    else if (tab === "tasks") tasks = await ctx.api("/api/admin/tasks");
  } catch (e) {
    root.innerHTML = `<div class="card empty-state"><h3>Developer</h3><p>${esc(e.message)}</p></div>`;
    return;
  }
  ({ overview: drawOverview, users: drawUsers, tasks: drawTasks, access: drawAccess })[tab]?.();
}
const fetchUsers = () => ctx.api("/api/admin/users");

const head = (h, p) => `<div class="admin-head"><h2 class="title">${h}</h2>${p ? `<p class="lede-sm">${p}</p>` : ""}</div>`;
const kpi = (label, value, sub) => `<div class="kpi"><span class="kpi-label">${label}</span><span class="kpi-value">${value}</span>${sub ? `<span class="kpi-sub">${sub}</span>` : ""}</div>`;
const bars = (rows, fmt) => {
  const max = Math.max(1, ...rows.map((r) => r.v));
  return rows.map((r) => `<div class="bar-row"><span class="bar-label">${ctx.esc(r.label)}</span><span class="bar-track"><span class="bar-fill" style="display:block;width:${Math.max(1.5, (r.v / max) * 100)}%"></span></span><span class="bar-value">${fmt(r.v)}</span></div>`).join("");
};
// Who uses what: the owner with no saved choice has every built section.
const picks = (u) => (u.services?.everything ? CORE : u.services?.picked || []);

// ---------------------------------------------------------------- Overview
function drawOverview() {
  const { $, esc, cssVar } = ctx, t = usage.totals, people = users.users.filter((u) => u.role !== "admin");
  const signed = people.filter((u) => u.first_seen), onboarded = people.filter((u) => u.services?.onboarded);
  const counts = Object.keys(SERVICE).map((k) => ({ label: SERVICE[k], v: onboarded.filter((u) => picks(u).includes(k)).length }))
    .filter((r) => r.v).sort((a, b) => b.v - a.v);
  const sections = usage.sections.map((s) => ({ label: SECTION[s.id] || s.id, v: s.minutes }));
  const since = usage.tracking_since ? `Counting since ${day(usage.tracking_since)}.` : "Counting starts now: nothing before this update was recorded.";
  const newWeek = signed.filter((u) => u.first_seen > Date.now() / 1000 - 7 * 86400).length;
  $("adminRoot").innerHTML = `${head("Overview", `How LifeTracker is used, in counts only. ${since} The developer account isn't counted.`)}
    <div class="kpis">${kpi("Accounts", n0(signed.length), newWeek ? `${newWeek} new this week` : "None new this week")}
      ${kpi("Active today", n0(t.active_today))}${kpi("Active, 7 days", n0(t.active_7))}${kpi(`Active, ${DAYS} days`, n0(t.active_window))}</div>
    <div class="kpis">${kpi(`Time on app, ${DAYS} days`, dur(t.minutes), t.active_window ? `${dur(Math.round(t.minutes / t.active_window))} per active person` : "")}
      ${kpi(`Sessions, ${DAYS} days`, n0(t.sessions))}${kpi(`Claude calls, ${DAYS} days`, n0(t.claude_calls))}
      ${kpi("Claude tokens", tok(t.claude_input + t.claude_output), `${tok(t.claude_input)} in, ${tok(t.claude_output)} out`)}</div>
    <div class="card dev-chart-card"><div class="card-head"><h3>Every day</h3><span class="card-meta dev-legend"><span class="dev-key dev-key-bar"></span>Minutes <span class="dev-key dev-key-line"></span>People</span></div>
      <div class="dev-chart"><canvas id="devChart"></canvas></div></div>
    <div class="grid-2">
      <div class="card"><div class="card-head"><h3>Services picked</h3><span class="card-meta">${onboarded.length} ${onboarded.length === 1 ? "account" : "accounts"}</span></div>
        ${counts.length ? bars(counts, n0) : `<p class="hint">No one has picked services yet.</p>`}
        ${people.length - onboarded.length ? `<p class="hint">${people.length - onboarded.length} haven't finished the service picker.</p>` : ""}</div>
      <div class="card"><div class="card-head"><h3>Time by section</h3><span class="card-meta">${DAYS} days</span></div>
        ${sections.length ? bars(sections, hm) : `<p class="hint">No time counted yet.</p>`}</div>
    </div>
    <div class="card card-flush dev-claude"><div class="card-head padded"><h3>Claude usage</h3><span class="card-meta">${DAYS} days</span></div>
      ${usage.claude.length ? `<div class="table-scroll"><table class="table"><thead><tr><th>Feature</th><th class="num">Calls</th><th class="num">Input tokens</th><th class="num">Output tokens</th></tr></thead><tbody>
        ${usage.claude.map((c) => `<tr><td>${esc(c.feature)}</td><td class="num">${n0(c.calls)}</td><td class="num">${n0(c.input)}</td><td class="num">${n0(c.output)}</td></tr>`).join("")}</tbody></table></div>` : `<p class="hint padded-x">No Claude calls counted yet.</p>`}
      <p class="hint padded-x">${usage.models.length ? `Models: ${usage.models.map((m) => `${esc(m.model)} (${n0(m.calls)})`).join(", ")}. ` : ""}Calls and tokens are the API's own counts. Cost and billing are in the Anthropic Console.</p></div>`;
  chart?.destroy(); chart = null;
  if (typeof Chart === "undefined") return;               // chart library blocked or offline: the numbers above still show
  const s = usage.series, accent = cssVar("--accent"), text = cssVar("--text-3"), line = cssVar("--line"), ink = cssVar("--text");
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  chart = new Chart($("devChart"), {
    data: { labels: s.map((r) => day(r.date)), datasets: [
      { type: "bar", label: "Minutes", data: s.map((r) => r.minutes), backgroundColor: accent, borderRadius: 3, yAxisID: "y", maxBarThickness: 18 },
      { type: "line", label: "People", data: s.map((r) => r.active), borderColor: ink, backgroundColor: ink, borderWidth: 2, pointRadius: 0, cubicInterpolationMode: "monotone", yAxisID: "y1" }] },
    options: { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, plugins: { legend: { display: false } },
      scales: { x: { grid: { display: false }, ticks: { color: text, maxTicksLimit: 8, maxRotation: 0 } },
        y: { beginAtZero: true, grid: { color: line }, ticks: { color: text, precision: 0 }, title: { display: true, text: "Minutes", color: text } },
        y1: { beginAtZero: true, position: "right", grid: { display: false }, ticks: { color: text, precision: 0 }, title: { display: true, text: "People", color: text } } } },
  });
}

// ---------------------------------------------------------------- Users
function drawUsers() {
  const { $, esc } = ctx, d = users;
  const services = (u) => {
    if (!u.services) return `<span class="muted">Developer tools</span>`;
    if (u.services.everything) return `<span class="muted">Everything</span>`;
    if (!u.services.onboarded) return `<span class="muted">Not picked yet</span>`;
    return `<span class="dev-svcs">${picks(u).map((k) => `<span class="pill pill-plain">${esc(SERVICE[k] || k)}</span>`).join("")}</span>`;
  };
  $("adminRoot").innerHTML = `${head("Users", "Everyone who has signed in with Google: what they picked and how much they use the app. Counts only; no one's saved data is shown here.")}
    <div class="kpis">${["member", "pending", "admin", "owner"].map((k) => kpi(ROLE[k], n0(d.counts[k]))).join("")}</div>
    <div class="card card-flush"><div class="table-scroll"><table class="table admin-table dev-users"><thead><tr><th>Account</th><th>Access</th><th>Services</th><th class="num">Time on app</th><th class="num">Sessions</th><th class="num">Claude</th><th>First sign-in</th><th>Last seen</th></tr></thead><tbody>
      ${d.users.map((u) => `<tr><td><strong>${esc(u.name || u.email)}</strong>${u.name ? `<small>${esc(u.email)}</small>` : ""}</td>
        <td><span class="pill pill-${u.role === "pending" ? "missing" : u.role === "member" ? "on_track" : "met"}">${ROLE[u.role]}</span></td>
        <td>${services(u)}</td><td class="num" data-label="Time on app">${u.role === "admin" ? "—" : dur(u.minutes)}</td><td class="num" data-label="Sessions">${u.role === "admin" ? "—" : n0(u.sessions)}</td>
        <td class="num" data-label="Claude calls">${u.role === "admin" ? "—" : n0(u.claude_calls)}</td><td data-label="First sign-in">${when(u.first_seen)}</td><td data-label="Last seen">${when(u.last_seen)}</td></tr>`).join("") || `<tr><td colspan="8" class="empty">No one has signed in yet</td></tr>`}
    </tbody></table></div></div>
    <p class="hint">Time counts a minute for each minute a tab is open on screen and in use. Counting started with this update.</p>`;
}

// ---------------------------------------------------------------- Tasks
function drawTasks() {
  const { $, esc } = ctx, all = tasks.tasks;
  const n = (s) => all.filter((t) => t.status === s).length;
  const shown = all.filter((t) => (taskFilter === "open" ? t.status !== "done" : taskFilter === "all" || t.status === taskFilter));
  const filters = [["open", "Open", all.length - n("done")], ["doing", "In progress", n("doing")], ["todo", "To do", n("todo")], ["idea", "Ideas", n("idea")], ["done", "Done", n("done")], ["all", "All", all.length]];
  const opts = (map, v) => Object.entries(map).map(([k, l]) => `<option value="${k}" ${k === v ? "selected" : ""}>${l}</option>`).join("");
  const row = (t) => editing === t.id ? `<form class="dev-task dev-task-edit" data-id="${t.id}">
      <input class="input" name="title" value="${esc(t.title)}" maxlength="200" required aria-label="Title" />
      <textarea class="input" name="notes" rows="3" maxlength="4000" placeholder="Notes" aria-label="Notes">${esc(t.notes || "")}</textarea>
      <div class="dev-task-fields"><select class="select" name="status" aria-label="Status">${opts(STATUS_ONE, t.status)}</select>
        <select class="select" name="kind" aria-label="Kind">${opts(KIND, t.kind)}</select>
        <select class="select" name="area" aria-label="Area">${opts(AREA, t.area)}</select>
        <select class="select" name="priority" aria-label="Priority">${opts(PRIORITY, t.priority)}</select></div>
      <div class="dev-task-actions"><button class="text-btn danger" type="button" data-del="${t.id}">Delete</button><span></span>
        <button class="btn btn-secondary btn-sm" type="button" data-cancel>Cancel</button><button class="btn btn-primary btn-sm">Save</button></div></form>`
    : `<div class="dev-task ${t.status === "done" ? "is-done" : ""}">
      <button class="dev-check" data-toggle="${t.id}" aria-label="${t.status === "done" ? "Mark not done" : "Mark done"}" aria-pressed="${t.status === "done"}"></button>
      <div class="dev-task-main"><span class="dev-task-title">${esc(t.title)}</span>${t.notes ? `<span class="dev-task-notes">${esc(t.notes)}</span>` : ""}
        <span class="dev-task-meta"><span class="pill ${t.kind === "bug" ? "pill-missing" : "pill-plain"}">${KIND[t.kind] || t.kind}</span><span class="pill pill-plain">${esc(AREA[t.area] || t.area)}</span>${t.priority !== "medium" ? `<span class="pill ${t.priority === "high" ? "pill-warn" : "pill-plain"}">${PRIORITY[t.priority]} priority</span>` : ""}</span></div>
      <span class="dev-task-ctl"><select class="select dev-status" data-status="${t.id}" aria-label="Status">${opts(STATUS_ONE, t.status)}</select>
      <button class="text-btn" data-edit="${t.id}">Edit</button></span></div>`;
  const groups = ["doing", "todo", "idea", "done"].map((s) => [s, shown.filter((t) => t.status === s)]).filter(([, l]) => l.length);
  $("adminRoot").innerHTML = `${head("Tasks", "Ideas and work to build, for you only. Move each one along as it ships.")}
    <form class="card dev-add" id="devAdd">
      <input class="input" name="title" placeholder="Add an idea or task" maxlength="200" required aria-label="New idea or task" />
      <div class="dev-add-fields"><select class="select" name="kind" aria-label="Kind">${opts(KIND, "idea")}</select>
        <select class="select" name="area" aria-label="Area">${opts(AREA, "general")}</select>
        <select class="select" name="priority" aria-label="Priority">${opts(PRIORITY, "medium")}</select>
        <button class="btn btn-primary btn-sm">Add</button></div></form>
    <div class="segmented dev-filter">${filters.map(([k, l, c]) => `<a href="#" data-filter="${k}" class="${k === taskFilter ? "active" : ""}">${l} <span class="muted">${c}</span></a>`).join("")}</div>
    ${groups.map(([s, list]) => `<div class="dev-group"><h3 class="dev-group-h">${STATUS[s]} <span class="muted">${list.length}</span></h3><div class="card card-flush dev-list">${list.map(row).join("")}</div></div>`).join("")
      || `<div class="card empty-state"><h3>${all.length ? "Nothing here" : "Nothing on the list yet"}</h3><p>${all.length ? "Pick another filter to see the rest." : "Add an idea above, or start with the add-ons that are already marked in development in the app."}</p>
        ${all.length ? "" : `<div class="empty-actions"><button class="btn btn-secondary btn-sm" id="devStarters">Add the in-development add-ons</button></div>`}</div>`}`;
  const save = async (path, method, body, msg) => {
    try { const r = await ctx.api(path, { method, body: body && JSON.stringify(body) }); tasks.tasks = r.tasks; if (msg) ctx.toast(msg); drawTasks(); }
    catch (e) { ctx.toast(e.message); }
  };
  const root = $("adminRoot");
  $("devAdd").onsubmit = (e) => {
    e.preventDefault();
    const f = e.target.elements;
    save("/api/admin/tasks", "POST", { title: f.title.value, kind: f.kind.value, area: f.area.value, priority: f.priority.value }, "Added");
  };
  root.querySelectorAll("[data-filter]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); taskFilter = a.dataset.filter; drawTasks(); }));
  root.querySelectorAll("[data-toggle]").forEach((b) => (b.onclick = () => {
    const t = all.find((x) => x.id === b.dataset.toggle);
    save(`/api/admin/tasks/${t.id}`, "PUT", { status: t.status === "done" ? (t.kind === "idea" ? "idea" : "todo") : "done" });
  }));
  root.querySelectorAll("[data-status]").forEach((s) => (s.onchange = () => save(`/api/admin/tasks/${s.dataset.status}`, "PUT", { status: s.value })));
  root.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => { editing = b.dataset.edit; drawTasks(); root.querySelector(".dev-task-edit input")?.focus(); }));
  root.querySelectorAll("[data-cancel]").forEach((b) => (b.onclick = () => { editing = null; drawTasks(); }));
  root.querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => {
    if (confirm("Delete this from the list?")) { editing = null; save(`/api/admin/tasks/${b.dataset.del}`, "DELETE", null, "Deleted"); }
  }));
  root.querySelectorAll(".dev-task-edit").forEach((f) => (f.onsubmit = (e) => {
    e.preventDefault();
    const el = f.elements;
    editing = null;
    save(`/api/admin/tasks/${f.dataset.id}`, "PUT", { title: el.title.value, notes: el.notes.value, status: el.status.value, kind: el.kind.value, area: el.area.value, priority: el.priority.value }, "Saved");
  }));
  if ($("devStarters")) $("devStarters").onclick = () => save("/api/admin/tasks/starters", "POST", null, "Added");
}

// ---------------------------------------------------------------- Access
function drawAccess() {
  const { $, esc, api, toast } = ctx;
  const d = users, waiting = d.users.filter((u) => u.role === "pending"), approved = d.users.filter((u) => u.role === "member");
  $("adminRoot").innerHTML = `${head("Access", `${d.signup_open ? "Sign-up is open, so new accounts get in right away" : "New accounts wait here until you approve them"}. Finances stay with the owner account.`)}
    <div class="card admin-open"><div><strong>Open sign-up</strong><p class="hint">${d.signup_open ? "Anyone who signs in with Google gets in right away, with their own empty data and no Finances." : "New accounts wait for your approval."}${d.signup_env ? " Set on the server (SIGNUP_OPEN), so this switch can't change it." : ""}</p></div>
      <label class="switch"><input type="checkbox" id="signupOpen" ${d.signup_open ? "checked" : ""} ${d.signup_env ? "disabled" : ""} /> ${d.signup_open ? "On" : "Off"}</label></div>
    <form class="card admin-add" id="adminAdd"><label>Approve an email before they sign in<input class="input" name="email" type="email" placeholder="name@example.com" required /></label><button class="btn btn-primary btn-sm">Approve</button></form>
    <div class="card card-flush"><div class="card-head padded"><h3>Waiting</h3><span class="card-meta">${waiting.length}</span></div>
      ${waiting.map((u) => `<div class="dev-access-row"><span><strong>${esc(u.name || u.email)}</strong>${u.name ? `<small>${esc(u.email)}</small>` : ""}</span><span class="muted">${when(u.first_seen)}</span><button class="btn btn-primary btn-sm" data-approve="${esc(u.email)}">Approve</button></div>`).join("") || `<p class="hint padded-x">No one is waiting.</p>`}</div>
    ${!d.signup_open && approved.length ? `<div class="card card-flush"><div class="card-head padded"><h3>Approved</h3><span class="card-meta">${approved.length}</span></div>
      ${approved.map((u) => `<div class="dev-access-row"><span><strong>${esc(u.name || u.email)}</strong>${u.name ? `<small>${esc(u.email)}</small>` : ""}</span><span class="muted">${when(u.last_seen)}</span><button class="text-btn danger" data-remove="${esc(u.email)}">Remove access</button></div>`).join("")}</div>` : ""}`;
  const set = async (email, approve) => {
    try { users = await api("/api/admin/access", { method: "POST", body: JSON.stringify({ email, approve }) }); toast(approve ? `${email} approved` : `${email} removed`); drawAccess(); }
    catch (e) { toast(e.message); }
  };
  const root = $("adminRoot");
  root.querySelectorAll("[data-approve]").forEach((b) => (b.onclick = () => set(b.dataset.approve, true)));
  root.querySelectorAll("[data-remove]").forEach((b) => (b.onclick = () => confirm(`Remove ${b.dataset.remove}'s access? Their data stays, but they can't sign in.`) && set(b.dataset.remove, false)));
  $("signupOpen").onchange = async (e) => {
    try { users = await api("/api/admin/signup", { method: "POST", body: JSON.stringify({ open: e.target.checked }) }); toast(users.signup_open ? "Sign-up is open" : "New accounts need approval"); drawAccess(); }
    catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
  };
  $("adminAdd").onsubmit = (e) => { e.preventDefault(); set(e.target.elements.email.value.trim(), true); };
}

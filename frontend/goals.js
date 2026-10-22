// Goals: one widget per goal, a detail view with the SMART breakdown and step plan, an editor,
// the 100-goal library, and a coach (Claude) that proposes SMART goals the user saves.
// app.js passes its helpers in with initGoals() and calls loadGoals() on #/goals.

let H;                       // { api, $, esc, toast, renderMarkdown, usd0, showError }
let state = { goals: [], counts: {}, areas: {} };
let filter = { status: "active", area: "all" };
let library = null;
let metricsCache = null;
let openId = null;
const coach = { goalId: null, hist: [] };

const PACE = {
  on_track: ["On track", "pill-on_track"], behind: ["Behind", "pill-warn"], overdue: ["Overdue", "pill-missing"],
  reached: ["Target reached", "pill-met"], done: ["Done", "pill-met"], someday: ["Someday", "pill-plain"],
  no_pace: ["In progress", "pill-plain"], no_data: ["No data yet", "pill-plain"],
};
const STEP_KIND = { steps: "Step plan", number: "Number I update", linked: "Linked to my data" };

export function initGoals(helpers) { H = helpers; }

// ---------- formatting
const yr = new Date().getFullYear();
function fd(d, withYear) {
  if (!d) return "";
  const x = new Date(d.slice(0, 10) + "T00:00");
  return x.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(withYear || x.getFullYear() !== yr ? { year: "numeric" } : {}) });
}
function fnum(v, unit) {
  if (v == null) return "—";
  if (unit === "$") return H.usd0.format(v);
  const n = Math.abs(v) >= 100 ? Math.round(v).toLocaleString("en-US") : (+v.toFixed(2)).toLocaleString("en-US");
  return n;
}
const unitTxt = (u) => (u && u !== "$" ? ` ${u}` : "");
function left(days) {
  if (days == null) return "";
  if (days < 0) return `${-days} day${days === -1 ? "" : "s"} past deadline`;
  if (days < 14) return `${days} day${days === 1 ? "" : "s"} left`;
  if (days < 75) return `${Math.round(days / 7)} weeks left`;
  return `${Math.round(days / 30.4)} months left`;
}
const pill = (pace) => { const [t, c] = PACE[pace] || PACE.no_data; return `<span class="pill ${c}">${t}</span>`; };
const areaName = (a) => state.areas[a] || a;

// ---------- load + page
export async function loadGoals() {
  const root = H.$("goalsRoot");
  if (!root.innerHTML) root.innerHTML = `<p class="empty">Loading goals…</p>`;
  state = await H.api("/api/goals");
  renderPage();
}

function setState(r) { if (r && r.goals) state = r; renderPage(); if (openId) renderDetail(); }

function renderPage() {
  const root = H.$("goalsRoot");
  const c = state.counts || {};
  const active = state.goals.filter((g) => g.status === "active");
  const onTrack = active.filter((g) => ["on_track", "reached"].includes(g.eval.pace)).length;
  const sub = state.goals.length
    ? [`${onTrack} on track`, c.behind ? `${c.behind} behind` : "", c.done ? `${c.done} done` : "", c.someday ? `${c.someday} someday` : ""].filter(Boolean).join(" · ")
    : "Pick from the library, write your own, or work one out with the coach.";
  const shown = state.goals.filter((g) => (filter.status === "all" || g.status === filter.status) && (filter.area === "all" || g.area === filter.area));
  const seg = (k, v, label) => `<button class="seg ${filter[k] === v ? "seg-on" : ""}" data-f="${k}" data-v="${v}">${label}</button>`;
  root.innerHTML = `
    <div class="goals-head">
      <div>
        <p class="eyebrow">Goals</p>
        <h1 class="goals-title">${active.length ? `${active.length} active goal${active.length === 1 ? "" : "s"}` : "Set a goal"}</h1>
        <p class="lede-sm">${H.esc(sub)}</p>
      </div>
      <div class="goals-actions">
        <label class="btn btn-secondary btn-sm">Import file<input type="file" id="gImport" accept=".json" hidden /></label>
        <button class="btn btn-secondary btn-sm" id="gLibrary">Library</button>
        <button class="btn btn-secondary btn-sm" id="gCoach">Plan with coach</button>
        <button class="btn btn-primary btn-sm" id="gNew">New goal</button>
      </div>
    </div>
    ${state.goals.length ? `<div class="goals-filter">
      <div class="seg-row">${seg("status", "active", `Active${c.active ? ` ${c.active}` : ""}`)}${seg("status", "someday", "Someday")}${seg("status", "done", "Done")}${seg("status", "all", "All")}</div>
      <div class="seg-row">${seg("area", "all", "All areas")}${Object.entries(state.areas).map(([k, v]) => seg("area", k, v)).join("")}</div>
    </div>` : ""}
    ${shown.length ? `<div class="goal-grid">${shown.map(widget).join("")}</div>`
      : state.goals.length ? `<p class="empty">Nothing here with these filters.</p>`
      : `<div class="card empty-state"><h3>No goals yet</h3><p>Browse 100 goals and challenges, or tell the coach what you want to do and it will help you make it specific, measurable and dated.</p>
         <div class="empty-actions"><button class="btn btn-primary" data-act="library">Browse the library</button><button class="btn btn-secondary" data-act="coach">Plan with coach</button></div></div>`}`;
  root.querySelectorAll("[data-f]").forEach((b) => (b.onclick = () => { filter[b.dataset.f] = b.dataset.v; renderPage(); }));
  root.querySelectorAll(".goal-card").forEach((el) => (el.onclick = () => openDetail(el.dataset.id)));
  H.$("gNew").onclick = () => openEditor(null);
  H.$("gLibrary").onclick = openLibrary;
  H.$("gImport").onchange = importFile;
  H.$("gCoach").onclick = () => openCoach(null);
  root.querySelector("[data-act=library]")?.addEventListener("click", openLibrary);
  root.querySelector("[data-act=coach]")?.addEventListener("click", () => openCoach(null));
}

async function importFile(ev) {
  const file = ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  try {
    const body = JSON.parse(await file.text());
    const r = await H.api("/api/goals/import", { method: "POST", body: JSON.stringify(body) });
    setState(r);
    H.toast(`Imported: ${r.added} new, ${r.updated} updated`);
  } catch (e) { H.showError(e instanceof SyntaxError ? new Error("That file isn't valid JSON.") : e); }
}

function figure(g) {
  const e = g.eval, m = g.measure || {};
  if (m.kind === "steps") {
    return e.steps_total ? `${e.steps_done}<small> of ${e.steps_total} steps</small>` : `<span class="muted">—</span><small> no steps yet</small>`;
  }
  const cur = e.current ? fnum(e.current.value, m.unit) : "—";
  const tgt = m.target != null ? ` / ${fnum(m.target, m.unit)}${unitTxt(m.unit)}` : m.unit && m.unit !== "$" ? ` ${m.unit}` : "";
  return `${cur}<small>${H.esc(tgt)}</small>`;
}

function bar(g) {
  const e = g.eval;
  if (e.progress == null) return `<div class="goal-bar empty-bar"></div>`;
  const mark = e.expected != null && !["done", "someday"].includes(e.pace) ? `<span class="goal-mark" style="left:${(e.expected * 100).toFixed(1)}%" title="Where you'd be on a straight line to the deadline"></span>` : "";
  return `<div class="goal-bar"><div class="goal-fill pace-${e.pace}" style="width:${(e.progress * 100).toFixed(1)}%"></div>${mark}</div>`;
}

function widget(g) {
  const e = g.eval;
  const meta = g.status === "done" ? `Done${g.done_on ? ` ${fd(g.done_on, true)}` : ""}`
    : e.next_step ? `Next: ${e.next_step.title}${e.next_step.due ? ` · ${fd(e.next_step.due)}` : ""}`
    : e.metric_label ? e.metric_label : "";
  const due = g.status === "active" && g.due_date ? `By ${fd(g.due_date, true)}` : "";
  return `<button class="card goal-card ${g.status !== "active" ? "goal-quiet" : ""}" data-id="${g.id}">
    <span class="goal-top"><span class="goal-area">${H.esc(areaName(g.area))}${g.category ? ` · ${H.esc(g.category)}` : ""}</span>${pill(e.pace)}</span>
    <span class="goal-name">${H.esc(g.title)}</span>
    <span class="goal-figure">${figure(g)}</span>
    ${bar(g)}
    <span class="goal-meta"><span class="goal-next">${H.esc(meta)}</span><span>${H.esc(due || left(g.status === "active" ? e.days_left : null))}</span></span>
  </button>`;
}

// ---------- dialogs (created once)
function dialog(id, cls = "dialog dialog-wide") {
  let d = document.getElementById(id);
  if (!d) {
    d = document.createElement("dialog");
    d.id = id; d.className = cls;
    d.addEventListener("click", (ev) => { if (ev.target === d) d.close(); });
    document.body.appendChild(d);
  }
  return d;
}

// ---------- detail
function openDetail(id) { openId = id; renderDetail(); const d = dialog("goalDetail"); if (!d.open) d.showModal(); }

function renderDetail() {
  const g = state.goals.find((x) => x.id === openId);
  const d = dialog("goalDetail");
  d.onclose = () => { openId = null; };
  if (!g) { d.close(); return; }
  const e = g.eval, m = g.measure || {};
  const smart = [["specific", "Specific"], ["measurable", "Measurable"], ["achievable", "Achievable"], ["relevant", "Relevant"], ["time_bound", "Time-bound"]]
    .filter(([k]) => g.smart?.[k]).map(([k, l]) => `<div class="smart-row"><dt>${l}</dt><dd>${H.esc(g.smart[k])}</dd></div>`).join("");
  let source = "";
  if (m.kind === "number") {
    const bits = [];
    if (e.current) bits.push(`${e.current.source}${e.current.as_of ? `, ${fd(e.current.as_of, true)}` : ""}`);
    else if (m.metric) bits.push(`${e.metric_label}: no data in LifeTracker yet`);
    if (m.start != null) bits.push(`start ${fnum(m.start, m.unit)}${unitTxt(m.unit)}${m.start_note ? ` (${m.start_note.toLowerCase()})` : ""}`);
    if (m.target == null) bits.push("no target set");
    source = bits.join(" · ");
  }
  const today = state.today;
  const steps = (g.steps || []).map((s, i) => `<li class="step ${s.done ? "step-done" : ""} ${!s.done && s.due && s.due < today ? "step-late" : ""}">
      <label><input type="checkbox" data-step="${i}" ${s.done ? "checked" : ""} /><span>${H.esc(s.title)}</span></label>
      <span class="step-due">${s.done && s.done_on ? `Done ${fd(s.done_on)}` : s.due ? fd(s.due) : ""}</span></li>`).join("");
  const manual = m.kind === "number" && !m.metric && g.status !== "done";
  const log = (g.log || []).slice(-6).reverse().map((x) => `<li><span>${fd(x.date, true)}</span><span>${fnum(x.value, m.unit)}${unitTxt(m.unit)}${x.note ? ` · ${H.esc(x.note)}` : ""}</span></li>`).join("");
  d.innerHTML = `<div class="goal-detail">
    <div class="gd-head">
      <div><p class="eyebrow">${H.esc(areaName(g.area))}${g.category ? ` · ${H.esc(g.category)}` : ""}</p><h3>${H.esc(g.title)}</h3></div>
      <button class="text-btn" data-x="close">Close</button>
    </div>
    <div class="gd-progress">
      <div class="gd-figure">${figure(g)} ${pill(e.pace)}</div>
      ${bar(g)}
      <p class="gd-source">${H.esc(source)}${g.due_date ? `${source ? " · " : ""}deadline ${fd(g.due_date, true)}${g.status === "active" && e.days_left != null ? ` (${left(e.days_left)})` : ""}` : ""}</p>
      ${manual ? `<form class="gd-log" id="gdLog"><input class="input" name="value" type="number" step="any" placeholder="New value${unitTxt(m.unit)}" required />
        <input class="input" name="date" type="date" value="${today}" /><button class="btn btn-secondary btn-sm">Update</button></form>` : ""}
    </div>
    ${g.why ? `<p class="gd-why">${H.esc(g.why)}</p>` : ""}
    ${smart ? `<dl class="smart">${smart}</dl>` : ""}
    <div class="gd-section"><h4>Plan</h4>${steps ? `<ol class="steps">${steps}</ol>` : `<p class="empty small">No steps yet. Ask the coach to break this into weekly and monthly steps.</p>`}</div>
    ${log ? `<div class="gd-section"><h4>Updates</h4><ul class="gd-loglist">${log}</ul></div>` : ""}
    <div class="dialog-actions gd-actions">
      <button class="text-btn danger" data-x="delete">Delete</button><span style="flex:1"></span>
      ${g.status === "active" ? `<button class="btn btn-secondary btn-sm" data-x="someday">Someday</button><button class="btn btn-secondary btn-sm" data-x="done">Mark done</button>`
        : `<button class="btn btn-secondary btn-sm" data-x="active">Make active</button>`}
      <button class="btn btn-secondary btn-sm" data-x="coach">Coach</button>
      <button class="btn btn-primary btn-sm" data-x="edit">Edit</button>
    </div></div>`;
  const put = (body) => H.api(`/api/goals/${g.id}`, { method: "PUT", body: JSON.stringify(body) }).then(setState).catch(H.showError);
  d.querySelectorAll("[data-step]").forEach((cb) => (cb.onchange = () => {
    const steps = g.steps.map((s, i) => (i === +cb.dataset.step ? { ...s, done: cb.checked, done_on: cb.checked ? today : null } : s));
    put({ steps });
  }));
  d.querySelector("#gdLog")?.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    H.api(`/api/goals/${g.id}/log`, { method: "POST", body: JSON.stringify({ value: f.get("value"), date: f.get("date") }) }).then(setState).catch(H.showError);
  });
  d.querySelectorAll("[data-x]").forEach((b) => (b.onclick = () => {
    const x = b.dataset.x;
    if (x === "close") d.close();
    else if (x === "edit") { d.close(); openEditor(g); }
    else if (x === "coach") { d.close(); openCoach(g.id); }
    else if (x === "delete") {
      if (confirm(`Delete "${g.title}"?`)) H.api(`/api/goals/${g.id}`, { method: "DELETE" }).then((r) => { d.close(); setState(r); }).catch(H.showError);
    } else put({ status: x, ...(x === "done" ? { done_on: today } : {}) }).then(() => x !== "active" && H.toast(x === "done" ? "Marked done" : "Moved to Someday"));
  }));
}

// ---------- editor
async function metrics() {
  if (!metricsCache) metricsCache = await H.api("/api/goals/metrics");
  return metricsCache;
}

async function openEditor(g, preset) {
  const src = g || preset || {};
  const m = src.measure || {};
  const kind = m.kind === "number" ? (m.metric ? "linked" : "number") : "steps";
  const [mKey, mArg] = (m.metric || "").split(":");
  const d = dialog("goalEdit", "dialog dialog-wide");
  const areas = Object.entries(state.areas || {});
  const today = state.today || new Date().toISOString().slice(0, 10);
  const smartF = [["specific", "Specific: exactly what"], ["measurable", "Measurable: how you'll know"], ["achievable", "Achievable: why it's realistic"],
    ["relevant", "Relevant: why it matters now"], ["time_bound", "Time-bound: by when"]];
  d.innerHTML = `<form class="dialog-form" id="geForm" autocomplete="off">
    <h3>${g ? "Edit goal" : "New goal"}</h3>
    <label class="span-2">Goal<input class="input" name="title" required maxlength="140" value="${H.esc(src.title || "")}" placeholder="Run a sub-4-hour marathon" /></label>
    <label>Area<select class="input" name="area">${areas.map(([k, v]) => `<option value="${k}" ${src.area === k ? "selected" : ""}>${v}</option>`).join("")}</select></label>
    <label>Category<input class="input" name="category" maxlength="40" value="${H.esc(src.category || "")}" placeholder="Endurance" /></label>
    <label class="span-2">Why it matters<textarea class="input" name="why" rows="2" maxlength="600">${H.esc(src.why || "")}</textarea></label>
    <fieldset class="tags-field span-2 smart-field"><legend>SMART</legend>
      ${smartF.map(([k, l]) => `<label class="span-2">${l}<textarea class="input" name="smart_${k}" rows="1" maxlength="600">${H.esc(src.smart?.[k] || "")}</textarea></label>`).join("")}
    </fieldset>
    <label>Start date<input class="input" name="start_date" type="date" value="${src.start_date || today}" /></label>
    <label>Deadline<input class="input" name="due_date" type="date" value="${src.due_date || ""}" /></label>
    <label class="span-2">Track progress by<select class="input" name="kind">
      ${Object.entries(STEP_KIND).map(([k, v]) => `<option value="${k}" ${k === kind ? "selected" : ""}>${v}</option>`).join("")}</select></label>
    <label class="ge-linked span-2">Metric<select class="input" name="metric"><option value="">Loading…</option></select></label>
    <label class="ge-linked ge-ex span-2">Exercise<select class="input" name="exercise"></select></label>
    <p class="ge-linked ge-now span-2"></p>
    <label class="ge-num">Unit<input class="input" name="unit" maxlength="20" value="${H.esc(m.unit || "")}" placeholder="lb, mi, races" /></label>
    <label class="ge-num">Start value<input class="input" name="start" type="number" step="any" value="${m.start ?? ""}" placeholder="Leave blank to use today's" /></label>
    <label class="ge-num">Target<input class="input" name="target" type="number" step="any" value="${m.target ?? ""}" /></label>
    <label class="ge-manual">Current<input class="input" name="current" type="number" step="any" value="${m.current ?? ""}" /></label>
    <div class="span-2 ge-steps"><div class="ge-steps-head"><span>Plan</span><button type="button" class="text-btn accent" id="geAddStep">Add step</button></div><ol id="geSteps"></ol></div>
    <div class="dialog-actions span-2"><span style="flex:1"></span>
      <button type="button" class="btn btn-secondary" id="geCancel">Cancel</button>
      <button type="submit" class="btn btn-primary">Save</button></div>
  </form>`;
  const f = d.querySelector("form");
  let steps = (src.steps || []).map((s) => (typeof s === "string" ? { title: s } : { ...s }));
  const drawSteps = () => {
    d.querySelector("#geSteps").innerHTML = steps.map((s, i) => `<li class="ge-step">
      <input class="input" data-i="${i}" data-k="title" value="${H.esc(s.title || "")}" placeholder="Step" />
      <input class="input" data-i="${i}" data-k="due" type="date" value="${s.due || ""}" />
      <button type="button" class="icon-btn sm" data-rm="${i}" aria-label="Remove step">×</button></li>`).join("");
    d.querySelectorAll(".ge-step input").forEach((inp) => (inp.oninput = () => { steps[+inp.dataset.i][inp.dataset.k] = inp.value; }));
    d.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { steps.splice(+b.dataset.rm, 1); drawSteps(); }));
  };
  drawSteps();
  d.querySelector("#geAddStep").onclick = () => { steps.push({ title: "", due: "" }); drawSteps(); d.querySelector(`.ge-step:last-child input`).focus(); };
  const sync = () => {
    const k = f.kind.value;
    d.querySelectorAll(".ge-linked").forEach((x) => x.classList.toggle("hidden", k !== "linked"));
    d.querySelectorAll(".ge-num").forEach((x) => x.classList.toggle("hidden", k === "steps"));
    d.querySelectorAll(".ge-manual").forEach((x) => x.classList.toggle("hidden", k !== "number"));
    if (k === "linked") showNow();
  };
  let cat = null;
  const showNow = async () => {
    if (!cat) return;
    const def = cat.metrics.find((x) => x.key === f.metric.value);
    d.querySelector(".ge-ex").classList.toggle("hidden", !def?.takes_exercise);
    let now = def?.current;
    if (def?.takes_exercise && f.exercise.value) {
      const r = await H.api(`/api/goals/metrics?exercise=${encodeURIComponent(f.exercise.value)}`);
      now = r.lift?.[def.key];
    }
    if (def && !f.unit.value) f.unit.value = def.unit;
    d.querySelector(".ge-now").textContent = !def ? "" : def.since_start ? "Counts from the start date." :
      now ? `Today: ${fnum(now.value, def.unit)}${unitTxt(def.unit)} (${now.source})` : "No data for this in LifeTracker yet. Progress shows once there is.";
  };
  f.kind.onchange = sync;
  f.metric.onchange = () => { const def = cat?.metrics.find((x) => x.key === f.metric.value); if (def) f.unit.value = def.unit; showNow(); };
  f.exercise.onchange = showNow;
  sync();
  d.querySelector("#geCancel").onclick = () => d.close();
  f.onsubmit = async (ev) => {
    ev.preventDefault();
    const k = f.kind.value;
    const metric = k === "linked" ? (cat?.metrics.find((x) => x.key === f.metric.value)?.takes_exercise ? `${f.metric.value}:${f.exercise.value}` : f.metric.value) : null;
    const body = {
      title: f.title.value, area: f.area.value, category: f.category.value, why: f.why.value,
      smart: Object.fromEntries(smartF.map(([key]) => [key, f[`smart_${key}`].value])),
      start_date: f.start_date.value, due_date: f.due_date.value || null,
      library_id: src.library_id || null,
      measure: k === "steps" ? { kind: "steps" } : { kind: "number", metric, unit: f.unit.value, start: f.start.value, target: f.target.value, current: k === "number" ? f.current.value : null },
      steps: steps.filter((s) => (s.title || "").trim()),
    };
    if (g && g.status) body.status = g.status;
    try {
      const r = await H.api(g ? `/api/goals/${g.id}` : "/api/goals", { method: g ? "PUT" : "POST", body: JSON.stringify(body) });
      d.close(); setState(r); H.toast(g ? "Goal saved" : "Goal added");
    } catch (e) { H.showError(e); }
  };
  d.showModal();
  try {
    cat = await metrics();
    f.metric.innerHTML = `<option value="">Choose…</option>` + Object.entries(state.areas).map(([a, label]) => {
      const rows = cat.metrics.filter((x) => x.area === a);
      return rows.length ? `<optgroup label="${label}">${rows.map((x) => `<option value="${x.key}" ${x.key === mKey ? "selected" : ""}>${H.esc(x.label)}</option>`).join("")}</optgroup>` : "";
    }).join("");
    const exs = [...new Set([...(cat.exercises || []), ...(mArg ? [mArg] : [])])];
    f.exercise.innerHTML = exs.length ? exs.map((x) => `<option ${x.toLowerCase() === (mArg || "").toLowerCase() ? "selected" : ""}>${H.esc(x)}</option>`).join("")
      : `<option value="">No exercises logged yet</option>`;
    sync();
  } catch (e) { H.showError(e); }
}

// ---------- library
async function openLibrary() {
  const d = dialog("goalLibrary", "dialog dialog-wide");
  if (!library) library = await H.api("/api/goals/library").catch((e) => { H.showError(e); return null; });
  if (!library) return;
  let q = "", cat = "";
  const draw = () => {
    const adopted = new Set(state.goals.map((g) => g.library_id).filter(Boolean));
    const titles = new Set(state.goals.map((g) => g.title.toLowerCase()));
    const rows = library.library.filter((x) => (!cat || x.category === cat) && (!q || x.title.toLowerCase().includes(q)));
    const groups = library.categories.map((c) => [c, rows.filter((x) => x.category === c)]).filter(([, r]) => r.length);
    d.querySelector("#glList").innerHTML = groups.length ? groups.map(([c, r]) => `<h4 class="gl-cat">${c}</h4>${r.map((x) => {
      const has = adopted.has(x.id) || titles.has(x.title.toLowerCase());
      const meta = x.metric ? "Tracks itself" : x.kind === "number" && x.target != null ? `${x.target.toLocaleString("en-US")} ${x.unit}` : x.steps.length ? `${x.steps.length}-step plan` : "";
      return `<div class="gl-row"><span><span class="gl-title">${H.esc(x.title)}</span>${meta ? `<span class="gl-meta">${H.esc(meta)}</span>` : ""}</span>
        ${has ? `<span class="gl-added">Added</span>` : `<button class="btn btn-secondary btn-sm" data-add="${x.id}">Add</button>`}</div>`;
    }).join("")}`).join("") : `<p class="empty">No matches.</p>`;
    d.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => adopt(b.dataset.add)));
    d.querySelectorAll("[data-cat]").forEach((b) => b.classList.toggle("chip-on", b.dataset.cat === cat));
  };
  d.innerHTML = `<div class="goal-library">
    <div class="gd-head"><div><p class="eyebrow">${library.library.length} goals and challenges</p><h3>Goal library</h3></div>
      <span><button class="text-btn" id="glCsv">Download list</button><button class="text-btn" id="glClose">Close</button></span></div>
    <input class="input gl-search" id="glSearch" type="search" placeholder="Search" />
    <div class="gl-cats"><button class="chip" data-cat="">All</button>${library.categories.map((c) => `<button class="chip" data-cat="${c}">${c}</button>`).join("")}</div>
    <div id="glList" class="gl-list"></div></div>`;
  d.querySelector("#glClose").onclick = () => d.close();
  d.querySelector("#glCsv").onclick = downloadCsv;
  d.querySelector("#glSearch").oninput = (ev) => { q = ev.target.value.trim().toLowerCase(); draw(); };
  d.querySelectorAll("[data-cat]").forEach((b) => (b.onclick = () => { cat = b.dataset.cat; draw(); }));
  draw();
  d.showModal();
}

function adopt(id) {
  const x = library.library.find((y) => y.id === id);
  dialog("goalLibrary").close();
  openEditor(null, {
    title: x.title, area: x.area, category: x.category, library_id: x.id, steps: x.steps.map((t) => ({ title: t })),
    measure: x.kind === "steps" ? { kind: "steps" } : { kind: "number", metric: x.metric, unit: x.unit, target: x.target },
  });
}

function downloadCsv() {
  const q = (s) => `"${String(s ?? "").replaceAll('"', '""')}"`;
  const lines = [["Goal", "Category", "Area", "Tracking", "Target", "Suggested steps"].map(q).join(",")]
    .concat(library.library.map((x) => [x.title, x.category, library.areas[x.area], x.metric ? "Linked" : x.kind === "number" ? "Number" : "Steps",
      x.target != null ? `${x.target} ${x.unit}` : "", x.steps.join("; ")].map(q).join(",")));
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
  a.download = "lifetracker-goal-library.csv";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------- coach
const COACH_SUGGEST = ["Make one of my goals SMART", "Break a big goal into weekly steps", "Suggest my next goal from the library"];

function coachDrawer() {
  let el = document.getElementById("goalCoach");
  if (el) return el;
  el = document.createElement("aside");
  el.id = "goalCoach"; el.className = "drawer hidden"; el.setAttribute("aria-label", "Goals coach");
  el.innerHTML = `<div class="drawer-head"><div><p class="eyebrow" id="gcPlace">Goals coach</p><h3>Plan a goal</h3></div><button class="text-btn" id="gcClose">Close</button></div>
    <div class="chat-log" id="gcLog"></div><div class="chat-suggestions" id="gcSuggest"></div>
    <div class="chat-composer"><textarea id="gcInput" rows="1" placeholder="What do you want to achieve?"></textarea><button id="gcSend" class="btn btn-primary">Send</button></div>`;
  document.body.appendChild(el);
  el.querySelector("#gcClose").onclick = () => el.classList.add("hidden");
  const inp = el.querySelector("#gcInput");
  inp.addEventListener("input", () => { inp.style.height = "auto"; inp.style.height = Math.min(inp.scrollHeight, 160) + "px"; });
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendCoach(inp.value); } });
  el.querySelector("#gcSend").onclick = () => sendCoach(inp.value);
  window.addEventListener("hashchange", () => el.classList.add("hidden"));
  return el;
}

function openCoach(goalId) {
  const el = coachDrawer();
  if (coach.goalId !== goalId) { coach.goalId = goalId; coach.hist = []; }
  const g = state.goals.find((x) => x.id === goalId);
  el.querySelector("#gcPlace").textContent = g ? `Coach · ${g.title}` : "Goals coach";
  const log = el.querySelector("#gcLog");
  log.innerHTML = "";
  coach.hist.forEach((m) => addMsg(m.role, m.role === "user" ? H.esc(m.content) : H.renderMarkdown(m.content)));
  const sug = g ? [`Make "${g.title}" SMART`, "Break this into weekly steps for the next month", "Am I on pace? What should change?"] : COACH_SUGGEST;
  el.querySelector("#gcSuggest").innerHTML = sug.map((s) => `<button class="chip">${H.esc(s)}</button>`).join("");
  el.querySelectorAll("#gcSuggest .chip").forEach((c) => (c.onclick = () => sendCoach(c.textContent)));
  el.querySelector("#gcSuggest").classList.toggle("hidden", coach.hist.length > 0);
  el.classList.remove("hidden");
  setTimeout(() => el.querySelector("#gcInput").focus(), 50);
}

function addMsg(role, html) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.innerHTML = html;
  const log = document.getElementById("gcLog");
  log.appendChild(el);
  el.scrollIntoView({ behavior: "smooth", block: "end" });
  return el;
}

async function sendCoach(text) {
  text = (text || "").trim();
  const send = document.getElementById("gcSend"), inp = document.getElementById("gcInput");
  if (!text || send.disabled) return;
  inp.value = ""; inp.style.height = "auto";
  document.getElementById("gcSuggest").classList.add("hidden");
  addMsg("user", H.esc(text));
  coach.hist.push({ role: "user", content: text });
  const pending = addMsg("assistant", `<span class="typing"><span></span><span></span><span></span></span>`);
  send.disabled = true;
  try {
    const r = await H.api("/api/goals/coach", { method: "POST", body: JSON.stringify({ messages: coach.hist, goal_id: coach.goalId }) });
    pending.remove();
    const reply = r.reply || "";
    coach.hist.push({ role: "assistant", content: reply + (r.proposal ? `\n\n(Proposed goal: ${r.proposal.title})` : "") });
    addMsg("assistant", H.renderMarkdown(reply));
    if (r.proposal) proposalCard(r.proposal);
  } catch (e) {
    pending.remove(); coach.hist.pop();
    addMsg("assistant error", H.esc(e.message));
  } finally { send.disabled = false; inp.focus(); }
}

function proposalCard(p) {
  const m = p.measure || {};
  const track = m.kind === "steps" ? `${p.steps.length}-step plan`
    : `${m.metric ? "Tracks itself" : "You update it"}${m.target != null ? ` · target ${fnum(m.target, m.unit)}${unitTxt(m.unit)}` : ""}`;
  const el = addMsg("assistant", `<div class="proposal">
    <p class="eyebrow">${p.id ? "Updated goal" : "Proposed goal"} · ${H.esc(areaName(p.area))}</p>
    <h4>${H.esc(p.title)}</h4>
    <p class="gl-meta">${H.esc(track)}${p.due_date ? ` · by ${fd(p.due_date, true)}` : ""}</p>
    ${p.steps.length ? `<ol class="proposal-steps">${p.steps.map((s) => `<li><span>${H.esc(s.title)}</span><span>${s.due ? fd(s.due) : ""}</span></li>`).join("")}</ol>` : ""}
    <div class="proposal-actions"><button class="btn btn-primary btn-sm" data-p="save">${p.id ? "Save changes" : "Save goal"}</button><button class="btn btn-secondary btn-sm" data-p="edit">Edit first</button></div>
  </div>`);
  el.querySelector("[data-p=save]").onclick = async (ev) => {
    ev.target.disabled = true;
    try {
      const r = await H.api(p.id ? `/api/goals/${p.id}` : "/api/goals", { method: p.id ? "PUT" : "POST", body: JSON.stringify(p) });
      setState(r); ev.target.textContent = "Saved"; H.toast(p.id ? "Goal updated" : "Goal added");
    } catch (e) { ev.target.disabled = false; H.showError(e); }
  };
  el.querySelector("[data-p=edit]").onclick = () => {
    const existing = p.id && state.goals.find((g) => g.id === p.id);
    openEditor(existing ? { ...existing, ...p } : null, p);
  };
}

// ---------- home card
export async function goalsHomeStat() {
  const r = await H.api("/api/goals");
  const active = r.goals.filter((g) => g.status === "active");
  if (!r.goals.length) return null;
  const behind = r.counts.behind || 0;
  return `${active.length}<small>active · ${behind ? `${behind} behind` : "none behind"}${r.counts.done ? ` · ${r.counts.done} done` : ""}</small>`;
}

import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import { getAuth, GoogleAuthProvider, signInWithPopup, signOut, onAuthStateChanged }
  from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import { API_BASE, FIREBASE_CONFIG } from "./config.js";

const auth = getAuth(initializeApp(FIREBASE_CONFIG));
const $ = (id) => document.getElementById(id);
const $$ = (sel) => document.querySelectorAll(sel);
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const iso = (d) => d.toISOString().slice(0, 10);
const backendReady = !API_BASE.includes("YOUR-");
const charts = {};
let allTx = [];
const chatHistory = []; // [{role, content}] — kept in the browser only

// ---------- Sections & routing ----------
const SECTIONS = {
  finances: { title: "Finances", tabs: [
    ["overview", "Overview"], ["transactions", "Transactions"], ["investments", "Investments"],
    ["assistant", "Assistant"], ["accounts", "Accounts"]] },
  health: { title: "Health", tabs: [
    ["overview", "Overview"], ["workouts", "Workouts"], ["calendar", "Calendar"], ["connect", "Connect"]] },
  education: { title: "Education", tabs: [
    ["overview", "Overview"], ["grades", "Grades & GPA"], ["audit", "Degree Audit"], ["planner", "Planner"]] },
  goals: { title: "Goals", tabs: [] },
};

let routeQuery = new URLSearchParams();
function parseRoute() {
  const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
  const [section, tab] = path.split("/");
  routeQuery = new URLSearchParams(query || "");
  if (!SECTIONS[section]) return { section: "home" };
  const tabs = SECTIONS[section].tabs;
  return { section, tab: tabs.some(([k]) => k === tab) ? tab : tabs[0]?.[0] };
}

function render() {
  if (!auth.currentUser) return;
  const { section, tab } = parseRoute();
  $$(".route").forEach((r) => r.classList.toggle("active", r.dataset.route === section));
  $$(".section-links a").forEach((a) => a.classList.toggle("active", a.dataset.section === section));

  const cfg = SECTIONS[section];
  const hasTabs = cfg && cfg.tabs.length;
  $("subnav").classList.toggle("hidden", !hasTabs);
  if (hasTabs) {
    $("subnavTitle").textContent = cfg.title;
    $("subnavTabs").innerHTML = cfg.tabs.map(([k, label]) =>
      `<a href="#/${section}/${k}" class="${k === tab ? "active" : ""}">${label}</a>`).join("");
    const showControls = (section === "finances" && ["overview", "transactions", "investments"].includes(tab))
      || (section === "health" && tab === "overview");
    $("sectionControls").classList.toggle("hidden", !showControls);
    $$(`[data-route="${section}"] .panel`).forEach((p) => p.classList.toggle("active", p.dataset.panel === tab));
  }
  window.scrollTo({ top: 0 });
  document.title = section === "home" ? "LifeTracker" : `${cfg.title} · LifeTracker`;
  load(section, tab).catch((e) => toast(e.message));
}

async function load(section, tab) {
  if (section === "home") return loadHome();
  if (!backendReady) return toast("Backend not connected yet");
  if (section === "health") return loadHealth(tab);
  if (section === "education") return loadEducation(tab);
  if (section !== "finances") return;
  if (tab === "overview" || tab === "transactions") return loadOverview();
  if (tab === "investments") return loadInvestments();
  if (tab === "assistant") setTimeout(() => $("chatInput").focus(), 50);
}

window.addEventListener("hashchange", render);

// ---------- API helper (attaches Firebase ID token) ----------
async function api(path, opts = {}) {
  const token = await auth.currentUser.getIdToken();
  const res = await fetch(API_BASE + path, {
    ...opts,
    headers: { Authorization: `Bearer ${token}`,
               ...(opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 3800);
}

function rangeParams() {
  const days = +$("range").value;
  return `?start=${iso(new Date(Date.now() - days * 864e5))}&end=${iso(new Date())}`;
}

// ---------- Theme ----------
const MOON = '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>';
const SUN = '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>';
const isDark = () => {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
};
function paintThemeIcon() { $("themeBtn").querySelector("svg").innerHTML = isDark() ? SUN : MOON; }
$("themeBtn").onclick = () => {
  const next = isDark() ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("lt-theme", next); } catch {}
  paintThemeIcon();
  Object.values(charts).forEach((c) => restyleChart(c));
};
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { paintThemeIcon(); Object.values(charts).forEach(restyleChart); });
paintThemeIcon();

// ---------- Charts (single accent, quiet axes) ----------
const cssVar = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

function chartColors() {
  return { accent: cssVar("--accent"), text: cssVar("--text-3"), line: cssVar("--line") };
}

function restyleChart(c) {
  const k = chartColors();
  c.options.scales.x.ticks.color = c.options.scales.y.ticks.color = k.text;
  c.options.scales.y.grid.color = k.line;
  c.data.datasets[0].borderColor = k.accent;
  if (c.config.type === "bar") c.data.datasets[0].backgroundColor = k.accent;
  c.update("none");
}

function drawChart(id, type, labels, data, fmt = { tip: (v) => usd.format(v), tick: (v) => usd0.format(v) }, opts = {}) {
  charts[id]?.destroy();
  const k = chartColors();
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 12;
  charts[id] = new Chart($(id), {
    type,
    data: { labels, datasets: [{
      data,
      backgroundColor: type === "line"
        ? (ctx) => { const g = ctx.chart.ctx.createLinearGradient(0, 0, 0, ctx.chart.height || 300);
                     const a = chartColors().accent; g.addColorStop(0, a + "33"); g.addColorStop(1, a + "00"); return g; }
        : k.accent,
      borderColor: k.accent, borderWidth: type === "line" ? 2 : 0, fill: type === "line",
      tension: 0.35, borderRadius: 6, maxBarThickness: 48, pointRadius: 0, pointHoverRadius: 4, spanGaps: true,
    }] },
    options: {
      aspectRatio: type === "line" ? 2.8 : 1.8,
      plugins: { legend: { display: false },
                 tooltip: { displayColors: false, callbacks: { label: (c) => fmt.tip(c.parsed.y) } } },
      scales: {
        y: { border: { display: false }, ticks: { color: k.text, maxTicksLimit: 5, callback: (v) => fmt.tick(v) }, grid: { color: k.line }, ...(opts.y || {}) },
        x: { border: { display: false }, ticks: { color: k.text, maxTicksLimit: 7, maxRotation: 0 }, grid: { display: false } },
      },
    },
  });
}

function barRows(elId, entries, max = 7) {
  const rows = [...entries].filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).slice(0, max);
  if (!rows.length) { $(elId).innerHTML = `<div class="empty">No data yet. Connect an account or import a CSV.</div>`; return; }
  const top = rows[0][1] || 1;
  $(elId).innerHTML = rows.map(([label, v]) => `
    <div class="bar-row">
      <span class="bar-label" title="${esc(label)}">${esc(label)}</span>
      <span class="bar-track"><span class="bar-fill" style="display:block;width:${Math.max(1.5, (v / top) * 100)}%"></span></span>
      <span class="bar-value">${usd0.format(v)}</span>
    </div>`).join("");
}

// ---------- Home ----------
function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

async function loadHome() {
  const first = (auth.currentUser.displayName || "").split(" ")[0];
  $("homeGreeting").textContent = `${greeting()}${first ? `, ${first}` : ""}.`;
  $("homeDate").textContent = new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  if (!backendReady) return;
  const now = new Date();
  try {
    const s = await api(`/api/summary?start=${iso(new Date(now.getFullYear(), now.getMonth(), 1))}&end=${iso(now)}`);
    $("homeFinance").innerHTML = `${usd0.format(s.total_spent)}<small>spent this month</small>`;
  } catch { $("homeFinance").textContent = "Open to connect accounts"; }
  try {
    const h = await api(`/api/health/summary?start=${iso(new Date(Date.now() - 7 * 864e5))}&end=${iso(now)}`);
    const rec = h.latest?.recovery;
    $("homeHealth").classList.toggle("muted-stat", false);
    $("homeHealth").innerHTML = rec != null
      ? `${Math.round(rec)}%<small>recovery · ${h.workouts_this_week} workout${h.workouts_this_week === 1 ? "" : "s"} this week</small>`
      : `${h.workouts_this_week}<small>workout${h.workouts_this_week === 1 ? "" : "s"} this week</small>`;
  } catch { $("homeHealth").textContent = "Log your first workout"; }
  try {
    const e = await api("/api/edu");
    const C = e.computed;
    $("homeEdu").classList.toggle("muted-stat", !e.courses.length);
    $("homeEdu").innerHTML = e.courses.length
      ? `${C.gpa_actual?.toFixed(2) ?? "—"}<small>GPA · projected ${C.gpa_projected?.toFixed(2) ?? "—"} vs ${C.profile.target_gpa.toFixed(2)} target</small>`
      : "Import your courses";
  } catch { $("homeEdu").textContent = "Import your courses"; }
}

// ---------- Finances ----------
async function loadOverview() {
  const [s, h, tx] = await Promise.all([
    api("/api/summary" + rangeParams()), api("/api/holdings"), api("/api/transactions" + rangeParams())]);
  $("kpi-spent").textContent = usd0.format(s.total_spent);
  $("kpi-in").textContent = usd0.format(s.total_in);
  const net = s.total_in - s.total_spent;
  $("kpi-net").textContent = (net < 0 ? "−" : "") + usd0.format(Math.abs(net));
  $("kpi-net").className = "kpi-value " + (net >= 0 ? "pos" : "neg");
  $("kpi-portfolio").textContent = h.latest ? usd0.format(h.latest.total) : "—";
  const fmt = (d) => new Date(d + "T00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
  $("kpi-spent-sub").textContent = `${fmt(s.start)} – ${fmt(s.end)}`;
  $("catSubtitle").textContent = `${Object.keys(s.by_category).length} categories`;

  barRows("categoryBars", Object.entries(s.by_category));
  barRows("sourceBars", Object.entries(s.by_source));
  const months = Object.keys(s.by_month).map((m) => new Date(m + "-01T00:00").toLocaleDateString("en-US", { month: "short" }));
  drawChart("chart-month", "bar", months, Object.values(s.by_month));

  allTx = tx.transactions;
  const byMerchant = {};
  allTx.filter((t) => t.amount > 0).forEach((t) => (byMerchant[t.merchant] = (byMerchant[t.merchant] || 0) + t.amount));
  barRows("merchantBars", Object.entries(byMerchant).sort((a, b) => b[1] - a[1]));
  renderTx();
}

function renderTx() {
  const q = $("tx-search").value.toLowerCase();
  const rows = allTx.filter((t) => !q || `${t.merchant} ${t.category} ${t.source} ${t.note || ""}`.toLowerCase().includes(q));
  $("tx-count").textContent = rows.length.toLocaleString();
  const net = rows.reduce((a, t) => a + t.amount, 0);
  $("tx-total").textContent = net >= 0 ? `${usd.format(net)} out` : `+${usd.format(-net)} in`;
  const fmt = (d) => new Date(d + "T00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  $("tx-body").innerHTML = rows.slice(0, 500).map((t) => `
    <tr><td>${fmt(t.date)}</td>
      <td>${esc(t.merchant)}${t.pending ? '<span class="pending">Pending</span>' : ""}</td>
      <td class="tag">${esc(t.category)}</td><td class="tag">${esc(t.source)}</td>
      <td class="num ${t.amount < 0 ? "pos" : ""}">${t.amount < 0 ? "+" : ""}${usd.format(Math.abs(t.amount))}</td></tr>`).join("")
    || `<tr><td colspan="5" class="empty">No transactions in this range</td></tr>`;
}

async function loadInvestments() {
  const h = await api("/api/holdings");
  $("portfolioTotal").textContent = h.latest ? usd.format(h.latest.total) : "";
  const fmt = (d) => new Date(d + "T00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
  drawChart("chart-portfolio", "line", h.history.map((x) => fmt(x.date)), h.history.map((x) => x.total));
  $("holdings-body").innerHTML = (h.latest?.holdings || []).sort((a, b) => b.value - a.value).map((x) => {
    const gain = x.cost_basis != null ? x.value - x.cost_basis : null;
    return `<tr><td><strong>${esc(x.ticker || "—")}</strong></td><td class="tag">${esc(x.name)}</td>
      <td class="num">${x.quantity.toLocaleString()}</td><td class="num">${usd.format(x.value)}</td>
      <td class="num ${gain == null ? "" : gain >= 0 ? "pos" : "neg"}">${gain == null ? "—" : (gain >= 0 ? "+" : "−") + usd.format(Math.abs(gain))}</td></tr>`;
  }).join("") || `<tr><td colspan="5" class="empty">Connect a brokerage and sync to see holdings</td></tr>`;
}

// ---------- Finance assistant ----------
const renderMarkdown = (md) => DOMPurify.sanitize(marked.parse(md || ""));

function addMessage(role, html, meta = "") {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.innerHTML = html + (meta ? `<div class="msg-meta">${esc(meta)}</div>` : "");
  $("chatMessages").appendChild(el);
  el.scrollIntoView({ behavior: "smooth", block: "end" });
  return el;
}

async function sendChat(text) {
  text = (text || "").trim();
  if (!text || $("chatSend").disabled) return;
  if (!backendReady) return toast("Backend not connected yet");
  $("chatInput").value = ""; autosize();
  $("chatSuggestions").classList.add("hidden");
  addMessage("user", esc(text));
  chatHistory.push({ role: "user", content: text });
  const pending = addMessage("assistant", `<span class="typing"><span></span><span></span><span></span></span>`);
  $("chatSend").disabled = true;
  try {
    const r = await api("/api/chat", { method: "POST", body: JSON.stringify({ messages: chatHistory }) });
    chatHistory.push({ role: "assistant", content: r.reply });
    pending.remove();
    const looked = [...new Set(r.tools_used || [])].map((t) => t.replaceAll("_", " ")).join(", ");
    addMessage("assistant", renderMarkdown(r.reply), looked ? `Looked up: ${looked}` : "");
  } catch (e) {
    pending.remove();
    chatHistory.pop();
    addMessage("assistant error", esc(e.message));
  } finally {
    $("chatSend").disabled = false;
    $("chatInput").focus();
  }
}

function autosize() { const t = $("chatInput"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 160) + "px"; }
$("chatInput").addEventListener("input", autosize);
$("chatSend").onclick = () => sendChat($("chatInput").value);
$("chatInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat($("chatInput").value); }
});
$$(".chip").forEach((c) => (c.onclick = () => sendChat(c.textContent)));

// ---------- Plaid Link ----------
async function connect(kind) {
  if (!backendReady) return toast("Backend not connected yet");
  const { link_token } = await api("/api/plaid/link-token", { method: "POST", body: JSON.stringify({ kind }) });
  Plaid.create({
    token: link_token,
    onSuccess: async (public_token, meta) => {
      await api("/api/plaid/exchange", { method: "POST",
        body: JSON.stringify({ public_token, kind, institution: meta.institution?.name }) });
      toast(`Connected ${meta.institution?.name}. Syncing…`);
      await doSync();
    },
  }).open();
}

async function doSync() {
  if (!backendReady) return toast("Backend not connected yet");
  if (parseRoute().section === "health") return syncWhoop();
  $("sync").disabled = true; $("sync").textContent = "Syncing…";
  try { await api("/api/sync", { method: "POST" }); toast("Up to date"); render(); }
  catch (e) { toast("Sync failed: " + e.message); }
  finally { $("sync").disabled = false; $("sync").textContent = "Sync"; }
}

// ---------- Health ----------
const recZone = (r) => (r == null ? "" : r >= 67 ? "rec-green" : r >= 34 ? "rec-yellow" : "rec-red");
const shortDate = (d) => new Date(d + "T00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
const fmtHours = (h) => (h == null ? "—" : `${Math.floor(h)}h ${String(Math.round((h % 1) * 60)).padStart(2, "0")}m`);
let calMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
let whoopState = null;

async function loadHealth(tab) {
  if (tab === "overview") return loadHealthOverview();
  if (tab === "workouts") return loadWorkoutsTab();
  if (tab === "calendar") return renderCalendar();
  if (tab === "connect") return loadConnect();
}

async function loadHealthOverview() {
  const h = await api("/api/health/summary" + rangeParams());
  whoopState = h.whoop;
  const L = h.latest;
  $("h-recovery").textContent = L?.recovery != null ? `${Math.round(L.recovery)}%` : "—";
  $("h-recovery").className = "kpi-value " + (L ? recZone(L.recovery) + "-text" : "");
  $("h-recovery-sub").textContent = L ? `${shortDate(L.date)} · RHR ${L.rhr ?? "—"} bpm` : "";
  $("h-hrv").textContent = L?.hrv != null ? `${Math.round(L.hrv)} ms` : "—";
  $("h-hrv-sub").textContent = h.averages.hrv != null ? `Avg ${Math.round(h.averages.hrv)} ms` : "";
  const lastSleep = [...h.days].reverse().find((d) => d.sleep_hours != null);
  $("h-sleep").textContent = lastSleep ? fmtHours(lastSleep.sleep_hours) : "—";
  $("h-sleep-sub").textContent = lastSleep?.sleep_performance != null ? `${Math.round(lastSleep.sleep_performance)}% of need` : "";
  $("h-workouts").textContent = h.workouts_this_week;

  const hasWhoop = h.whoop.connected && h.days.length;
  $("whoopEmpty").classList.toggle("hidden", h.whoop.connected);
  $("healthCharts").classList.toggle("hidden", !hasWhoop);
  if (!hasWhoop) return;
  const labels = h.days.map((d) => shortDate(d.date));
  const series = (k) => h.days.map((d) => d[k] ?? null);
  const pct = { tip: (v) => `${Math.round(v)}%`, tick: (v) => `${v}%` };
  drawChart("chart-recovery", "line", labels, series("recovery"), pct, { y: { min: 0, max: 100 } });
  drawChart("chart-hrv", "line", labels, series("hrv"), { tip: (v) => `${Math.round(v)} ms`, tick: (v) => `${v}` });
  drawChart("chart-sleep", "bar", labels, series("sleep_hours"), { tip: fmtHours, tick: (v) => `${v}h` }, { y: { min: 0 } });
  drawChart("chart-strain", "bar", labels, series("strain"), { tip: (v) => v.toFixed(1), tick: (v) => `${v}` }, { y: { min: 0, max: 21 } });
  const A = h.averages;
  $("avg-recovery").textContent = A.recovery != null ? `Avg ${Math.round(A.recovery)}%` : "";
  $("avg-hrv").textContent = A.hrv != null ? `Avg ${Math.round(A.hrv)} ms` : "";
  $("avg-sleep").textContent = A.sleep_hours != null ? `Avg ${fmtHours(A.sleep_hours)}` : "";
  $("avg-strain").textContent = A.strain != null ? `Avg ${A.strain.toFixed(1)}` : "";
}

// Workout log
function exerciseRow(e = {}) {
  const row = document.createElement("div");
  row.className = "exercise-row";
  row.innerHTML = `
    <input class="input" placeholder="Exercise" value="${esc(e.name || "")}" data-k="name" />
    <input class="input" type="number" min="0" placeholder="Sets" value="${e.sets ?? ""}" data-k="sets" />
    <input class="input" type="number" min="0" placeholder="Reps" value="${e.reps ?? ""}" data-k="reps" />
    <input class="input" type="number" min="0" step="0.5" placeholder="lb" value="${e.weight ?? ""}" data-k="weight" />
    <button type="button" class="icon-btn" aria-label="Remove exercise">×</button>`;
  row.querySelector("button").onclick = () => row.remove();
  $("exerciseRows").appendChild(row);
}

function describeWorkout(w) {
  const bits = [];
  if (w.duration_min) bits.push(`${w.duration_min} min`);
  if (w.strain != null) bits.push(`Strain ${w.strain}`);
  if (w.avg_hr) bits.push(`Avg HR ${w.avg_hr}`);
  if (w.calories) bits.push(`${w.calories} cal`);
  return bits.join(" · ");
}

function exerciseText(w) {
  return (w.exercises || []).map((e) => {
    const sr = e.sets && e.reps ? ` ${e.sets}×${e.reps}` : e.sets ? ` ${e.sets} sets` : "";
    return `${esc(e.name)}${sr}${e.weight ? ` @ ${e.weight} lb` : ""}`;
  }).join(", ");
}

async function loadWorkoutsTab() {
  if (!$("w-date").value) $("w-date").value = iso(new Date(Date.now() - new Date().getTimezoneOffset() * 6e4));
  if (!$("exerciseRows").children.length) exerciseRow();
  const { workouts } = await api("/api/workouts");
  $("workoutList").innerHTML = workouts.map((w) => `
    <div class="workout-item">
      <div class="workout-date">${shortDate(w.date)}</div>
      <div class="workout-main">
        <div class="workout-title">${esc(w.type)}${w.source === "whoop" ? '<span class="source-tag">WHOOP</span>' : ""}</div>
        <div class="workout-meta">${describeWorkout(w)}</div>
        ${w.exercises?.length ? `<div class="workout-ex">${exerciseText(w)}</div>` : ""}
        ${w.notes ? `<div class="workout-ex">${esc(w.notes)}</div>` : ""}
      </div>
      ${w.source === "manual" ? `<button class="text-btn" data-del="${esc(w.id)}">Delete</button>` : ""}
    </div>`).join("") || `<div class="empty">No workouts yet. Log your first one.</div>`;
  $$("[data-del]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Delete this workout?")) return;
    await api(`/api/workouts/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" });
    toast("Workout deleted"); loadWorkoutsTab();
  }));
}

$("addExercise").onclick = () => exerciseRow();
$("workoutForm").onsubmit = async (e) => {
  e.preventDefault();
  const exercises = [...$("exerciseRows").children].map((row) => {
    const o = {}; row.querySelectorAll("[data-k]").forEach((i) => (o[i.dataset.k] = i.value.trim())); return o;
  }).filter((x) => x.name);
  $("w-save").disabled = true;
  try {
    await api("/api/workouts", { method: "POST", body: JSON.stringify({
      date: $("w-date").value, type: $("w-type").value.trim(), duration_min: $("w-duration").value,
      notes: $("w-notes").value.trim(), exercises }) });
    toast("Workout saved");
    $("w-type").value = ""; $("w-duration").value = ""; $("w-notes").value = ""; $("exerciseRows").innerHTML = ""; exerciseRow();
    loadWorkoutsTab();
  } catch (err) { toast(err.message); }
  finally { $("w-save").disabled = false; }
};

// Calendar
async function renderCalendar() {
  const y = calMonth.getFullYear(), m = calMonth.getMonth();
  const first = new Date(y, m, 1), last = new Date(y, m + 1, 0);
  $("calTitle").textContent = first.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  const ds = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const [h, wk] = await Promise.all([
    api(`/api/health/summary?start=${ds(first)}&end=${ds(last)}`),
    api(`/api/workouts?start=${ds(first)}&end=${ds(last)}`)]);
  const byDay = Object.fromEntries(h.days.map((d) => [d.date, d]));
  const workoutsByDay = {};
  wk.workouts.forEach((w) => (workoutsByDay[w.date] ||= []).push(w));
  const lead = (first.getDay() + 6) % 7;          // weeks start Monday
  const todayStr = ds(new Date());
  let html = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => `<div class="cal-dow">${d}</div>`).join("");
  for (let i = 0; i < lead; i++) html += `<div class="cal-cell cal-pad"></div>`;
  for (let day = 1; day <= last.getDate(); day++) {
    const key = ds(new Date(y, m, day));
    const hd = byDay[key], ws = workoutsByDay[key] || [];
    html += `<button class="cal-cell${key === todayStr ? " today" : ""}${ws.length ? " has-workout" : ""}" data-day="${key}">
      <span class="cal-num">${day}</span>
      ${hd?.recovery != null ? `<span class="cal-rec"><i class="dot ${recZone(hd.recovery)}"></i>${Math.round(hd.recovery)}%</span>` : ""}
      <span class="cal-workouts">${ws.slice(0, 3).map((w) => `<span class="cal-chip">${esc(w.type)}</span>`).join("")}${ws.length > 3 ? `<span class="cal-more">+${ws.length - 3}</span>` : ""}</span>
    </button>`;
  }
  $("calendar").innerHTML = html;
  $("calDetail").classList.add("hidden");
  $$("[data-day]").forEach((c) => (c.onclick = () => {
    const key = c.dataset.day, hd = byDay[key], ws = workoutsByDay[key] || [];
    const stats = hd ? [hd.recovery != null && `Recovery ${Math.round(hd.recovery)}%`, hd.hrv != null && `HRV ${Math.round(hd.hrv)} ms`,
      hd.sleep_hours != null && `Sleep ${fmtHours(hd.sleep_hours)}`, hd.strain != null && `Strain ${hd.strain}`].filter(Boolean).join(" · ") : "";
    $("calDetail").innerHTML = `<h3>${new Date(key + "T00:00").toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })}</h3>
      ${stats ? `<p>${stats}</p>` : ""}
      ${ws.map((w) => `<div class="workout-item"><div class="workout-main"><div class="workout-title">${esc(w.type)}</div>
        <div class="workout-meta">${describeWorkout(w)}</div>${w.exercises?.length ? `<div class="workout-ex">${exerciseText(w)}</div>` : ""}</div></div>`).join("")
        || `<p>No workouts logged. <a class="link" href="#/health/workouts">Log one →</a></p>`}`;
    $("calDetail").classList.remove("hidden");
  }));
}
$("calPrev").onclick = () => { calMonth = new Date(calMonth.getFullYear(), calMonth.getMonth() - 1, 1); renderCalendar().catch((e) => toast(e.message)); };
$("calNext").onclick = () => { calMonth = new Date(calMonth.getFullYear(), calMonth.getMonth() + 1, 1); renderCalendar().catch((e) => toast(e.message)); };
$("calToday").onclick = () => { calMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1); renderCalendar().catch((e) => toast(e.message)); };

// WHOOP connection
async function loadConnect() {
  const result = routeQuery.get("whoop");
  if (result) {
    history.replaceState(null, "", "#/health/connect");
    const msg = { connected: "WHOOP connected. Pulling your history…", cancelled: "WHOOP connection cancelled",
                  expired: "That link expired. Try connecting again.", failed: "WHOOP couldn't finish connecting. Try again." }[result];
    if (msg) toast(msg);
  }
  const h = await api(`/api/health/summary?start=${iso(new Date())}&end=${iso(new Date())}`);
  whoopState = h.whoop;
  const on = h.whoop.connected;
  $("whoopStatus").textContent = on
    ? `Connected${h.whoop.last_sync ? ` · last synced ${new Date(h.whoop.last_sync).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}` : " · not synced yet"}`
    : "Recovery, HRV, resting heart rate, strain, sleep and workouts.";
  $("whoopConnect").classList.toggle("hidden", on);
  $("whoopSync").classList.toggle("hidden", !on);
  $("whoopDisconnect").classList.toggle("hidden", !on);
  if (result === "connected") await syncWhoop();
}

async function syncWhoop() {
  const btns = [$("sync"), $("whoopSync")];
  btns.forEach((b) => { b.disabled = true; });
  $("sync").textContent = "Syncing…";
  try {
    const r = await api("/api/whoop/sync", { method: "POST" });
    toast(`WHOOP synced · ${r.days} days, ${r.workouts} workouts`);
    render();
  } catch (e) { toast(e.message); }
  finally { btns.forEach((b) => { b.disabled = false; }); $("sync").textContent = "Sync"; }
}

$("whoopConnect").onclick = async () => {
  try { const { url } = await api("/api/whoop/connect", { method: "POST" }); location.href = url; }
  catch (e) { toast(e.message); }
};
$("whoopSync").onclick = syncWhoop;
$("whoopDisconnect").onclick = async () => {
  if (!confirm("Disconnect WHOOP? Your synced history stays in LifeTracker.")) return;
  await api("/api/whoop", { method: "DELETE" }); toast("WHOOP disconnected"); loadConnect();
};

// ---------- Education ----------
let edu = null;            // server state {courses, computed, has_data}
let gradeDraft = {};       // courseId -> {grade, grade_status} while editing on the Grades tab
let plannerTerm = null;
let editingCourse = null;
const PRIORITY = { 3: "Must take", 2: "High", 1: "Maybe", 0: "" };
const STATUS_LABEL = { completed: "Completed", enrolled: "Enrolled", planned: "Planned", waitlisted: "Waitlisted", considering: "Considering", dropped: "Dropped" };
const fmtGpa = (v) => (v == null ? "—" : v.toFixed(2));
const r2 = (x) => Math.floor(x * 100 + 0.5 + 1e-9) / 100;

async function loadEducation(tab) {
  edu = await api("/api/edu");
  const p = edu.computed.profile;
  $("eduSchool").textContent = [p.school, p.program, p.grad_term ? `Expected ${p.grad_term}` : ""].filter(Boolean).join(" · ");
  const empty = !edu.courses.length;
  $("eduEmpty").classList.toggle("hidden", !empty);
  $("eduOverview").classList.toggle("hidden", empty);
  if (tab === "overview" && !empty) renderEduOverview();
  if (tab === "grades") { gradeDraft = {}; renderGrades(); }
  if (tab === "audit") renderAudit();
  if (tab === "planner") renderPlanner();
}

// Same math as education.compute(), used for instant what-if on the Grades tab.
function liveGpa() {
  const GP = edu.computed.grade_points, target = +$("targetGpa").value || edu.computed.profile.target_gpa;
  const counts = new Set(["completed", "enrolled", "planned"]);
  let aq = 0, ag = 0, pq = 0, pg = 0; const terms = {};
  edu.courses.forEach((c) => {
    if (!counts.has(c.status)) return;
    const d = gradeDraft[c.id] || {}; const g = d.grade ?? c.grade, gs = d.grade_status ?? c.grade_status;
    if (!(g in GP)) return;
    const cr = +c.credits || 0, q = GP[g] * cr;
    pq += q; pg += cr; (terms[c.term] ||= { q: 0, g: 0 }); terms[c.term].q += q; terms[c.term].g += cr;
    if (gs === "actual") { aq += q; ag += cr; }
  });
  const rem = edu.courses.filter((c) => ["enrolled", "planned"].includes(c.status) && (gradeDraft[c.id]?.grade_status ?? c.grade_status) !== "actual"
    && c.graded !== false && !["P", "H", "CR"].includes(gradeDraft[c.id]?.grade ?? c.grade));
  const remCr = rem.reduce((a, c) => a + (+c.credits || 0), 0);
  return { actual: ag ? r2(aq / ag) : null, projected: pg ? r2(pq / pg) : null,
           terms: Object.fromEntries(Object.entries(terms).map(([t, v]) => [t, v.g ? r2(v.q / v.g) : null])),
           needed: remCr ? (target * (ag + remCr) - aq) / remCr : null, remCr, target };
}

function renderEduOverview() {
  const C = edu.computed, n = C.needed;
  $("e-gpa").textContent = fmtGpa(C.gpa_actual);
  $("e-gpa-sub").textContent = `${C.graded_credits_actual} graded credits`;
  $("e-proj").textContent = fmtGpa(C.gpa_projected);
  $("e-proj").className = "kpi-value " + (C.gpa_projected != null ? (C.gpa_projected >= C.profile.target_gpa ? "pos" : "neg") : "");
  $("e-proj-sub").textContent = `vs. ${C.profile.target_gpa.toFixed(2)} target`;
  $("e-need").textContent = n ? (n.already_met ? "Met" : n.achievable ? n.average.toFixed(2) : "Out of reach") : "—";
  $("e-need-sub").textContent = n ? `avg on ${n.graded_credits} remaining graded credits` : "No remaining graded courses";
  $("e-credits").textContent = `${C.credits.earned + C.credits.in_progress + C.credits.planned} / ${C.credits.required}`;
  $("e-credits-sub").textContent = `${C.credits.earned} earned · ${C.credits.in_progress} in progress · ${C.credits.planned} planned`;

  const terms = C.terms.filter((t) => t.gpa_projected != null);
  drawChart("chart-gpa", "bar", terms.map((t) => t.term), terms.map((t) => t.gpa_projected),
    { tip: (v) => v.toFixed(2), tick: (v) => v.toFixed(1) }, { y: { min: 2.5, max: 4.33 } });
  // dim projected bars
  const ch = charts["chart-gpa"]; const acc = cssVar("--accent");
  ch.data.datasets[0].backgroundColor = terms.map((t) => (t.gpa_actual != null ? acc : acc + "66")); ch.update("none");

  $("eduAuditMini").innerHTML = C.audit.map((a) => `<div class="audit-mini"><span class="pill pill-${a.status}">${{ met: "Met", on_track: "On track", missing: "Missing" }[a.status]}</span><span>${esc(a.label)}</span></div>`).join("");

  const next = C.terms.find((t) => t.status === "planned") || C.terms.find((t) => t.status === "enrolled");
  if (next) {
    $("eduNextTermTitle").textContent = next.term;
    const pl = C.planner[next.term];
    const list = edu.courses.filter((c) => c.term === next.term && ["planned", "enrolled"].includes(c.status));
    $("eduNextTerm").innerHTML = `<div class="mini-stats"><span><strong>${pl.committed_credits}</strong> credits</span><span><strong>${pl.considering}</strong> still considering</span>${pl.conflicts.length ? `<span class="neg"><strong>${pl.conflicts.length}</strong> time conflict${pl.conflicts.length > 1 ? "s" : ""}</span>` : ""}</div>` +
      list.map((c) => `<div class="mini-row"><span>${esc(c.title)}</span><span class="tag">${c.credits ?? "—"} cr${c.graded === false ? " · P/F" : ""}</span></div>`).join("");
  }
  const hr = C.profile.honors_reference || [];
  $("eduHonors").innerHTML = `<p class="honors-target">Your target: <strong>${C.profile.target_gpa.toFixed(2)}</strong>. Projected: <strong class="${(C.gpa_projected || 0) >= C.profile.target_gpa ? "pos" : "neg"}">${fmtGpa(C.gpa_projected)}</strong></p>
    <p class="hint">Georgetown Law awards cum laude to the top third and magna cum laude to the top 10% of the class, so cutoffs move each year. Published examples:</p>
    ${hr.map((h) => `<div class="mini-row"><span>${esc(h.label)}</span><span class="tag">${h.examples.map((e) => `${e.gpa.toFixed(2)} (${e.year})`).join(" · ")}</span></div>`).join("")}`;
}

function gradeOptions(selected, includeBlank = true) {
  const opts = Object.keys(edu.computed.grade_points).concat(["P", "H", "CR"]);
  return (includeBlank ? `<option value="">—</option>` : "") + opts.map((g) => `<option ${g === selected ? "selected" : ""}>${g}</option>`).join("");
}

function renderGrades() {
  const C = edu.computed;
  if (document.activeElement !== $("targetGpa")) $("targetGpa").value = C.profile.target_gpa.toFixed(2);
  const live = liveGpa();
  $("gradeTerms").innerHTML = C.terms.filter((t) => t.status !== "empty").map((t) => {
    const rows = edu.courses.filter((c) => c.term === t.term && ["completed", "enrolled", "planned"].includes(c.status));
    return `<div class="card card-flush term-card">
      <div class="card-head padded"><h3>${esc(t.term)}</h3><span class="card-meta">${t.credits} credits · GPA <strong>${fmtGpa(live.terms[t.term])}</strong>${t.status !== "completed" ? " projected" : ""}</span></div>
      <table class="table grade-table"><colgroup><col /><col class="c-cr" /><col class="c-gr" /><col class="c-ty" /><col class="c-ed" /></colgroup><thead><tr><th>Course</th><th class="num">Credits</th><th>Grade</th><th>Type</th><th></th></tr></thead><tbody>
      ${rows.map((c) => { const d = gradeDraft[c.id] || {}; const g = d.grade ?? c.grade ?? ""; const gs = d.grade_status ?? c.grade_status ?? "projected";
        return `<tr class="${gradeDraft[c.id] ? "dirty" : ""}"><td>${esc(c.title)}</td><td class="num">${c.credits ?? "—"}</td>
        <td><select class="select grade-select" data-gid="${c.id}">${gradeOptions(g)}</select></td>
        <td><button class="seg ${gs === "actual" ? "seg-on" : ""}" data-gs="${c.id}" title="Toggle actual / projected">${gs === "actual" ? "Actual" : "Projected"}</button></td>
        <td class="num"><button class="text-btn" data-edit="${c.id}">Edit</button></td></tr>`; }).join("")}
      </tbody></table></div>`;
  }).join("");
  renderSolver(live);
  $("gradeSavebar").classList.toggle("hidden", !Object.keys(gradeDraft).length);
  $$(".grade-select").forEach((s) => (s.onchange = () => {
    const c = edu.courses.find((x) => x.id === s.dataset.gid);
    gradeDraft[c.id] = { ...(gradeDraft[c.id] || {}), grade: s.value || null };
    renderGrades();
  }));
  $$("[data-gs]").forEach((b) => (b.onclick = () => {
    const c = edu.courses.find((x) => x.id === b.dataset.gs);
    const cur = gradeDraft[c.id]?.grade_status ?? c.grade_status ?? "projected";
    gradeDraft[c.id] = { ...(gradeDraft[c.id] || {}), grade_status: cur === "actual" ? "projected" : "actual" };
    renderGrades();
  }));
  $$("[data-edit]").forEach((b) => (b.onclick = () => openCourse(edu.courses.find((x) => x.id === b.dataset.edit))));
}

function renderSolver(live) {
  const n = live.needed, server = edu.computed.needed;
  let html = `<div class="solver-stats"><div><span class="kpi-label">Current</span><strong>${fmtGpa(live.actual)}</strong></div>
    <div><span class="kpi-label">Projected</span><strong class="${(live.projected || 0) >= live.target ? "pos" : "neg"}">${fmtGpa(live.projected)}</strong></div>
    <div><span class="kpi-label">Needed average</span><strong>${n == null ? "—" : n <= 0 ? "Met" : n > 4.33 ? "Out of reach" : n.toFixed(2)}</strong></div></div>`;
  if (n != null && n > 0 && n <= 4.33) {
    html += `<p class="solver-text">To finish at <strong>${live.target.toFixed(2)}</strong>, you need a <strong>${n.toFixed(2)}</strong> average across your ${live.remCr} remaining graded credits.</p>`;
    if (server?.plan && !Object.keys(gradeDraft).length) {
      const as = server.plan.filter((p) => ["A", "A+"].includes(p.grade)), rest = server.plan.filter((p) => !["A", "A+"].includes(p.grade));
      html += `<p class="solver-text">One way to get there: <strong>${as.map((p) => `${p.grade} in ${esc(p.title.split(":")[0])}`).join(", ")}</strong>${rest.length ? `, and ${rest[0].grade} or better in the rest` : ""}.</p>
        <button class="btn btn-secondary btn-sm" id="applyPlan">Try this plan</button>`;
    }
  } else if (n != null && n > 4.33) {
    html += `<p class="solver-text">That target isn't reachable with your remaining graded credits.</p>`;
  }
  $("solverOut").innerHTML = html;
  $("applyPlan")?.addEventListener("click", () => {
    server.plan.forEach((p) => (gradeDraft[p.id] = { ...(gradeDraft[p.id] || {}), grade: p.grade }));
    renderGrades(); toast("Applied as a what-if. Save to keep it.");
  });
}

$("targetGpa").oninput = () => { if (edu) renderSolver(liveGpa()); };
$("targetSave").onclick = async () => {
  try { edu = await api("/api/edu/profile", { method: "PUT", body: JSON.stringify({ target_gpa: +$("targetGpa").value }) }); toast("Target updated"); renderGrades(); }
  catch (e) { toast(e.message); }
};
$("gradeDiscard").onclick = () => { gradeDraft = {}; renderGrades(); };
$("gradeSave").onclick = async () => {
  $("gradeSave").disabled = true;
  try {
    for (const [id, d] of Object.entries(gradeDraft)) {
      const body = { ...d };
      if ("grade" in d) body.graded = !["P", "H", "CR"].includes(d.grade);
      edu = await api(`/api/edu/courses/${id}`, { method: "PATCH", body: JSON.stringify(body) });
    }
    gradeDraft = {}; toast("Grades saved"); renderGrades();
  } catch (e) { toast(e.message); }
  finally { $("gradeSave").disabled = false; }
};

function renderAudit() {
  $("auditList").innerHTML = edu.computed.audit.map((a) => {
    let body = "";
    if (a.type === "credits") {
      const pct = (v) => Math.min(100, (v / a.need) * 100);
      body = `<div class="stack-bar"><span class="seg-done" style="width:${pct(a.done)}%"></span><span class="seg-prog" style="width:${pct(a.in_progress)}%"></span><span class="seg-plan" style="width:${pct(a.planned)}%"></span></div>
        <div class="stack-legend"><span><i class="lg lg-done"></i>${a.done} earned</span><span><i class="lg lg-prog"></i>${a.in_progress} in progress</span><span><i class="lg lg-plan"></i>${a.planned} planned</span>${a.short_by ? `<span class="neg">${a.short_by} short</span>` : ""}</div>`;
    }
    if (a.type === "gpa") body = `<div class="audit-courses">Current GPA ${fmtGpa(a.value)}</div>`;
    if (a.courses) body += `<div class="audit-courses">${a.courses.length ? a.courses.map(esc).join(" · ") : "No course tagged yet. Open a course in the Planner and check this requirement."}</div>`;
    return `<div class="audit-row"><div class="audit-title"><span class="pill pill-${a.status}">${{ met: "Met", on_track: "On track", missing: "Missing" }[a.status]}</span><h3>${esc(a.label)}</h3></div>${body}</div>`;
  }).join("");
}

function renderPlanner() {
  const C = edu.computed;
  const terms = C.profile.terms;
  if (!plannerTerm) plannerTerm = (C.terms.find((t) => t.status === "planned") || C.terms[C.terms.length - 1] || {}).term || terms[0];
  $("plannerTerm").innerHTML = terms.map((t) => `<option ${t === plannerTerm ? "selected" : ""}>${esc(t)}</option>`).join("");
  const pl = C.planner[plannerTerm] || { committed_credits: 0, graded_credits: 0, pf_credits: 0, conflicts: [], considering: 0 };
  const remaining = C.credits.remaining_after_plan;
  $("plannerStats").innerHTML = `<span><strong>${pl.committed_credits}</strong> credits planned</span><span><strong>${pl.graded_credits}</strong> graded · <strong>${pl.pf_credits}</strong> P/F</span><span class="${remaining ? "neg" : "pos"}">${remaining ? `${remaining} credits short of ${C.credits.required}` : `Reaches ${C.credits.required} credits`}</span>`;
  const missing = C.audit.filter((a) => a.status === "missing" && a.key !== "min_gpa");
  $("plannerAlerts").innerHTML =
    pl.conflicts.map((c) => `<div class="alert ${c.both_committed ? "alert-bad" : ""}">Time conflict: ${esc(c.a_title)} and ${esc(c.b_title)}${c.both_committed ? " (both planned)" : ""}</div>`).join("") +
    (missing.length ? `<div class="alert">Still unmet after this plan: ${missing.map((m) => esc(m.label)).join(", ")}</div>` : "");
  const conflictIds = new Set(pl.conflicts.flatMap((c) => [c.a, c.b]));
  const cols = [["planned", "Planned"], ["enrolled", "Enrolled"], ["waitlisted", "Waitlisted"], ["considering", "Considering"], ["dropped", "Dropped"]];
  const inTerm = edu.courses.filter((c) => c.term === plannerTerm);
  $("plannerBoard").innerHTML = cols.filter(([k]) => k !== "enrolled" || inTerm.some((c) => c.status === "enrolled")).map(([k, label]) => {
    const list = inTerm.filter((c) => c.status === k || (k === "planned" && c.status === "completed"));
    const cr = list.reduce((a, c) => a + (+c.credits || 0), 0);
    return `<div class="board-col"><div class="board-col-head"><span>${label}</span><span class="tag">${list.length} · ${cr} cr</span></div>
      ${list.map((c) => `<div class="course-card ${conflictIds.has(c.id) ? "has-conflict" : ""}" data-course="${c.id}">
        <div class="course-top">${c.priority ? `<span class="prio prio-${c.priority}">${PRIORITY[c.priority]}</span>` : ""}${(c.tags || []).map((t) => `<span class="req-tag">${{ pr: "PR", ulwr: "Writing", experiential: "Experiential", ilo8: "ILO #8" }[t]}</span>`).join("")}</div>
        <div class="course-title">${esc(c.title)}</div>
        <div class="course-meta">${[c.code, c.credits != null ? `${c.credits} cr` : null, c.graded === false ? "P/F" : null, c.exam_type].filter(Boolean).map(esc).join(" · ")}</div>
        ${c.meetings_text ? `<div class="course-meta">${esc(c.meetings_text)}</div>` : ""}
        ${c.notes ? `<div class="course-note">${esc(c.notes)}</div>` : ""}
        <select class="select status-select" data-move="${c.id}" aria-label="Move course">${Object.entries(STATUS_LABEL).map(([v, l]) => `<option value="${v}" ${v === c.status ? "selected" : ""}>${l}</option>`).join("")}</select>
      </div>`).join("") || `<div class="empty small">None</div>`}
    </div>`;
  }).join("");
  $$("[data-move]").forEach((s) => {
    s.onclick = (e) => e.stopPropagation();
    s.onchange = async () => {
      try { edu = await api(`/api/edu/courses/${s.dataset.move}`, { method: "PATCH", body: JSON.stringify({ status: s.value }) }); renderPlanner(); }
      catch (e) { toast(e.message); }
    };
  });
  $$("[data-course]").forEach((card) => (card.onclick = () => openCourse(edu.courses.find((c) => c.id === card.dataset.course))));
}
$("plannerTerm").onchange = () => { plannerTerm = $("plannerTerm").value; renderPlanner(); };
$("addCourse").onclick = () => openCourse(null);

// Course editor dialog
function openCourse(c) {
  editingCourse = c;
  const f = $("courseForm");
  $("courseDialogTitle").textContent = c ? "Edit course" : "Add course";
  $("courseTermSelect").innerHTML = edu.computed.profile.terms.map((t) => `<option>${esc(t)}</option>`).join("");
  $("courseGradeSelect").innerHTML = gradeOptions(c?.grade || "");
  const v = c || { term: plannerTerm, status: "considering", priority: 0, grade_status: "projected", tags: [] };
  ["title", "code", "crn", "term", "credits", "status", "priority", "grade_status", "meetings_text", "professor", "exam_type", "category", "waitlist", "notes", "experiential_credits"]
    .forEach((k) => { if (f.elements[k]) f.elements[k].value = v[k] ?? (k === "grade_status" ? "projected" : ""); });
  f.elements.grade.value = v.grade || "";
  f.elements.pf.checked = v.graded === false;
  f.elements.pf_eligible.checked = !!v.pf_eligible;
  ["pr", "ulwr", "ilo8", "experiential"].forEach((t) => (f.elements[`tag_${t}`].checked = (v.tags || []).includes(t)));
  $("courseDelete").classList.toggle("hidden", !c);
  $("courseDialog").showModal();
}
$("courseCancel").onclick = () => $("courseDialog").close();
$("courseForm").onsubmit = async (e) => {
  e.preventDefault();
  const f = $("courseForm"), val = (k) => f.elements[k].value.trim();
  const body = {
    title: val("title"), code: val("code"), crn: val("crn"), term: val("term"), credits: val("credits") || null,
    status: val("status"), priority: +val("priority"), grade: val("grade") || null, grade_status: val("grade") ? val("grade_status") : null,
    graded: !f.elements.pf.checked && !["P", "H", "CR"].includes(val("grade")), pf_eligible: f.elements.pf_eligible.checked,
    meetings_text: val("meetings_text"), professor: val("professor"), exam_type: val("exam_type"), category: val("category"),
    waitlist: val("waitlist"), notes: val("notes"), experiential_credits: val("experiential_credits") || null,
    tags: ["pr", "ulwr", "ilo8", "experiential"].filter((t) => f.elements[`tag_${t}`].checked),
  };
  try {
    edu = editingCourse
      ? await api(`/api/edu/courses/${editingCourse.id}`, { method: "PATCH", body: JSON.stringify(body) })
      : await api("/api/edu/courses", { method: "POST", body: JSON.stringify(body) });
    $("courseDialog").close(); toast(editingCourse ? "Course updated" : "Course added");
    render();
  } catch (err) { toast(err.message); }
};
$("courseDelete").onclick = async () => {
  if (!editingCourse || !confirm(`Delete ${editingCourse.title}?`)) return;
  edu = await api(`/api/edu/courses/${editingCourse.id}`, { method: "DELETE" });
  $("courseDialog").close(); toast("Course deleted"); render();
};

$("eduImportFile").onchange = async () => {
  const f = $("eduImportFile").files[0]; if (!f) return;
  try {
    const payload = JSON.parse(await f.text());
    const r = await api("/api/edu/import", { method: "POST", body: JSON.stringify(payload) });
    toast(`Imported ${r.imported} courses`); render();
  } catch (e) { toast(e instanceof SyntaxError ? "That file isn't valid JSON" : e.message); }
};

// ---------- Wiring ----------
$("signin").onclick = () => signInWithPopup(auth, new GoogleAuthProvider()).catch((e) => toast(e.message));
$("signout").onclick = () => signOut(auth);
$("sync").onclick = doSync;
$("range").onchange = () => render();
$("tx-search").oninput = renderTx;
$$("[data-link]").forEach((b) => (b.onclick = () => connect(b.dataset.link).catch((e) => toast(e.message))));
$("bank-files").onchange = () => {
  const fs = $("bank-files").files;
  $("bankLabel").textContent = fs.length ? (fs.length === 1 ? fs[0].name : `${fs.length} files selected`) : "Choose CSV files";
};
$("bank-upload").onclick = async () => {
  const files = [...$("bank-files").files];
  if (!files.length) return toast("Choose at least one CSV file");
  if (!backendReady) return toast("Backend not connected yet");
  $("bank-upload").disabled = true;
  let total = 0;
  try {
    for (const f of files) {
      const fd = new FormData(); fd.append("file", f);
      if ($("bank-account").value.trim()) fd.append("account", $("bank-account").value.trim());
      const r = await api("/api/import/csv", { method: "POST", body: fd });
      total += r.imported;
    }
    toast(`Imported ${total.toLocaleString()} transactions from ${files.length} file${files.length > 1 ? "s" : ""}`);
  } catch (e) { toast(e.message); }
  finally { $("bank-upload").disabled = false; }
};
$("venmo-file").onchange =() => ($("venmoLabel").textContent = $("venmo-file").files[0]?.name || "Choose a CSV file");
$("venmo-upload").onclick = async () => {
  const f = $("venmo-file").files[0];
  if (!f) return toast("Choose a CSV file first");
  const fd = new FormData(); fd.append("file", f);
  try { const r = await api("/api/venmo/upload", { method: "POST", body: fd }); toast(`Imported ${r.imported} Venmo transactions`); }
  catch (e) { toast(e.message); }
};

onAuthStateChanged(auth, (user) => {
  $("login").classList.toggle("hidden", !!user);
  $("app").classList.toggle("hidden", !user);
  if (user) render();
});

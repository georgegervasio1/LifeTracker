import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import { getAuth, GoogleAuthProvider, signInWithPopup, signOut, onAuthStateChanged }
  from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import { API_BASE, FIREBASE_CONFIG } from "./config.js";
import { viewSwap, enter, panelIn } from "./motion.js";
import { mountEduToday, loadToday, loadTasks } from "./edu-today.js";

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


// ---------- Sections & routing ----------
const SECTIONS = {
  finances: { title: "Finances", tabs: [
    ["networth", "Net Worth"], ["cash", "Cash & Cards"], ["investments", "Investments"],
    ["income", "Income"], ["taxes", "Taxes"], ["accounts", "Accounts & Imports"]] },
  health: { title: "Health", tabs: [] },   // dashboard + areas, see HEALTH below
  education: { title: "Education", tabs: [
    ["today", "Today"], ["tasks", "Tasks"], ["plan", "Academic plan"], ["syllabi", "Syllabi"], ["calendar", "Calendar"]] },
  goals: { title: "Goals", tabs: [] },
};

// Health opens on a dashboard of five areas; each area has its own tabs. A tab maps to a panel in index.html.
const UNSPLASH = (id) => `https://images.unsplash.com/${id}?auto=format&fit=crop&w=1600&q=70`;
const HEALTH = {
  lifting: { title: "Weight Lifting", tabs: [["planner", "Planner", "planner"], ["history", "History", "history"]],
             img: "photo-1646492169621-d8da8016354a", credit: ["Benjamin Brunner", "a-black-and-white-photo-of-a-barbell-in-a-gym-wcOGQeT1jIw"] },
  running: { title: "Running", tabs: [["runs", "Runs", "running"], ["race", "Race Training", "race"]],
             img: "photo-1502224562085-639556652f33", credit: ["lucas Favre", "silhouette-photo-of-a-person-running-on-road-JnoNcfFwrNA"] },
  sleep:   { title: "Sleep & Recovery", tabs: [["overview", "Overview", "overview"], ["calendar", "Calendar", "calendar"]],
             img: "photo-1585128719715-46776b56a0d1", credit: ["Julia", "nightstand-with-lamp-and-wooden-house-toX2sYnycCw"] },
  sports:  { title: "Sports", tabs: [["overview", "Overview", "sports"]],
             img: "photo-1780404850222-96b7b5a7d8a3", credit: ["Sanket Mishra", "a-man-holding-a-basketball-celebrates-on-an-outdoor-court-YDrqAcHEP_k"] },
  food:    { title: "Food", tabs: [["today", "Today", "food-today"], ["history", "History", "food-history"], ["goals", "Goals", "food-goals"]],
             img: "photo-1512621776951-a57141f2eefd", credit: ["Anna Pelzer", "bowl-of-vegetable-salads-IGfIGP5ONV0"] },
};
// Old links (and OAuth redirects) keep working
const HEALTH_LEGACY = { overview: "sleep/overview", planner: "lifting/planner", history: "lifting/history", running: "running/runs",
                        race: "running/race", calendar: "sleep/calendar" };

function parseHealth(parts) {
  let [area, tab] = parts;
  if (HEALTH_LEGACY[area]) [area, tab] = HEALTH_LEGACY[area].split("/");
  if (area === "connect") return { section: "health", area, tab: "", panel: "connect" };
  if (!HEALTH[area]) return { section: "health", area: "home", tab: "", panel: "dashboard" };
  const t = HEALTH[area].tabs.find(([k]) => k === tab) || HEALTH[area].tabs[0];
  return { section: "health", area, tab: t[0], panel: t[2] };
}

let routeQuery = new URLSearchParams();
const EDU_PLAN_SUBS = ["overview", "grades", "audit", "planner"];     // Education → Academic plan sections
let lastPanel = "";
function parseRoute() {
  const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
  const [section, tab] = path.split("/");
  routeQuery = new URLSearchParams(query || "");
  if (section === "health") return parseHealth(path.split("/").slice(1));
  if (!SECTIONS[section]) return { section: "home" };
  const tabs = SECTIONS[section].tabs;
  const alias = section === "finances" ? { overview: "networth", transactions: "cash", paychecks: "income", assistant: "networth" }[tab] : null;
  if (alias === "income" && !routeQuery.get("sub")) routeQuery.set("sub", "paychecks");
  if (section === "education" && EDU_PLAN_SUBS.includes(tab)) { routeQuery.set("sub", tab); return { section, tab: "plan" }; }   // old tab links
  const t = alias || tab;
  return { section, tab: tabs.some(([k]) => k === t) ? t : tabs[0]?.[0] };
}

function render() {
  if (!auth.currentUser) return;
  const route = parseRoute();
  const { section, tab } = route;
  lastRoute = route;
  $$(".route").forEach((r) => r.classList.toggle("active", r.dataset.route === section));
  $$(".section-links a").forEach((a) => a.classList.toggle("active", a.dataset.section === section));
  if (section === "health") return renderHealth(route);

  const cfg = SECTIONS[section];
  const hasTabs = cfg && cfg.tabs.length;
  $("subnav").classList.toggle("hidden", !hasTabs);
  if (hasTabs) {
    $("subnavTitle").textContent = cfg.title;
    $("subnavTabs").innerHTML = cfg.tabs.map(([k, label]) =>
      `<a href="#/${section}/${k}" class="${k === tab ? "active" : ""}">${label}</a>`).join("");
    const showControls = section === "finances" || (section === "health" && tab === "overview");
    $("sectionControls").classList.toggle("hidden", !showControls);
    $("range").classList.toggle("hidden", section === "finances" && tab !== "cash");
    const sub = EDU_PLAN_SUBS.includes(routeQuery.get("sub")) ? routeQuery.get("sub") : "overview";
    const panel = section === "education" && tab === "plan" ? sub : tab;
    $$(`[data-route="${section}"] .panel`).forEach((p) => p.classList.toggle("active", p.dataset.panel === panel));
    $("eduPlanNav").classList.toggle("hidden", !(section === "education" && tab === "plan"));
    $$("#eduPlanNav [data-sub]").forEach((a) => a.classList.toggle("active", a.dataset.sub === sub));
    if (section === "education") {          // slide toward the tab the user moved to
      const order = ["today", "tasks", ...EDU_PLAN_SUBS, "syllabi", "calendar"], [a, b] = [order.indexOf(lastPanel.split(":")[1]), order.indexOf(panel)];
      if (lastPanel.startsWith("education:") && a !== b) panelIn($$(`[data-route="education"] .panel.active`)[0], b > a ? 1 : -1);
    }
    lastPanel = `${section}:${panel}`;
    centerActiveTab();
  }
  $("askBtn").classList.toggle("hidden", section !== "finances");
  if (section !== "finances") $("chatDrawer").classList.add("hidden");
  window.scrollTo({ top: 0 });
  document.title = section === "home" ? "LifeTracker" : `${cfg.title} · LifeTracker`;
  load(section, tab).catch(showError);
}

function renderHealth(r) {
  const A = HEALTH[r.area];
  const tabs = A ? A.tabs : [];
  $("subnav").classList.toggle("hidden", r.area === "home");
  if (r.area !== "home") {
    $("subnavTitle").innerHTML = `<a class="subnav-crumb" href="#/health">Health</a>${esc(A ? A.title : "Connections")}`;
    $("subnavTabs").innerHTML = tabs.length > 1 ? tabs.map(([k, label]) =>
      `<a href="#/health/${r.area}/${k}" class="${k === r.tab ? "active" : ""}">${label}</a>`).join("") : "";
    $("sectionControls").classList.toggle("hidden", r.panel !== "overview");
    $("range").classList.remove("hidden");
    centerActiveTab();
  }
  $$('[data-route="health"] .panel').forEach((p) => p.classList.toggle("active", p.dataset.panel === r.panel));
  $("healthHeroWrap").classList.toggle("hidden", !A);
  if (A) {
    $("heroImg").src = UNSPLASH(A.img);
    $("heroTitle").textContent = A.title;
    $("heroSub").textContent = heroLines[r.area] || "";
    $("heroCredit").textContent = `Photo: ${A.credit[0]} / Unsplash`;
    $("heroCredit").href = `https://unsplash.com/photos/${A.credit[1]}`;
    $("healthHero").dataset.area = r.area;
  }
  $("askBtn").classList.add("hidden");
  $("chatDrawer").classList.add("hidden");
  window.scrollTo({ top: 0 });
  document.title = `${A ? A.title : r.area === "connect" ? "Connections" : "Health"} · LifeTracker`;
  loadHealth(r).catch(showError);
}

// View transitions: a dashboard card's photo morphs into its area's banner and back (viewSwap from motion.js).
let lastRoute = null;
function onHashChange() {
  const next = parseRoute(), prev = lastRoute;
  lastRoute = next;
  const morph = prev && prev.section === "health" && next.section === "health" && (prev.area === "home") !== (next.area === "home")
    && HEALTH[prev.area === "home" ? next.area : prev.area];
  if (!morph) return render();
  const area = prev.area === "home" ? next.area : prev.area;
  const card = () => document.querySelector(`.hd-card[data-area="${area}"] .hd-media`);
  const src = prev.area === "home" ? card() : $("heroMedia");
  if (src) src.style.viewTransitionName = "health-hero";
  const t = viewSwap(() => {
    if (src) src.style.viewTransitionName = "";
    render();
    const dst = next.area === "home" ? card() : $("heroMedia");
    if (dst) dst.style.viewTransitionName = "health-hero";
  });
  t.finished.finally(() => [card(), $("heroMedia")].forEach((el) => el && (el.style.viewTransitionName = "")));
}

// On narrow screens the tab row scrolls: keep the current tab in view and fade an edge while tabs are hidden past it.
function centerActiveTab() {
  const row = $("subnavTabs"), a = row.querySelector(".active");
  if (a) row.scrollLeft += a.getBoundingClientRect().left - row.getBoundingClientRect().left - (row.clientWidth - a.offsetWidth) / 2;
  markTabOverflow();
}
function markTabOverflow() {
  const row = $("subnavTabs");
  row.classList.toggle("more", row.scrollLeft + row.clientWidth < row.scrollWidth - 2);
  row.classList.toggle("more-left", row.scrollLeft > 2);
}
$("subnavTabs").addEventListener("scroll", markTabOverflow, { passive: true });
window.addEventListener("resize", markTabOverflow);

async function load(section, tab) {
  if (section === "home") return loadHome();
  if (!backendReady) return toast("Backend not connected yet");

  if (section === "education") return loadEducation(tab);
  if (section === "goals") return (await goalsModule()).loadGoals();
  if (section !== "finances") return;
  setChatPlace(tab);
  if (tab === "networth") return loadNetWorth();
  if (tab === "cash") return loadCash();
  if (tab === "investments") return loadInv();
  if (tab === "income") return loadIncome();
  if (tab === "taxes") return loadFinTaxes();
  if (tab === "accounts") return loadImports();
}

window.addEventListener("hashchange", onHashChange);
mountEduToday({ api, toast });

// Goals live in goals.js, loaded on first use.
let goalsMod = null;
async function goalsModule() {
  if (!goalsMod) {
    goalsMod = await import("./goals.js");
    goalsMod.initGoals({ api, $, esc, toast, renderMarkdown, usd0, showError });
  }
  return goalsMod;
}

// ---------- API helper (attaches Firebase ID token) ----------
async function api(path, opts = {}) {
  const token = await auth.currentUser.getIdToken();
  const res = await fetch(API_BASE + path, {
    ...opts,
    headers: { Authorization: `Bearer ${token}`,
               ...(opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 403 && data.error === "forbidden") throw new Error("This Google account doesn't have access to LifeTracker. Sign out and sign in with your LifeTracker account.");
  if (res.status === 401 && /token/.test(data.error || "")) throw new Error("Your sign-in expired. Sign out and sign in again.");
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

// Errors from page code (not the server) shouldn't surface as raw JavaScript messages.
function showError(e) {
  console.error(e);
  if (e instanceof TypeError) {
    toast(/fetch|network/i.test(e.message) ? "Can't reach the server. Check your connection and try again." : "Something went wrong loading this page. Try reloading.");
  } else toast(e.message);
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
  if (earn?.ready && parseRoute().tab === "paychecks") renderPaychecks();
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
      tension: 0.35, borderRadius: 6, maxBarThickness: 48, pointRadius: 0, pointHoverRadius: 4, spanGaps: true, ...(opts.ds || {}),
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
    const o = await api("/api/fin/overview");
    $("homeFinance").innerHTML = o.net_worth != null
      ? `${usd0.format(o.net_worth)}<small>net worth · ${usd0.format(o.month_spent || 0)} spent this month</small>`
      : `${usd0.format(o.month_spent || 0)}<small>spent this month</small>`;
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
  try {
    const stat = await (await goalsModule()).goalsHomeStat();
    $("homeGoals").classList.toggle("muted-stat", !stat);
    $("homeGoals").innerHTML = stat || "Set your first goal";
  } catch { $("homeGoals").textContent = "Set your first goal"; }
}

// ---------- Finances (net worth, cash & cards, investments, income, taxes, imports) ----------
const m0 = (v) => (v == null ? "—" : usd0.format(v));
const m2 = (v) => (v == null ? "—" : usd.format(v));
const signed = (v, f = m2) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${f(Math.abs(v))}`);
const pctTxt = (v, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : ""}${(+v).toFixed(d)}%`);
const fdate = (d, o = { month: "short", day: "numeric", year: "numeric" }) => (d ? new Date(d.slice(0, 10) + "T00:00").toLocaleDateString("en-US", o) : "—");
const SRC = { plaid: "Plaid", ubs_pdf: "UBS statement", chase_csv: "Chase CSV", venmo_csv: "Venmo CSV", csv: "CSV", manual: "Entered by you" };
let finAccounts = [];

function subtabs(elId, items, active, tab, param) {
  $(elId).innerHTML = items.map(([k, label]) =>
    `<a role="tab" class="subtab ${k === active ? "active" : ""}" href="#/finances/${tab}?${param}=${encodeURIComponent(k)}">${esc(label)}</a>`).join("");
}

function drawLines(id, labels, series, fmt = { tip: (v) => usd.format(v), tick: (v) => usd0.format(v) }) {
  charts[id]?.destroy();
  const k = chartColors();
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  charts[id] = new Chart($(id), {
    type: "line",
    data: { labels, datasets: series.map((s, i) => ({
      label: s.label, data: s.data, borderColor: s.color || (i ? cssVar("--text-3") : k.accent), borderWidth: 2,
      borderDash: s.dash ? [5, 4] : [], pointRadius: 3, pointHoverRadius: 5, tension: 0.25, fill: false, spanGaps: true,
      backgroundColor: s.color || (i ? cssVar("--text-3") : k.accent) })) },
    options: {
      aspectRatio: 2.2,
      plugins: { legend: { display: series.length > 1, labels: { color: k.text, boxWidth: 10, boxHeight: 10, usePointStyle: true } },
                 tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt.tip(c.parsed.y)}` } } },
      scales: {
        y: { border: { display: false }, ticks: { color: k.text, maxTicksLimit: 5, callback: (v) => fmt.tick(v) }, grid: { color: k.line } },
        x: { border: { display: false }, ticks: { color: k.text, maxTicksLimit: 7, maxRotation: 0 }, grid: { display: false } },
      },
    },
  });
}

function signedBars(elId, rows) {
  const top = Math.max(1, ...rows.map((r) => Math.abs(r.value || 0)));
  $(elId).innerHTML = rows.length ? rows.map((r) => `<div class="bar-row">
      <span class="bar-label">${esc(r.label)}</span>
      <span class="bar-track"><span class="bar-fill ${r.value < 0 ? "neg-fill" : ""}" style="display:block;width:${Math.max(1.5, Math.abs(r.value) / top * 100)}%"></span></span>
      <span class="bar-value ${r.value < 0 ? "neg" : ""}">${signed(r.value, m0).replace("+", "")}</span></div>`).join("")
    : `<div class="empty">No balances yet.</div>`;
}

// ---- Net worth
async function loadNetWorth() {
  const o = await api("/api/fin/overview");
  finAccounts = o.accounts;
  $("nwTotal").textContent = m0(o.net_worth);
  $("nwSub").textContent = `${m0(o.assets)} in assets − ${m0(o.liabilities)} in debts` +
    (o.missing_balances.length ? ` · no balance yet for ${o.missing_balances.join(", ")}` : "");
  const tax = o.taxes || {};
  $("nwKpis").innerHTML = `
    <div class="kpi"><span class="kpi-label">Assets</span><span class="kpi-value">${m0(o.assets)}</span><span class="kpi-sub">${o.accounts.filter((a) => a.balance != null && !a.liability).length} accounts</span></div>
    <div class="kpi"><span class="kpi-label">Debts</span><span class="kpi-value">${m0(o.liabilities)}</span><span class="kpi-sub">Credit cards and loans</span></div>
    <a class="kpi kpi-link" href="#/finances/cash"><span class="kpi-label">Spent this month</span><span class="kpi-value">${m0(o.month_spent)}</span><span class="kpi-sub">Cash & cards →</span></a>
    <a class="kpi kpi-link" href="#/finances/taxes"><span class="kpi-label">${new Date().getFullYear()} taxes</span><span class="kpi-value ${tax.settle < 0 ? "neg" : "pos"}">${tax.settle == null ? "—" : m0(Math.abs(tax.settle))}</span><span class="kpi-sub">${tax.settle == null ? "Estimate unavailable" : tax.settle < 0 ? "Estimated to owe →" : "Estimated refund →"}</span></a>`;
  const h = o.history.filter((x) => x.accounts);
  if (h.length) drawChart("chart-nw", "line", h.map((x) => fdate(x.date, { month: "short", year: "2-digit" })), h.map((x) => x.net_worth));
  signedBars("nwTypes", o.by_type);
  const link = (a) => ["investment", "retirement"].includes(a.group) ? `#/finances/investments?acct=${a.account}` : `#/finances/cash?acct=${a.account}`;
  $("nwAccounts").innerHTML = o.accounts.map((a) => `<div class="acct-row">
      <a href="${link(a)}" class="acct-name"><strong>${esc(a.name)}</strong><span class="tag">${esc(a.type_label)}</span></a>
      <span class="acct-src">${a.as_of ? `${fdate(a.as_of)} · ${SRC[a.src] || a.src}${a.stale_days > 35 ? ` <span class="warn">${a.stale_days} days old</span>` : ""}` : "No balance yet"}</span>
      <span class="acct-bal ${a.liability && a.balance ? "neg" : ""}">${a.balance == null ? "—" : (a.liability ? "−" : "") + m2(Math.abs(a.balance))}</span>
      ${a.src !== "plaid" && ["cash", "credit"].includes(a.group) ? `<button class="text-btn" data-bal="${a.account}">Update</button>` : "<span></span>"}
    </div>`).join("");
  bindBalanceButtons();
  $("nwMoves").innerHTML = o.changes.length ? `<div class="move-head"><span>Month</span><span>Change</span><span>Investments</span><span>New money</span><span>Everything else</span></div>` +
    o.changes.filter((c) => c.change || c.investment_returns || c.new_money || c.saving_and_spending || c.newly_tracked).map((c) => `<div class="move-row"><span>${fdate(c.month + "-01", { month: "short", year: "numeric" })}</span><strong class="${c.change < 0 ? "neg" : ""}">${signed(c.change - (c.newly_tracked || 0), m0)}</strong><span>${signed(c.investment_returns, m0)}</span><span>${signed(c.new_money, m0)}</span><span>${signed(c.saving_and_spending, m0)}</span></div>${c.newly_tracked ? `<div class="move-note">${signed(c.newly_tracked, m0)} from accounts first tracked this month, not counted as a change</div>` : ""}`).join("") +
    `<p class="hint">Investments = market change + dividends from your statements. New money = trust distributions. Everything else = saving, spending and transfers from accounts without a balance yet.</p>`
    : `<div class="empty">Needs two months of balances.</div>`;
  const mv = o.investments.movers;
  $("nwMoversTitle").textContent = mv.daily ? "Today's movers" : "Movers";
  $("nwMovers").innerHTML = `<div class="inv-total"><strong>${m0(o.investments.total)}</strong><span>invested across accounts</span></div>` +
    (mv.items.length ? `<p class="hint">${esc(mv.label)}: ${signed(mv.total_change, m0)}</p>` + mv.items.map((i) => `<div class="mini-row"><span><strong>${esc(i.symbol)}</strong></span><span class="${i.change_pct < 0 ? "neg" : "pos"}">${pctTxt(i.change_pct)} · ${signed(i.change_value, m0)}</span></div>`).join("")
      : `<p class="hint">Movers appear once there are two price snapshots (two statements, or daily with Plaid).</p>`);
}

function bindBalanceButtons() {
  $$("[data-bal]").forEach((b) => (b.onclick = () => {
    const a = finAccounts.find((x) => (x.account || x.id) === b.dataset.bal) || {};
    $("balForm").reset();
    $("balForm").dataset.account = b.dataset.bal;
    $("balTitle").textContent = `Update ${a.name || "balance"}`;
    $("balForm").elements.date.value = iso(new Date());
    $("balDialog").showModal();
  }));
}
$("balCancel").onclick = () => $("balDialog").close();
$("balForm").onsubmit = async (e) => {
  e.preventDefault();
  const f = $("balForm");
  try {
    await api("/api/fin/balances", { method: "POST", body: JSON.stringify({ account: f.dataset.account, balance: +f.elements.balance.value, date: f.elements.date.value }) });
    $("balDialog").close(); toast("Balance saved"); render();
  } catch (err) { toast(err.message); }
};

// ---- Cash & cards
let cashData = null;
async function loadCash() {
  const acct = routeQuery.get("acct") || "all";
  const days = +$("range").value;
  cashData = await api(`/api/fin/cash?account=${encodeURIComponent(acct)}&start=${iso(new Date(Date.now() - days * 864e5))}&end=${iso(new Date())}`);
  finAccounts = cashData.accounts.map((a) => ({ ...a, account: a.id }));
  const tabs = [["all", "All"], ...cashData.accounts.filter((a) => a.type !== "p2p").map((a) => [a.id, a.name]), ["p2p", "Venmo / Zelle"]];
  subtabs("cashTabs", tabs, acct, "cash", "acct");
  const b = cashData.balance, a = cashData.account;
  $("cashKpis").innerHTML = `
    <div class="kpi"><span class="kpi-label">${a ? (a.liability ? "Balance owed" : "Balance") : "Spent"}</span><span class="kpi-value">${a ? (b ? m2(Math.abs(b.value)) : "—") : m0(cashData.spent)}</span>
      <span class="kpi-sub">${a ? (b ? `${fdate(b.date)} · ${SRC[b.src] || b.src}` : "") : `Last ${days} days`}${a && a.id ? ` · <button class="text-btn" data-bal="${a.id}">Update</button>` : ""}</span></div>
    <div class="kpi"><span class="kpi-label">${a ? "Spent" : "Money in"}</span><span class="kpi-value">${m0(a ? cashData.spent : cashData.money_in)}</span><span class="kpi-sub">Last ${days} days</span></div>
    <div class="kpi"><span class="kpi-label">${a ? "Money in" : "Net cash flow"}</span><span class="kpi-value">${a ? m0(cashData.money_in) : signed(cashData.money_in - cashData.spent, m0)}</span></div>
    <div class="kpi"><span class="kpi-label">Transfers</span><span class="kpi-value">${m0(Math.abs(cashData.transfers))}</span><span class="kpi-sub">Between your accounts, not spending</span></div>`;
  bindBalanceButtons();
  $("cashCatMeta").textContent = `${Object.keys(cashData.by_category).length} categories`;
  barRows("cashCats", Object.entries(cashData.by_category));
  const months = Object.keys(cashData.by_month);
  drawChart("chart-cash", "bar", months.map((m) => new Date(m + "-01T00:00").toLocaleDateString("en-US", { month: "short" })), Object.values(cashData.by_month));
  allTx = cashData.transactions;
  txShown = 50;
  renderTx();
  $("cashNote").textContent = acct === "p2p"
    ? "Venmo comes from monthly CSV uploads; Zelle payments show up in Chase checking. Each payment counts once even if it appears in both."
    : "A purchase that's in both a CSV and Plaid counts once. Card payments and transfers between your accounts aren't spending.";
}

let txShown = 50;
function renderTx() {
  const q = $("tx-search").value.toLowerCase();
  const names = Object.fromEntries((cashData?.accounts || []).map((a) => [a.id, a.name]));
  const rows = allTx.filter((t) => !q || `${t.merchant} ${t.category} ${names[t.account] || t.source} ${t.note || ""}`.toLowerCase().includes(q));
  $("tx-count").textContent = rows.length.toLocaleString();
  const net = rows.reduce((a, t) => a + t.amount, 0);
  $("tx-total").textContent = net >= 0 ? `${usd.format(net)} out` : `+${usd.format(-net)} in`;
  $("tx-more").classList.toggle("hidden", rows.length <= txShown);
  $("tx-more").textContent = `Show more (${(rows.length - txShown).toLocaleString()} left)`;
  $("tx-body").innerHTML = rows.slice(0, txShown).map((t) => `
    <tr><td>${fdate(t.date)}</td>
      <td>${esc(t.merchant)}${t.pending ? '<span class="pending">Pending</span>' : ""}${t.transfer_pair ? '<span class="pending">Matched transfer</span>' : ""}</td>
      <td class="tag">${esc(t.category)}</td><td class="tag">${esc(names[t.account] || t.source || "")}<span class="src-dot" title="${esc(SRC[t.src] || "")}"> · ${esc(SRC[t.src] || "")}</span></td>
      <td class="num ${t.amount < 0 ? "pos" : ""}">${t.amount < 0 ? "+" : ""}${usd.format(Math.abs(t.amount))}</td></tr>`).join("")
    || `<tr><td colspan="5" class="empty">No transactions in this range. Import a Chase CSV or connect Plaid in Accounts & Imports.</td></tr>`;
}

// ---- Investments
async function loadInv() {
  const acct = routeQuery.get("acct") || "all";
  const v = await api(`/api/fin/investments?account=${encodeURIComponent(acct)}`);
  const tabs = [["all", "All accounts"], ...v.accounts.map((a) => [a.id, a.name])];
  subtabs("invTabs", tabs, v.account || "all", "investments", "acct");
  const H = v.holdings, single = !!v.account, A = v.accounts.find((a) => a.id === v.account);
  if (!H.holdings.length && !H.cash) {
    $("invBody").innerHTML = `<div class="card empty-state"><h3>No investment data yet</h3><p>Upload UBS statements in Accounts & Imports, or connect UBS through Plaid.</p><a class="btn btn-primary" href="#/finances/accounts">Go to Accounts & Imports</a></div>`;
    return;
  }
  const asOf = Object.values(H.as_of).sort((a, b) => b.date.localeCompare(a.date))[0];
  const gain = H.holdings.reduce((s, h) => s + (h.unrealized || 0), 0);
  const eai = H.holdings.reduce((s, h) => s + (h.eai || 0), 0);
  const ytd = single ? v.performance.ytd : null;
  const roth = v.roth;
  $("invBody").innerHTML = `
    <div class="inv-head"><div><p class="eyebrow">${esc(A ? `${A.name} · ${A.type_label}${A.tax === "roth" ? " · tax-free growth" : A.tax === "pre_tax" ? " · pre-tax" : " · taxable"}` : "All investment accounts")}</p>
      <h2 class="nw-total">${m2(H.total)}</h2><p class="lede-sm">As of ${fdate(asOf?.date)} · ${SRC[asOf?.src] || ""}</p></div></div>
    <div class="kpis">
      <div class="kpi"><span class="kpi-label">Unrealized gain</span><span class="kpi-value ${gain < 0 ? "neg" : "pos"}">${signed(gain, m0)}</span><span class="kpi-sub">On ${m0(H.holdings.reduce((s, h) => s + (h.cost_basis || 0), 0))} cost</span></div>
      <div class="kpi"><span class="kpi-label">${single ? "Year to date" : "Cash"}</span><span class="kpi-value ${single && ytd.ytd_pct < 0 ? "neg" : ""}">${single ? pctTxt(ytd.ytd_pct) : m0(H.cash)}</span><span class="kpi-sub">${single ? `S&P 500 ${pctTxt(ytd.sp500_ytd)} · time-weighted, approx.` : `${(H.cash / H.total * 100).toFixed(1)}% of the total`}</span></div>
      <div class="kpi"><span class="kpi-label">Est. annual income</span><span class="kpi-value">${m0(eai)}</span><span class="kpi-sub">${v.dividends.received ? `${m2(v.dividends.received)} received on file` : ""}</span></div>
      <div class="kpi"><span class="kpi-label">Fund fees / year</span><span class="kpi-value">${v.fees.funds.length && v.fees.unknown.length === v.fees.funds.length ? "—" : m2(v.fees.fund_cost_per_year)}</span><span class="kpi-sub">${v.fees.weighted_expense_ratio != null ? `${v.fees.weighted_expense_ratio.toFixed(3)}% average expense ratio` : ""}${v.fees.unknown.length ? `${v.fees.weighted_expense_ratio != null ? " · " : ""}${v.fees.unknown.length} fund${v.fees.unknown.length > 1 ? "s" : ""} unknown` : ""}</span></div>
    </div>
    <div class="grid-2">
      <div class="card"><div class="card-head"><h3>${single ? "Return vs S&P 500" : "Value over time"}</h3><span class="card-meta">${single ? "Year to date, at each statement" : "Statements + Plaid"}</span></div><canvas id="chart-inv"></canvas>
        ${single ? `<p class="hint">Time-weighted return from each statement's own figures (deposits removed). The S&P 500 line is the index return UBS prints on each statement; daily data comes with Plaid.</p>` : `<div id="invPerfTable"></div>`}</div>
      <div class="card"><div class="card-head"><h3>Allocation</h3><span class="card-meta">Look-through by fund type</span></div><div id="invAlloc"></div>
        ${v.allocation.flags.map((f) => `<p class="hint flag">${esc(f)}</p>`).join("")}</div>
    </div>
    <div class="card card-flush"><div class="card-head padded"><h3>Holdings</h3><span class="card-meta">Tap a holding for its tax lots</span></div>
      <div class="table-scroll"><table class="table hold-table"><thead><tr><th>Security</th><th class="num">Shares</th><th class="num">Price</th><th class="num">Value</th><th class="num">Cost</th><th class="num">Gain</th><th class="num">Weight</th><th class="num">Yield</th></tr></thead>
      <tbody id="invHoldings"></tbody></table></div></div>
    <div class="grid-2">
      <div class="card"><div class="card-head"><h3>${v.movers.daily ? "Today's movers" : "Movers"}</h3><span class="card-meta">${esc(v.movers.label || "")}</span></div><div id="invMovers"></div></div>
      <div class="card"><div class="card-head"><h3>Fees</h3><span class="card-meta">Fund expense ratios</span></div><div id="invFees"></div></div>
    </div>
    <div class="grid-2">
      <div class="card"><div class="card-head"><h3>Dividends & interest</h3><span class="card-meta">${m2(v.dividends.projected_annual)} projected a year</span></div><div id="invDivs"></div></div>
      ${roth ? `<div class="card"><div class="card-head"><h3>Roth IRA details</h3><span class="card-meta">Contributions, conversions, 5-year clocks</span></div><div id="invRoth"></div></div>`
             : single && A?.tax === "pre_tax" ? `<div class="card"><div class="card-head"><h3>Pre-tax money</h3></div><p>${esc(v.pre_tax?.note || "")}</p><p class="hint">Rollovers and Roth conversions show up here from the 1099-R and 5498 forms on file. See Taxes for what a withdrawal would cost.</p></div>`
             : `<div class="card"><div class="card-head"><h3>Accounts</h3></div><div id="invAccts"></div></div>`}
    </div>
    ${single && v.statements?.length ? `<div class="card card-flush"><div class="card-head padded"><h3>Statements on file</h3><a class="link-sm" href="#/finances/accounts">Reconciliation →</a></div>
      <div class="table-scroll"><table class="table"><thead><tr><th>Period</th><th class="num">Start</th><th class="num">Deposits</th><th class="num">Income</th><th class="num">Market change</th><th class="num">End</th></tr></thead>
      <tbody>${v.statements.slice().reverse().map((s) => `<tr><td>${esc(s.period_label)}</td><td class="num">${m2(s.opening_value)}</td><td class="num">${m2(s.change?.deposits)}</td><td class="num">${m2(s.change?.div_int)}</td><td class="num ${(s.change?.market_change || 0) < 0 ? "neg" : ""}">${signed(s.change?.market_change)}</td><td class="num"><strong>${m2(s.closing_value)}</strong></td></tr>`).join("")}</tbody></table></div></div>` : ""}`;

  if (single && ytd.points.length) {
    drawLines("chart-inv", ytd.points.map((p) => fdate(p.date, { month: "short", day: "numeric" })),
      [{ label: A.name, data: ytd.points.map((p) => p.ytd_pct) }, { label: "S&P 500", data: ytd.points.map((p) => p.sp500_ytd), dash: true }],
      { tip: (x) => pctTxt(x), tick: (x) => `${x}%` });
  } else if (!single) {
    const vs = v.value_series;
    if (vs.length) drawChart("chart-inv", "line", vs.map((p) => fdate(p.date, { month: "short", day: "numeric" })), vs.map((p) => p.value));
    $("invPerfTable").innerHTML = `<div class="perf-rows">${v.performance.by_account.map((p) => `<a class="perf-row" href="#/finances/investments?acct=${p.account}"><span>${esc(p.name)}</span><span class="${p.ytd_pct < 0 ? "neg" : "pos"}">${pctTxt(p.ytd_pct)}</span><span class="muted">S&P ${pctTxt(p.sp500_ytd)}</span></a>`).join("")}</div><p class="hint">Year-to-date, time-weighted. Open an account for its chart.</p>`;
  }
  const alloc = (rows) => rows.filter((r) => r.value).map((r) => `<div class="bar-row"><span class="bar-label">${esc(r.label)}</span><span class="bar-track"><span class="bar-fill" style="display:block;width:${Math.max(1.5, r.pct)}%"></span></span><span class="bar-value">${r.pct.toFixed(1)}%</span></div>`).join("");
  $("invAlloc").innerHTML = `<h4 class="mini-h">Asset class</h4>${alloc(v.allocation.by_asset)}${v.allocation.by_asset.filter((r) => ["Bonds", "International stocks", "Crypto"].includes(r.label) && !r.value).length === 3 && !v.allocation.by_asset.some((r) => r.label === "Unclassified" && r.value) ? `<p class="hint">Bonds, international and crypto: none held</p>` : ""}<h4 class="mini-h">By fund type</h4>${alloc(v.allocation.by_style)}`;
  $("invHoldings").innerHTML = H.holdings.map((h, i) => `
    <tr class="hold-row" data-lots="${i}"><td><strong>${esc(h.symbol)}</strong><span class="hold-name">${esc(h.name)}${!single && h.accounts?.length > 1 ? ` · ${h.accounts.length} accounts` : ""}</span></td>
      <td class="num">${(+h.quantity).toLocaleString()}</td><td class="num">${m2(h.price)}</td><td class="num"><strong>${m2(h.value)}</strong></td>
      <td class="num">${m2(h.cost_basis)}</td><td class="num ${h.unrealized < 0 ? "neg" : "pos"}">${signed(h.unrealized)}</td>
      <td class="num">${h.weight_pct?.toFixed(1)}%</td><td class="num">${h.yield_pct != null ? h.yield_pct.toFixed(2) + "%" : "—"}</td></tr>
    <tr class="lots-row hidden" id="lots-${i}"><td colspan="8">${(h.lots || []).length ? `<table class="lots"><thead><tr><th>Bought</th>${single ? "" : "<th>Account</th>"}<th class="num">Shares</th><th class="num">Cost</th><th class="num">Value</th><th class="num">Gain</th><th>Term</th></tr></thead><tbody>
      ${h.lots.map((l) => `<tr><td>${fdate(l.trade_date)}</td>${single ? "" : `<td>${esc(l.account_name)}</td>`}<td class="num">${l.quantity}</td><td class="num">${m2(l.cost_basis)}</td><td class="num">${m2(l.value)}</td><td class="num ${l.unrealized < 0 ? "neg" : "pos"}">${signed(l.unrealized)}</td>
        <td>${l.term === "LT" ? "Long-term" : `Short-term · long-term ${fdate(l.long_term_on)}${l.days_to_lt <= 60 ? ` <span class="warn">in ${l.days_to_lt} days</span>` : ""}`}</td></tr>`).join("")}</tbody></table>` : `<p class="hint">No tax lots from this source yet.</p>`}</td></tr>`).join("")
    + (H.cash ? `<tr><td><strong>Cash</strong><span class="hold-name">Sweep and cash balances</span></td><td></td><td></td><td class="num"><strong>${m2(H.cash)}</strong></td><td></td><td></td><td class="num">${(H.cash / H.total * 100).toFixed(1)}%</td><td></td></tr>` : "");
  $$(".hold-row").forEach((r) => (r.onclick = () => $(`lots-${r.dataset.lots}`).classList.toggle("hidden")));
  const mv = v.movers;
  $("invMovers").innerHTML = mv.items.length ? mv.items.map((i) => `<div class="mini-row"><span><strong>${esc(i.symbol)}</strong> <span class="tag">${m2(i.from)} → ${m2(i.to)}</span></span><span class="${i.change_pct < 0 ? "neg" : "pos"}">${pctTxt(i.change_pct)} · ${signed(i.change_value, m0)}</span></div>`).join("") +
      `<p class="hint">${mv.daily ? "From the last two Plaid syncs." : "Statement to statement until Plaid adds daily prices."}</p>` : `<p class="hint">Needs two price snapshots.</p>`;
  const F = v.fees;
  $("invFees").innerHTML = F.funds.map((f) => `<div class="mini-row"><span><strong>${esc(f.symbol)}</strong> <span class="tag">${f.expense_ratio != null ? f.expense_ratio + "%" : "unknown"}</span></span><span>${m2(f.annual_cost)} / yr</span></div>`).join("") +
    `<div class="mini-row total"><span>All funds</span><strong>${F.funds.length && F.unknown.length === F.funds.length ? "—" : m2(F.fund_cost_per_year)} / yr</strong></div>
    <p class="hint">${esc(F.note)} ${F.account_fees.length ? `Account fees on file: ${m2(F.account_fees_total)}.` : "No account fees in the activity on file."} ${F.funds.some((f) => f.as_of) ? `Expense ratios from fund pages as of ${fdate(F.funds.find((f) => f.as_of).as_of)} (${new Set(F.funds.map((f) => f.source).filter(Boolean)).size} sources).` : ""}${F.unknown.length ? ` Not yet known for ${F.unknown.map(esc).join(", ")}, so they aren't in the total.` : ""}</p>`;
  const D = v.dividends, months = Object.entries(D.by_month).slice(-12);
  const topD = Math.max(1, ...months.map(([, x]) => x));
  $("invDivs").innerHTML = `<div class="div-bars">${months.map(([m, x]) => `<div class="div-bar"><i style="height:${Math.max(4, x / topD * 100)}%"></i><span>${new Date(m + "-01T00:00").toLocaleDateString("en-US", { month: "short" })}</span><em>${m0(x)}</em></div>`).join("")}</div>
    <div class="mini-row"><span>Received (on file)</span><strong>${m2(D.received)}</strong></div><div class="mini-row"><span>Projected next 12 months</span><strong>${m2(D.projected_annual)}</strong></div><p class="hint">${esc(D.note)}</p>`;
  if (roth) {
    $("invRoth").innerHTML = `<div class="mini-row"><span>Value</span><strong>${m2(roth.value)}</strong></div>
      <div class="mini-row"><span>Your contributions (on file)</span><span>${m2(roth.contributions)}</span></div>
      ${roth.conversions.map((c) => `<div class="mini-row"><span>Converted in ${c.year}</span><span>${m2(c.amount)} · penalty-free ${fdate(c.penalty_free_from)}</span></div>`).join("")}
      <div class="mini-row"><span>Earnings</span><strong class="pos">${m2(roth.earnings)}</strong></div>
      <h4 class="mini-h">Contributions by tax year</h4>
      ${roth.by_year.map((y) => `<div class="mini-row"><span>${y.year}</span><span>${m2(y.contributed)}${y.limit ? ` of ${m0(y.limit)} limit` : ""}${y.converted ? ` · ${m0(y.converted)} converted` : ""}</span></div>`).join("")}
      <p class="hint">${esc(roth.five_year_clock.note)}${roth.five_year_clock.qualified_from ? ` Earnings clock: tax-free from ${fdate(roth.five_year_clock.qualified_from)} (and 59½).` : ""}</p>
      ${roth.missing_years_note ? `<p class="hint flag">${esc(roth.missing_years_note)}</p>` : ""}`;
  } else if ($("invAccts")) {
    $("invAccts").innerHTML = v.accounts.map((a) => { const p = v.performance.by_account?.find((x) => x.account === a.id);
      return `<a class="mini-row" href="#/finances/investments?acct=${a.id}"><span><strong>${esc(a.name)}</strong> <span class="tag">${esc(a.type_label)}</span></span><span>${p?.ytd_pct != null ? pctTxt(p.ytd_pct) + " YTD" : ""}</span></a>`; }).join("");
  }
}

// ---- Income
let incData = null, editingJob = null;
async function loadIncome() {
  incData = await api(`/api/fin/income?year=${new Date().getFullYear()}`);
  const tabs = [["overview", "Overview"], ...incData.jobs.map((j) => [j.id, j.employer]), ["trust", "Trust distributions"], ["investment", "Investment income"]];
  const sub = tabs.some(([k]) => k === routeQuery.get("sub")) ? routeQuery.get("sub") : "overview";   // unknown or old links land on Overview
  subtabs("incTabs", tabs, sub, "income", "sub");
  $("incPay").classList.toggle("hidden", sub !== "paychecks");
  $("incBody").classList.toggle("hidden", sub === "paychecks");
  if (sub === "paychecks") return loadPaychecks();
  const job = incData.jobs.find((j) => j.id === sub);
  if (sub === "overview") {
    const years = Object.entries(incData.by_year);
    const cats = [...new Set(years.flatMap(([, d]) => Object.keys(d)))];
    $("incBody").innerHTML = `<div class="inc-head"><h2 class="title">Income</h2><button class="btn btn-secondary btn-sm" id="addJob">Add a job</button></div>
      <div class="card card-flush"><div class="card-head padded"><h3>By year</h3><span class="card-meta">Everything that came in, by kind</span></div>
        <div class="table-scroll"><table class="table"><thead><tr><th>Year</th>${cats.map((c) => `<th class="num">${esc(c)}</th>`).join("")}<th class="num">Total</th></tr></thead>
        <tbody>${years.map(([y, d]) => `<tr><td><strong>${y}</strong></td>${cats.map((c) => `<td class="num">${d[c] ? m2(d[c]) : "—"}</td>`).join("")}<td class="num"><strong>${m2(Object.values(d).reduce((a, b) => a + b, 0))}</strong></td></tr>`).join("") || `<tr><td colspan="9" class="empty">No income on file yet</td></tr>`}</tbody></table></div></div>
      <div class="job-grid">${incData.jobs.map((j) => `<a class="card job-card" href="#/finances/income?sub=${j.id}"><span class="eyebrow">${esc(j.role || "Job")}</span><h3>${esc(j.employer)}</h3>
        <p>${j.has_data ? `${m2(j.gross)} gross · ${j.checks} paychecks${j.year ? ` · ${j.year}` : ""}` : esc(j.notes || "No pay data yet")}</p>
        <span class="link">${j.link === "earnings" ? "Paychecks and withholding →" : "Open →"}</span></a>`).join("")}
        <a class="card job-card" href="#/finances/income?sub=trust"><span class="eyebrow">Trust</span><h3>Trust distributions</h3><p>${m2(incData.trust_total)} this year</p><span class="link">Open →</span></a></div>
      <p class="hint">Years with no income on file are left out. Pay for other jobs arrives when Plaid links the bank it was paid into, or when you add pay stubs.</p>`;
    $("addJob").onclick = () => openJob(null);
  } else if (job) {
    $("incBody").innerHTML = `<div class="inc-head"><div><p class="eyebrow">${esc(job.role || "Job")}</p><h2 class="title">${esc(job.employer)}</h2>
        <p class="lede-sm">${[job.start && `${fdate(job.start)} – ${job.end ? fdate(job.end) : "present"}`, job.work_state && `worked in ${job.work_state}`].filter(Boolean).join(" · ")}</p></div>
        <button class="btn btn-secondary btn-sm" id="editJob">Edit / add pay</button></div>
      ${(job.pay || []).length ? `<div class="card card-flush"><div class="table-scroll"><table class="table"><thead><tr><th>Pay date</th><th class="num">Gross</th><th class="num">Federal</th><th class="num">State</th><th class="num">Net</th></tr></thead>
        <tbody>${job.pay.map((p) => `<tr><td>${fdate(p.date)}</td><td class="num">${m2(p.gross)}</td><td class="num">${m2(p.federal)}</td><td class="num">${m2(p.state)}</td><td class="num"><strong>${m2(p.net)}</strong></td></tr>`).join("")}</tbody></table></div></div>`
        : `<div class="card empty-state"><h3>No pay data yet</h3><p>${esc(job.notes || "")} Payroll deposits will be picked up automatically once Plaid links the bank you were paid into, or add pay stubs here.</p></div>`}`;
    $("editJob").onclick = () => openJob(job);
  } else if (sub === "trust") {
    $("incBody").innerHTML = `<div class="inc-head"><div><p class="eyebrow">Income</p><h2 class="title">Trust distributions</h2><p class="lede-sm">Distributions found in your statements</p></div></div>
      <div class="card">${incData.trust.map((t) => `<div class="mini-row"><span><strong>${fdate(t.date)}</strong> <span class="tag">into ${esc(t.account)} · ${esc(t.description)}</span></span><strong>${m2(t.amount)}</strong></div>`).join("") || `<p class="hint">None on file.</p>`}
        <p class="hint">Treated as taxable ordinary income on the Taxes page (change the taxable share there once the 1099-R or K-1 arrives). It's new money, not a transfer between your accounts, so it counts as income here and in net worth changes.</p>
        <a class="link-sm" href="#/finances/taxes">See the tax on it →</a></div>`;
  } else if (sub === "investment") {
    const I = incData.investment_income;
    $("incBody").innerHTML = `<div class="inc-head"><div><p class="eyebrow">Income</p><h2 class="title">${m2(I.total)} in dividends & interest</h2><p class="lede-sm">This year, all accounts. Only the brokerage account's is taxed now; IRA income isn't taxed until withdrawn (and never, from the Roth, once qualified).</p></div></div>
      <div class="grid-2"><div class="card"><div class="card-head"><h3>By account</h3></div>${Object.entries(I.by_account).map(([k, x]) => `<div class="mini-row"><span>${esc(k)}</span><strong>${m2(x)}</strong></div>`).join("")}</div>
      <div class="card"><div class="card-head"><h3>Payments</h3></div>${I.items.map((x) => `<div class="mini-row"><span>${fdate(x.date)} <span class="tag">${esc(x.symbol || x.type)} · ${esc(x.account)}</span></span><span>${m2(x.amount)}</span></div>`).join("")}</div></div>`;
  }
}

function openJob(job) {
  editingJob = job;
  const f = $("jobForm"); f.reset();
  $("jobTitle").textContent = job ? job.employer : "Add a job";
  for (const k of ["employer", "role", "start", "end", "salary", "work_state", "notes"]) f.elements[k].value = job?.[k] ?? "";
  $("jobDelete").classList.toggle("hidden", !job || job.link === "earnings");
  $("jobDialog").showModal();
}
$("jobCancel").onclick = () => $("jobDialog").close();
$("jobDelete").onclick = async () => {
  if (!editingJob || !confirm(`Delete ${editingJob.employer}?`)) return;
  try { await api(`/api/fin/income/jobs/${editingJob.id}`, { method: "DELETE" }); $("jobDialog").close(); location.hash = "#/finances/income"; }
  catch (e) { toast(e.message); }
};
$("jobForm").onsubmit = async (e) => {
  e.preventDefault();
  const f = $("jobForm"), val = (k) => f.elements[k].value.trim();
  const body = Object.fromEntries(["employer", "role", "start", "end", "salary", "work_state", "notes"].map((k) => [k, val(k) || null]));
  if (val("p_date") && val("p_gross")) {
    body.pay = [...(editingJob?.pay || []), { date: val("p_date"), gross: +val("p_gross"), net: +val("p_net") || 0, federal: +val("p_federal") || 0, state: +val("p_state") || 0 }];
  }
  try {
    if (editingJob) await api(`/api/fin/income/jobs/${editingJob.id}`, { method: "PATCH", body: JSON.stringify(body) });
    else await api("/api/fin/income/jobs", { method: "POST", body: JSON.stringify(body) });
    $("jobDialog").close(); toast("Saved"); render();
  } catch (err) { toast(err.message); }
};

// ---- Taxes
async function loadFinTaxes(t) {
  t = t || await api(`/api/fin/taxes?year=${new Date().getFullYear()}`);
  const F = t.federal, IL = t.illinois, DC = t.dc, E = t.estimated, S = t.settings, TR = t.trust, CH = t.charitable;
  const owe = t.settle < 0;
  const box = (title, x, extra = "") => `<div class="tax-box"><h4>${title}</h4>
      <div class="mini-row"><span>Tax</span><strong>${m2(x.tax)}</strong></div><div class="mini-row"><span>Paid so far</span><span>${m2(x.paid)}</span></div>
      <div class="mini-row total"><span>${x.balance < 0 ? "Owe" : "Back to you"}</span><strong class="${x.balance < 0 ? "neg" : "pos"}">${m2(Math.abs(x.balance))}</strong></div>${extra}</div>`;
  $("taxPage").innerHTML = `
    <div class="pay-head"><div><p class="eyebrow">Tax year ${t.year} · estimate</p><h2 class="title">${owe ? `About ${m0(-t.settle)} still to pay` : `About ${m0(t.settle)} back`}</h2>
      <p class="lede-sm">${m0(t.income_tax)} in income tax on ${m0(t.income)} of income${t.fica.ss ? `, plus ${m0(t.fica.ss + t.fica.medicare)} Social Security & Medicare (already withheld)` : ""}.</p></div></div>
    <div class="card"><div class="card-head"><h3>Income</h3><span class="card-meta">Each line links to where it came from</span></div>
      ${t.sources.map((x) => `<a class="mini-row" href="${x.link}"><span>${esc(x.label)}${x.note ? ` <span class="tag">${esc(x.note)}</span>` : ""}</span><strong>${m2(x.amount)}</strong></a>`).join("")}
      <div class="mini-row total"><span>Adjusted gross income</span><strong>${m2(F.agi)}</strong></div></div>
    ${E.needs_payment || E.dc_needs_payment ? `<div class="card alert-card"><div class="card-head"><h3>Estimated payment due ${fdate(E.next_due)}</h3></div>
      ${E.needs_payment ? `<p>Federal: withholding covers ${m0(E.federal_paid)} of the ${m0(E.federal_required)} needed to avoid an underpayment penalty${E.prior_year_known ? "" : " (90% of this year's tax; add last year's tax below to use the 100%-of-last-year safe harbor, which may be lower)"}. Paying about <strong>${m0(E.short_by)}</strong> by ${fdate(E.next_due)} covers it.</p>` : ""}
      ${E.dc_needs_payment ? `<p>DC: nothing is withheld for DC (your employer withheld Illinois), so about <strong>${m0(E.dc_short_by)}</strong> is due to DC; DC expects estimated payments when you'll owe more than $100.</p>` : ""}
      ${E.explain ? `<p class="hint">${esc(E.explain)}</p>` : ""}</div>` : ""}
    <div class="tax-grid">
      ${box("Federal", F, `<p class="hint">${F.deduction_kind === "itemized" ? "Itemizing" : F.charity_nonitemizer ? "Standard + charitable deduction" : "Standard deduction"} (${m0(F.deduction)}) · taxable ${m0(F.taxable)} · top bracket ${(F.marginal * 100).toFixed(0)}%${F.niit ? ` · NIIT ${m2(F.niit)}` : ""}</p>`)}
      ${box("Illinois", IL, `<p class="hint">${esc(IL.note)} Illinois wages ${m0(IL.wages)} at 4.95%, exemption prorated ${IL.ratio_pct}%.</p>`)}
      ${DC ? box("DC", DC, `<p class="hint">Resident: taxed on all ${m0(DC.agi)}, then a ${m0(DC.credit_for_illinois)} credit for the Illinois tax on your wages. Top bracket ${(DC.marginal * 100).toFixed(2)}%.</p>`) : ""}
    </div>
    <div class="grid-2">
      <div class="card"><div class="card-head"><h3>Deductions</h3><span class="card-meta">${F.deduction_kind === "itemized" ? "Itemizing wins" : "Standard deduction wins"}</span></div>
        <div class="mini-row"><span>Standard deduction</span><span>${m2(F.std_deduction)}</span></div>
        <div class="mini-row"><span>+ Charitable deduction for non-itemizers (up to $1,000)</span><span>${m2(F.charity_nonitemizer)}</span></div>
        <div class="mini-row"><span>vs. itemizing: state & local taxes ${m0(F.salt)} + gifts above 0.5% of income</span><span>${m2(F.itemized)}</span></div>
        <h4 class="mini-h">Charitable gifts found</h4>
        ${CH.items.map((c) => `<div class="mini-row"><span>${fdate(c.date)} <span class="tag">${esc(c.merchant)}</span></span><span>${m2(c.amount)} <button class="text-btn" data-notgift="${c.id}">Not a gift</button></span></div>`).join("") || `<p class="hint">None found in the card or bank feed yet. Gifts appear once that month's statement is imported or Plaid links the card. If a charity isn't picked up, add its name under Your inputs.</p>`}
        ${CH.extra ? `<div class="mini-row"><span>Added by you</span><span>${m2(CH.extra)}</span></div>` : ""}</div>
      <div class="card"><div class="card-head"><h3>Trust money</h3><span class="card-meta">${TR.form_on_file ? "Tax form on file" : "Waiting for the 1099-R / K-1"}</span></div>
        <div class="mini-row"><span>Received</span><strong>${m2(TR.gross)}</strong></div>
        <div class="mini-row"><span>Taxable share used</span><span>${TR.taxable_pct}% → ${m2(TR.taxable)}</span></div>
        <div class="mini-row"><span>Tax withheld by the trust</span><span>${m2(TR.withheld_federal)} federal · ${m2(TR.withheld_state)} state</span></div>
        <p class="hint">Money from an inherited retirement account is taxed as ordinary income. It's not Illinois income (you weren't an Illinois resident and it isn't Illinois-source), but DC taxes it.</p>
        <div class="card-head"><h3>Roth IRA income check</h3></div>
        <p>${esc(t.roth.note)}</p><p class="hint">2026 income for the test ${m0(t.roth.magi)} vs phase-out ${m0(t.roth.phaseout[0])}–${m0(t.roth.phaseout[1])}; contributed ${m0(t.roth.contributed)} of ${m0(t.roth.allowed)} allowed.</p></div>
    </div>
    <div class="card"><div class="card-head"><h3>If you took retirement money out</h3><span class="card-meta">Federal + DC now; Illinois later</span></div>
      <div class="ret-grid">${t.retirement.map((r) => `<div class="ret-acct"><h4>${esc(r.name)} <span class="tag">${m2(r.value)}</span></h4>
        ${r.pieces.map((p) => `<div class="mini-row"><span>${esc(p.label)} <span class="tag">${esc(p.note)}</span></span><span>${m2(p.amount)} → tax ${m2(p.tax_now)}</span></div>`).join("")}
        ${r.kind === "roth" ? `<div class="mini-row total"><span>Withdrawn today (under 59½)</span><strong>${m2(r.early_tax)}</strong></div><div class="mini-row"><span>Once qualified (59½ and 5 years)</span><strong class="pos">$0.00</strong></div><p class="hint">${esc(r.assumptions)}</p>`
          : (r.scenarios || []).map((x) => `<div class="mini-row"><span>${esc(x.label)}${x.note ? ` <span class="tag">${esc(x.note)}</span>` : ""}</span><span>${m2(x.federal + x.state + x.penalty)}</span></div>`).join("")}</div>`).join("")}</div></div>
    <div class="card"><div class="card-head"><h3>Federal brackets</h3><span class="card-meta">${m0(F.agi)} − ${m0(F.deduction)} = ${m0(F.taxable)} taxable</span></div>
      <div class="ladder">${F.fill.filter((b, i) => b.amount || (i && F.fill[i - 1].amount)).map((b) => `<div class="ladder-row ${b.amount ? "" : "unused"}"><span class="lr-rate">${(b.rate * 100).toFixed(0)}%</span>
        <span class="lr-range">${usd0.format(b.from)}${b.to == null ? "+" : ` – ${usd0.format(b.to)}`}</span><span class="lr-bar"><i style="width:${b.to == null ? 100 : Math.min(100, b.amount / (b.to - b.from) * 100)}%"></i></span>
        <span class="lr-amt">${b.amount ? `${m2(b.amount)} → <b>${m2(b.tax)}</b>` : "not reached"}</span></div>`).join("")}
        ${F.preferential ? `<div class="ladder-row"><span class="lr-rate">pref.</span><span class="lr-range">Qualified dividends / gains</span><span></span><span class="lr-amt">${m2(F.preferential)} → <b>${m2(F.preferential_tax)}</b></span></div>` : ""}</div></div>
    <div class="card"><div class="card-head"><h3>Your inputs</h3><span class="card-meta">The documents can't tell us these</span></div>
      <form id="taxSettings" class="settings-grid">
        <label>Trust: taxable share %<input class="input" name="trust_taxable_pct" type="number" min="0" max="100" step="1" value="${S.trust_taxable_pct ?? 100}" /></label>
        <label>Trust: federal tax withheld<input class="input" name="trust_federal_withheld" type="number" step="0.01" value="${S.trust_federal_withheld || 0}" /></label>
        <label>Trust: state tax withheld<input class="input" name="trust_state_withheld" type="number" step="0.01" value="${S.trust_state_withheld || 0}" /></label>
        <label>${t.year - 1} total federal tax (Form 1040 line 24)<input class="input" name="prior_year_tax" type="number" step="1" value="${S.prior_year_tax ?? ""}" /></label>
        <label>${t.year - 1} AGI<input class="input" name="prior_year_agi" type="number" step="1" value="${S.prior_year_agi ?? ""}" /></label>
        <label>Other charitable gifts not in the feed<input class="input" name="charitable_extra" type="number" step="0.01" value="${S.charitable_extra || 0}" /></label>
        <label>Birth year<input class="input" name="birth_year" type="number" value="${S.birth_year ?? ""}" /></label>
        <label class="span-2">Charities you give to (names as they appear on statements, comma-separated)<input class="input" name="charity_keywords" value="${esc((S.charity_keywords || []).join(", "))}" /></label>
        <div class="settings-actions"><button class="btn btn-primary btn-sm">Recalculate</button></div>
      </form></div>
    <p class="hint">${esc(t.disclaimer)} 2026 figures: IRS brackets and standard deduction; Illinois 4.95%; DC 2026 brackets; long-term gains 0%/15%/20% at $49,450 / $545,500 (single); charitable deduction up to $1,000 for non-itemizers and a 0.5%-of-income floor for itemizers (OBBBA); Roth phase-out $153,000–$168,000; Illinois subtracts IRA distributions.</p>`;
  $("taxSettings").onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.fromEntries([...new FormData(e.target).entries()].map(([k, x]) =>
      [k, k === "charity_keywords" ? x.split(",").map((w) => w.trim()).filter(Boolean) : x === "" ? null : +x]));
    try { loadFinTaxes(await api("/api/fin/tax-settings", { method: "PUT", body: JSON.stringify(body) })); toast("Recalculated"); }
    catch (err) { toast(err.message); }
  };
  $$("[data-notgift]").forEach((b) => (b.onclick = async () => {
    try { await api(`/api/fin/transactions/${b.dataset.notgift}`, { method: "PATCH", body: JSON.stringify({ charitable: false }) }); loadFinTaxes(); }
    catch (err) { toast(err.message); }
  }));
}

// ---- Accounts & imports
async function loadImports(v) {
  v = v || await api("/api/fin/imports");
  finAccounts = v.accounts.map((a) => ({ ...a, account: a.id }));
  // coverage timeline
  const all = v.coverage.flatMap((r) => Object.values(r.sources).flatMap((s) => [s.start, s.end]));
  const lo = all.length ? all.reduce((a, b) => (a < b ? a : b)) : iso(new Date()), hi = iso(new Date());
  const span = Math.max(1, (new Date(hi) - new Date(lo)) / 864e5);
  const pos = (d) => Math.max(0, Math.min(100, (new Date(d) - new Date(lo)) / 864e5 / span * 100));
  $("covTimeline").innerHTML = all.length ? `<div class="cov-scale"><span>${fdate(lo)}</span><span>Today</span></div>` + v.coverage.map((r) => `<div class="cov-row"><span class="cov-name">${esc(r.name)}</span><span class="cov-track">
      ${Object.entries(r.sources).flatMap(([src, s]) => (s.periods || [s]).map((p) => `<i class="cov-bar src-${src}" style="left:${pos(p.start)}%;width:${Math.max(0.8, pos(p.end) - pos(p.start))}%" title="${esc(SRC[src] || src)}: ${fdate(p.start)} – ${fdate(p.end)}${p.label ? ` (${p.label})` : ""}"></i>`)).join("")}</span>
      <span class="cov-meta">${Object.entries(r.sources).map(([src, s]) => `${SRC[src] || src}${s.gaps?.length ? ` <span class="warn">${s.gaps.length} gap${s.gaps.length > 1 ? "s" : ""}</span>` : ""}`).join(" · ") || "Nothing yet"}</span></div>`).join("") +
    `<div class="cov-legend">${["plaid", "ubs_pdf", "chase_csv", "venmo_csv"].map((k) => `<span><i class="cov-bar src-${k}"></i>${SRC[k]}</span>`).join("")}</div>` : `<div class="empty">Nothing imported yet.</div>`;
  const R = v.reconciliation;
  $("recMeta").textContent = R.length ? `${R.filter((r) => r.ok).length} of ${R.length} statement periods match to the cent` : "";
  $("recTable").innerHTML = R.length ? `<thead><tr><th>Account</th><th>Period</th><th class="num">Start</th><th class="num">Deposits</th><th class="num">Income</th><th class="num">Market</th><th class="num">End</th><th>Checks</th></tr></thead><tbody>` +
    R.map((r) => `<tr><td>${esc(r.account_name)}</td><td>${esc(r.period)}</td><td class="num">${m2(r.opening)}</td><td class="num">${m2(r.deposits)}</td><td class="num">${m2(r.income)}</td><td class="num">${signed(r.market_change)}</td><td class="num"><strong>${m2(r.closing)}</strong></td>
      <td>${r.ok ? `<span class="pill pill-met" title="${esc(r.checks.map((c) => c.check).join(" · "))}">${r.checks.length} checks ✓</span>` : r.checks.filter((c) => !c.ok).map((c) => `<span class="pill pill-missing">${esc(c.check)}: ${signed(c.diff)}</span>`).join(" ")}</td></tr>`).join("") + "</tbody>"
    : `<tbody><tr><td class="empty">Upload statements to reconcile them.</td></tr></tbody>`;
  const D = v.duplicates;
  $("dupReport").innerHTML = `<div class="mini-row"><span>Records on file</span><strong>${D.records.toLocaleString()}</strong></div>
    <div class="mini-row"><span>Kept as copies of another source (not counted)</span><strong>${D.superseded}</strong></div>
    ${D.examples.slice(0, 6).map((x) => `<div class="mini-row"><span>${fdate(x.date)} <span class="tag">${esc(x.description || "")} · ${SRC[x.src] || x.src}</span></span><span>${m2(x.amount)}</span></div>`).join("")}
    <p class="hint">The same account, amount and date (within 3 days) from two sources is one transaction. Plaid's copy wins over a statement, which wins over a CSV; the other is kept for the record but never added in.</p>`;
  $("taxDocs").innerHTML = v.tax_docs.length ? v.tax_docs.map((d) => `<div class="mini-row"><span><strong>${d.year} ${esc(d.form)}</strong> <span class="tag">${esc((v.accounts.find((a) => a.id === d.account) || {}).name || "")}</span></span><span>${d.form === "1099-R" ? `${m2(d.gross_distribution)} · code ${esc(d.distribution_code)}` : d.form === "5498" ? `conversion ${m2(d.roth_conversion)}` : d.form === "1099-DIV" ? `${m2(d.ordinary_dividends)} dividends` : d.form === "1099-INT" ? `${m2(d.interest)} interest` : d.form === "1099-B" ? `${m2((d.short?.gain || 0) + (d.long?.gain || 0))} gains` : ""}</span></div>`).join("") : `<p class="hint">None yet.</p>`;
  $("acctAdmin").innerHTML = v.accounts.map((a) => `<div class="acct-row"><span class="acct-name"><strong>${esc(a.name)}</strong><span class="tag">${esc(a.type_label)}${a.mask ? ` ··${esc(a.mask)}` : ""} · ${esc(a.institution || "")}</span></span>
      <span class="acct-src">${(a.aliases || []).map((x) => ({ import: "CSV import", ubs: "UBS statement", plaid: "Plaid", manual: "Entered by you", venmo: "Venmo CSV" })[x.split(":")[0]] || "Other").filter((x, i, arr) => arr.indexOf(x) === i).join(", ") || "—"}</span>
      <span></span><button class="text-btn" data-bal="${a.id}">Balance</button></div>`).join("");
  bindBalanceButtons();
  const S = v.tax_settings, prior = Object.entries(S.roth_prior_contributions || {}).map(([y, x]) => `${y}: ${x}`).join(", ");
  $("finSettingsForm").innerHTML = `
    <label>Birth year<input class="input" name="birth_year" type="number" value="${S.birth_year ?? ""}" /></label>
    <label>Year of your first Roth contribution<input class="input" name="roth_first_year" type="number" value="${S.roth_first_year ?? ""}" placeholder="starts the 5-year clock" /></label>
    <label class="span-2">Roth contributions before the statements on file<input class="input" name="roth_prior" value="${esc(prior)}" placeholder="2024: 3000, 2025: 0" /></label>
    <label>Filing status<select class="input" name="filing_status"><option value="single" ${S.filing_status !== "mfj" ? "selected" : ""}>Single</option><option value="mfj" ${S.filing_status === "mfj" ? "selected" : ""}>Married filing jointly</option></select></label>
    <label>Qualified share of dividends %<input class="input" name="qualified_dividend_pct" type="number" min="0" max="100" value="${S.qualified_dividend_pct ?? ""}" placeholder="from last year's 1099-DIV" /></label>
    <div class="settings-actions"><button class="btn btn-primary btn-sm">Save</button></div>`;
  $("finSettingsForm").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target, num = (k) => (f.elements[k].value === "" ? null : +f.elements[k].value);
    const roth = {};
    f.elements.roth_prior.value.split(",").forEach((p) => { const m = p.match(/(\d{4})\s*:\s*([\d.]+)/); if (m) roth[m[1]] = +m[2]; });
    try {
      await api("/api/fin/tax-settings", { method: "PUT", body: JSON.stringify({ birth_year: num("birth_year"), roth_first_year: num("roth_first_year"),
        roth_prior_contributions: roth, filing_status: f.elements.filing_status.value, qualified_dividend_pct: num("qualified_dividend_pct") }) });
      toast("Saved");
    } catch (err) { toast(err.message); }
  };
}
$("pdf-files").onchange = () => {
  const fs = $("pdf-files").files;
  $("pdfLabel").textContent = fs.length ? (fs.length === 1 ? fs[0].name : `${fs.length} PDFs selected`) : "Choose PDFs";
};
$("pdf-upload").onclick = async () => {
  const files = [...$("pdf-files").files];
  if (!files.length) return toast("Choose at least one PDF");
  // one file per request: each stays small and quick, and one bad file doesn't stop the rest
  $("pdf-upload").disabled = true;
  $("pdfResults").innerHTML = files.map((f, i) => `<div class="mini-row"><span>${esc(f.name)}</span><span id="pdfr-${i}" class="muted">Waiting</span></div>`).join("");
  let last = null, ok = 0;
  for (const [i, f] of files.entries()) {
    $("pdf-upload").textContent = `Reading ${i + 1} of ${files.length}…`;
    $(`pdfr-${i}`).textContent = "Reading…";
    const fd = new FormData(); fd.append("files", f);
    try {
      const r = await api("/api/fin/upload", { method: "POST", body: fd });
      last = r.imports;
      const x = r.results[0] || {};
      $(`pdfr-${i}`).className = x.error ? "neg" : "";
      $(`pdfr-${i}`).textContent = x.error ? x.error
        : x.kind === "statement" ? x.accounts.map((a) => `${(r.imports.accounts.find((c) => c.id === a.account) || {}).name || a.account} to ${fdate(a.closing_date)}`).join(" · ")
        : `${x.year} tax forms: ${x.forms.map((q) => q.form).join(", ")}`;
      if (!x.error) ok++;
    } catch (e) {
      $(`pdfr-${i}`).className = "neg";
      $(`pdfr-${i}`).textContent = e.message === "Failed to fetch" ? "The server didn't answer. Try this file again in a minute." : e.message;
    }
  }
  $("pdf-files").value = ""; $("pdfLabel").textContent = "Choose PDFs";
  $("pdf-upload").disabled = false; $("pdf-upload").textContent = "Upload";
  if (last) loadImports(last).then(() => { $("pdfResults").scrollIntoView({ block: "nearest" }); });
  toast(`${ok} of ${files.length} imported`);
};
$("profile-file").onchange = () => { $("profileLabel").textContent = $("profile-file").files[0]?.name || "Choose a JSON file"; };
$("profile-upload").onclick = async () => {
  const f = $("profile-file").files[0];
  if (!f) return toast("Choose a finance profile file");
  try {
    await api("/api/fin/profile", { method: "POST", body: await f.text() });
    $("profile-file").value = ""; $("profileLabel").textContent = "Choose a JSON file";
    toast("Finance profile imported"); loadImports();
  } catch (e) { toast(e.message); }
};
$("reconcileBtn").onclick = async () => {
  try { const r = await api("/api/fin/reconcile", { method: "POST" }); loadImports(r.imports); toast(`Checked: ${r.changed} links updated, ${r.paired} transfers paired`); }
  catch (e) { toast(e.message); }
};
$("addAcctBtn").onclick = () => { $("acctForm").reset(); $("acctForm").elements.date.value = iso(new Date()); $("acctDialog").showModal(); };
$("acctCancel").onclick = () => $("acctDialog").close();
$("acctForm").onsubmit = async (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target).entries());
  try { await api("/api/fin/accounts", { method: "POST", body: JSON.stringify(body) }); $("acctDialog").close(); toast("Account added"); render(); }
  catch (err) { toast(err.message); }
};

// ---------- Finance assistant: a chat on every tab ----------
const renderMarkdown = (md) => DOMPurify.sanitize(marked.parse(md || ""));
const chats = {};            // place -> [{role, content}] (kept in this browser tab only)
let chatPlace = "Net Worth";
const CHAT_SUGGEST = {
  networth: ["What changed my net worth in September?", "Which balances are out of date?", "How much is in cash vs invested?"],
  cash: ["How much did I spend on food last month?", "What are my biggest recurring charges?", "Did anything get counted twice?"],
  investments: ["What's my unrealized gain in the Roth?", "Which lots go long-term in the next 60 days?", "How am I doing vs the S&P 500 this year?"],
  income: ["What did I earn in 2026, all sources?", "How much did the trust distribute?", "What did my paychecks come to after tax?"],
  taxes: ["How much do I owe on the trust money?", "Do I need an estimated payment, and when?", "Itemize or take the standard deduction?"],
  accounts: ["Was any transfer counted twice?", "Which statements are missing?", "Do all the statements reconcile?"],
};
function setChatPlace(tab) {
  const sub = routeQuery.get("acct") || routeQuery.get("sub");
  const tabLabel = (SECTIONS.finances.tabs.find(([k]) => k === tab) || [, "Finances"])[1];
  const subLabel = sub && sub !== "all" && sub !== "overview" ? (finAccounts.find((a) => (a.account || a.id) === sub)?.name || sub) : null;
  chatPlace = subLabel ? `${tabLabel} → ${subLabel}` : tabLabel;
  $("chatPlace").textContent = chatPlace;
  $("chatSuggestions").innerHTML = (CHAT_SUGGEST[tab] || []).map((q) => `<button class="chip">${esc(q)}</button>`).join("");
  $$("#chatSuggestions .chip").forEach((c) => (c.onclick = () => sendChat(c.textContent)));
  const hist = chats[chatPlace] || [];
  $("chatMessages").innerHTML = "";
  hist.forEach((m) => addMessage(m.role, m.role === "user" ? esc(m.content) : renderMarkdown(m.content)));
  $("chatSuggestions").classList.toggle("hidden", hist.length > 0);
}
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
  const hist = (chats[chatPlace] ||= []);
  $("chatInput").value = ""; autosize();
  $("chatSuggestions").classList.add("hidden");
  addMessage("user", esc(text));
  hist.push({ role: "user", content: text });
  const pending = addMessage("assistant", `<span class="typing"><span></span><span></span><span></span></span>`);
  $("chatSend").disabled = true;
  try {
    const r = await api("/api/chat", { method: "POST", body: JSON.stringify({ messages: hist, place: chatPlace }) });
    hist.push({ role: "assistant", content: r.reply });
    pending.remove();
    const looked = [...new Set(r.tools_used || [])].map((t) => t.replace(/^get_/, "").replaceAll("_", " ")).join(", ");
    addMessage("assistant", renderMarkdown(r.reply), looked ? `Looked up: ${looked}` : "");
  } catch (e) {
    pending.remove(); hist.pop();
    addMessage("assistant error", esc(e.message));
  } finally { $("chatSend").disabled = false; $("chatInput").focus(); }
}
function autosize() { const t = $("chatInput"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 160) + "px"; }
$("chatInput").addEventListener("input", autosize);
$("chatSend").onclick = () => sendChat($("chatInput").value);
$("chatInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat($("chatInput").value); } });
$("askBtn").onclick = () => { $("chatDrawer").classList.remove("hidden"); $("askBtn").classList.add("hidden"); setTimeout(() => $("chatInput").focus(), 50); };
$("chatClose").onclick = () => { $("chatDrawer").classList.add("hidden"); $("askBtn").classList.remove("hidden"); };

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

async function loadHealth(r) {
  if (!backendReady) return toast("Backend not connected yet");
  const p = r.panel;
  if (p === "dashboard") return loadHealthDash();
  if (p === "overview") return loadHealthOverview();
  if (p === "planner") return loadWeekPlan();
  if (p === "history") return loadHistory();
  if (p === "running") return loadRunning();
  if (p === "race") return loadRace();
  if (p === "calendar") return renderCalendar();
  if (p === "connect") return loadConnect();
  if (p === "sports") return loadSports();
  if (p === "food-today") return loadFoodDay();
  if (p === "food-history") return loadFoodHistory();
  if (p === "food-goals") return loadFoodGoals();
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
  if (w.name) bits.push(w.name);
  if (w.pace) bits.push(`${fmtPace(w.pace)} /mi`);
  if (w.duration_min) bits.push(`${w.duration_min} min`);
  if (w.strain != null) bits.push(`Strain ${w.strain}`);
  if (w.avg_hr) bits.push(`Avg HR ${w.avg_hr}`);
  if (w.calories) bits.push(`${w.calories} cal`);
  return bits.join(" · ");
}

function exerciseText(w) {
  return (w.exercises || []).map((e) => {
    if (Array.isArray(e.sets)) return `${esc(e.name)} ${e.sets.length}×`;
    const sr = e.sets && e.reps ? ` ${e.sets}×${e.reps}` : e.sets ? ` ${e.sets} sets` : "";
    return `${esc(e.name)}${sr}${e.weight ? ` @ ${e.weight} lb` : ""}`;
  }).join(", ");
}

async function loadWorkoutsTab() {
  return loadHistory();
}
async function _legacyWorkoutList() {
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
    loadHistory();
  } catch (err) { toast(err.message); }
  finally { $("w-save").disabled = false; }
};

// Calendar
async function renderCalendar() {
  const y = calMonth.getFullYear(), m = calMonth.getMonth();
  const first = new Date(y, m, 1), last = new Date(y, m + 1, 0);
  $("calTitle").textContent = first.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  const ds = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const [h, wk, rn] = await Promise.all([
    api(`/api/health/summary?start=${ds(first)}&end=${ds(last)}`),
    api(`/api/workouts?start=${ds(first)}&end=${ds(last)}`),
    api(`/api/runs?start=${ds(first)}&end=${ds(last)}`).catch(() => ({ runs: [] }))]);
  const byDay = Object.fromEntries(h.days.map((d) => [d.date, d]));
  const workoutsByDay = {};
  const runDays = new Set(rn.runs.map((r) => r.date));
  // a run recorded by both WHOOP and Strava shows once, with Strava's distance and pace
  wk.workouts.filter((w) => !(w.source === "whoop" && /run/i.test(w.type) && runDays.has(w.date)))
    .forEach((w) => (workoutsByDay[w.date] ||= []).push(w));
  rn.runs.forEach((r) => (workoutsByDay[r.date] ||= []).push({ type: `Run ${fmtMi(r.distance_m)} mi`, source: "strava",
    duration_min: r.moving_s ? Math.round(r.moving_s / 60) : null, avg_hr: r.avg_hr ? Math.round(r.avg_hr) : null,
    notes: "", exercises: [], pace: paceOf(r), name: r.name }));
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
        || `<p>No workouts logged. <a class="link" href="#/health/lifting/planner">Plan one →</a></p>`}`;
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
  const sres = routeQuery.get("strava");
  if (sres) {
    history.replaceState(null, "", "#/health/connect");
    const msg = { connected: "Strava connected. Pulling your runs…", cancelled: "Strava connection cancelled",
                  expired: "That link expired. Try connecting again.", failed: "Strava couldn't finish connecting. Try again.",
                  scope: "LifeTracker needs access to your activities. Connect again and leave that box ticked." }[sres];
    if (msg) toast(msg);
  }
  const [h, run] = await Promise.all([
    api(`/api/health/summary?start=${iso(new Date())}&end=${iso(new Date())}`),
    api(`/api/running?year=${new Date().getFullYear()}`).catch(() => null)]);
  const st = run?.strava;
  $("stravaStatus").textContent = st?.connected
    ? `Connected${st.athlete ? ` as ${st.athlete}` : ""}${st.last_sync ? ` · last synced ${new Date(st.last_sync).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}` : " · not synced yet"}`
    : "Runs with routes, mileage, pace, splits and best efforts. Feeds Running and Race Training. Live sync needs a Strava subscription.";
  $("stravaConnect").classList.toggle("hidden", !!st?.connected);
  $("stravaSync").classList.toggle("hidden", !st?.connected);
  $("stravaDisconnect").classList.toggle("hidden", !st?.connected);
  if (sres === "connected") syncStrava();
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

// ---------- Running (Strava) ----------
const MI = 1609.344;
const miles = (m) => (m || 0) / MI;
const fmtMi = (m, d = 1) => miles(m).toFixed(d);
const fmtPace = (s) => (s == null || !isFinite(s) ? "—" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`);
const paceOf = (r) => (r.distance_m ? r.moving_s / miles(r.distance_m) : null);
function fmtClock(s) {
  if (s == null) return "—";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}
const fmtHrs = (s) => `${Math.floor(s / 3600)}h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")}m`;
const longDay = (d) => new Date(d + "T00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });

// Google encoded polyline -> [[lat, lng], ...]
function decodePolyline(str) {
  const out = []; let i = 0, lat = 0, lng = 0;
  while (str && i < str.length) {
    for (const k of [0, 1]) {
      let b, shift = 0, res = 0;
      do { b = str.charCodeAt(i++) - 63; res |= (b & 31) << shift; shift += 5; } while (b >= 32);
      const v = res & 1 ? ~(res >> 1) : res >> 1;
      if (k === 0) lat += v; else lng += v;
    }
    out.push([lat / 1e5, lng / 1e5]);
  }
  return out;
}

function routeSvg(poly, w = 64, h = 48) {
  const pts = decodePolyline(poly);
  if (pts.length < 2) return `<span class="route-thumb route-none" style="width:${w}px;height:${h}px"></span>`;
  const k = Math.cos((pts[0][0] * Math.PI) / 180);
  const xs = pts.map((p) => p[1] * k), ys = pts.map((p) => -p[0]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const s = Math.min((w - 8) / (x1 - x0 || 1e-9), (h - 8) / (y1 - y0 || 1e-9));
  const ox = (w - (x1 - x0) * s) / 2, oy = (h - (y1 - y0) * s) / 2;
  const d = xs.map((x, j) => `${j ? "L" : "M"}${(ox + (x - x0) * s).toFixed(1)},${(oy + (ys[j] - y0) * s).toFixed(1)}`).join("");
  return `<svg class="route-thumb" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="${d}"/></svg>`;
}

const maps = {};
function tileLayer() {
  const dark = isDark();
  return L.tileLayer(`https://{s}.basemaps.cartocdn.com/${dark ? "dark_all" : "light_all"}/{z}/{x}/{y}{r}.png`, {
    maxZoom: 19, subdomains: "abcd",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>' });
}
function drawMap(id, lines, { weight = 3, opacity = 0.9 } = {}) {
  if (typeof L === "undefined") { $(id).innerHTML = `<div class="empty">Map unavailable</div>`; return; }
  maps[id]?.remove();
  const m = maps[id] = L.map(id, { zoomControl: true, scrollWheelZoom: false, attributionControl: false });
  L.control.attribution({ prefix: false }).addTo(m);
  tileLayer().addTo(m);
  const color = cssVar("--accent");
  const group = L.featureGroup(lines.filter((l) => l.length > 1).map((l) => L.polyline(l, { color, weight, opacity }))).addTo(m);
  if (group.getLayers().length) m.fitBounds(group.getBounds(), { padding: [16, 16] });
  else m.setView([38.9, -77.03], 11);
  setTimeout(() => m.invalidateSize(), 50);
}

let runYear = null, runData = null, runSyncing = false;
async function loadRunning() {
  const y = runYear || new Date().getFullYear();
  const d = runData = await api(`/api/running?year=${y}`);
  const on = d.strava.connected || d.whoop.connected || !!d.export?.last_import;
  $("runEmpty").classList.toggle("hidden", on || d.runs.length);
  $("runBody").classList.toggle("hidden", !on && !d.runs.length);
  $("runYear").innerHTML = [...new Set([String(new Date().getFullYear()), ...d.years])].sort().reverse()
    .map((v) => `<option ${+v === y ? "selected" : ""}>${v}</option>`).join("");
  $("runSync").classList.toggle("hidden", !d.strava.connected && !d.whoop.connected);
  $("runImportBtn").classList.toggle("hidden", d.strava.connected || !d.runs.length);
  const fromExport = d.export?.last_import ? `Strava export imported ${new Date(d.export.last_import).toLocaleDateString("en-US", { month: "short", day: "numeric" })}` : "";
  $("runStatus").textContent = !d.strava.connected
    ? [fromExport, d.whoop.connected ? "new runs from WHOOP" : ""].filter(Boolean).join(" · ").replace(/^n/, "N")
    : on ? (d.strava.last_sync ? `Synced ${new Date(d.strava.last_sync).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}` : "Not synced yet")
      + (d.details_missing ? ` · ${d.details_missing} runs still loading splits` : "")
    : "";
  if (!on && !d.runs.length) return;
  // sync in the background when the data is more than an hour old
  if (d.strava.connected && !runSyncing && (!d.strava.last_sync || Date.now() - new Date(d.strava.last_sync) > 36e5)) syncStrava(true);

  const T = d.totals;
  $("r-miles").textContent = T.miles ? `${T.miles.toFixed(1)} mi` : "—";
  $("r-miles-sub").textContent = T.moving_s ? `${fmtHrs(T.moving_s)} moving · ${Math.round(T.elev_m * 3.28084).toLocaleString()} ft climbed` : "";
  $("r-runs").textContent = T.runs || "—";
  const wks = d.weekly.length || 1;
  $("r-runs-sub").textContent = T.runs ? `${(T.miles / wks).toFixed(1)} mi a week on average` : "";
  $("r-pace").textContent = T.pace_s_per_mi ? fmtPace(T.pace_s_per_mi) : "—";
  $("r-pace-sub").textContent = T.pace_s_per_mi ? "per mile, moving time" : "";
  $("r-long").textContent = T.longest ? `${T.longest.miles.toFixed(1)} mi` : "—";
  $("r-long-sub").textContent = T.longest ? shortDate(T.longest.date) : "";

  const last12 = d.weekly.slice(-26);
  drawChart("chart-run-weekly", "bar", last12.map((w) => shortDate(w.week)), last12.map((w) => w.miles),
    { tip: (v) => `${v.toFixed(1)} mi`, tick: (v) => `${v}` }, { y: { min: 0 } });
  $("r-week-meta").textContent = last12.length < d.weekly.length ? "Last 26 weeks" : "";

  $("runBests").innerHTML = d.bests.length ? d.bests.map((b) => `
    <button class="best-row" data-run="${esc(b.run_id)}">
      <span class="best-name">${esc(b.name.replace("Half-Marathon", "Half marathon"))}</span>
      <span class="best-time">${fmtClock(b.elapsed_s)}</span>
      <span class="best-date">${shortDate(b.date)}</span>
    </button>`).join("") : `<div class="empty">Best efforts appear once runs have their splits loaded.</div>`;
  $("r-best-meta").textContent = d.bests.length ? `Fastest in ${y}` : "";

  $("r-map-meta").textContent = d.runs.filter((r) => r.polyline).length ? `${d.runs.filter((r) => r.polyline).length} routes` : "";
  $("runMapAll").parentElement.classList.toggle("hidden", !d.runs.some((r) => r.polyline));
  if (d.runs.some((r) => r.polyline)) drawMap("runMapAll", d.runs.map((r) => decodePolyline(r.polyline)), { weight: 2, opacity: 0.45 });

  $("r-list-meta").textContent = `${d.runs.length} in ${y}`;
  renderRunList();
}

let runListAll = false;
function renderRunList() {
  const d = runData, y = d.year;
  const shown = runListAll ? d.runs : d.runs.slice(0, 20);
  $("runList").innerHTML = shown.map((r) => `
    <button class="run-row" data-run="${esc(r.id)}">
      ${routeSvg(r.polyline)}
      <span class="run-main">
        <span class="run-name">${esc(r.name)}${r.workout_type === 1 ? '<span class="source-tag">RACE</span>' : ""}${r.source === "whoop" ? '<span class="source-tag">WHOOP</span>' : ""}</span>
        <span class="run-date">${longDay(r.date)}${r.start_local ? ` · ${new Date(r.start_local).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}` : ""}</span>
      </span>
      <span class="run-stat"><b>${r.distance_m ? fmtMi(r.distance_m, 2) : "—"}</b> mi</span>
      <span class="run-stat"><b>${fmtPace(paceOf(r))}</b> /mi</span>
      <span class="run-stat run-opt"><b>${fmtClock(r.moving_s)}</b> time</span>
      <span class="run-stat run-opt"><b>${r.avg_hr ? Math.round(r.avg_hr) : "—"}</b> bpm</span>
    </button>`).join("") + (shown.length < d.runs.length ? `<button class="run-more" id="runMore">Show all ${d.runs.length} runs</button>` : "")
    || `<div class="empty">No runs in ${y}.</div>`;
  if ($("runMore")) $("runMore").onclick = () => { runListAll = true; renderRunList(); };
  $$("[data-panel='running'] [data-run]").forEach((b) => (b.onclick = () => openRun(b.dataset.run)));
}

async function openRun(id) {
  $("runDetail").innerHTML = `<div class="empty">Loading…</div>`;
  $("runDialog").showModal();
  const r = await api(`/api/running/${encodeURIComponent(id)}`);
  const splits = r.splits || [];
  const paces = splits.filter((s) => s.distance_m > 400).map((s) => s.moving_s / miles(s.distance_m));
  const [fast, slow] = [Math.min(...paces), Math.max(...paces)];
  const stat = (v, l) => `<div><b>${v}</b><span>${l}</span></div>`;
  $("runDetail").innerHTML = `
    <div class="run-detail-head">
      <div><h3>${esc(r.name)}</h3><p class="hint">${new Date(r.start_local || r.date).toLocaleString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" })}</p></div>
      <button class="btn btn-secondary btn-sm" id="runClose">Close</button>
    </div>
    <div class="run-map run-map-lg" id="runMapOne"></div>
    <div class="run-stats">
      ${stat(r.distance_m ? `${fmtMi(r.distance_m, 2)} mi` : "—", "Distance")}${stat(fmtPace(paceOf(r)) + " /mi", "Pace")}${stat(fmtClock(r.moving_s), "Moving time")}
      ${stat(r.elev_m != null ? `${Math.round(r.elev_m * 3.28084)} ft` : "—", "Elevation gain")}
      ${stat(r.avg_hr ? `${Math.round(r.avg_hr)} bpm` : "—", "Avg heart rate")}${stat(r.cadence ? `${Math.round(r.cadence * 2)} spm` : "—", "Cadence")}
      ${r.calories ? stat(Math.round(r.calories), "Calories") : ""}${r.elapsed_s && r.elapsed_s - r.moving_s > 60 ? stat(fmtClock(r.elapsed_s), "Elapsed") : ""}
    </div>
    ${r.description ? `<p class="run-desc">${esc(r.description)}</p>` : ""}
    ${splits.length ? `<h4>Splits</h4><div class="splits">${splits.map((s) => {
      const p = s.moving_s / miles(s.distance_m), partial = s.distance_m < 1500;
      const w = paces.length && slow > fast ? 35 + 65 * (1 - (p - fast) / (slow - fast)) : 70;
      return `<div class="split"><span class="split-n">${partial ? fmtMi(s.distance_m, 2) : s.n}</span>
        <span class="bar-track"><span class="bar-fill" style="display:block;width:${partial ? 20 : w}%"></span></span>
        <span class="split-pace">${fmtPace(p)}</span><span class="split-elev">${s.elev_m != null ? `${s.elev_m > 0 ? "+" : ""}${Math.round(s.elev_m * 3.28084)} ft` : ""}</span>
        <span class="split-hr">${s.avg_hr ? Math.round(s.avg_hr) : ""}</span></div>`; }).join("")}</div>` : `<p class="hint">${r.source === "whoop" ? "WHOOP doesn't record mile splits or the route." : "Splits load with the next sync."}</p>`}
    ${(r.best_efforts || []).length ? `<h4>Best efforts</h4><div class="efforts">${r.best_efforts.map((b) => `<span><b>${fmtClock(b.elapsed_s)}</b> ${esc(b.name.replace("Half-Marathon", "Half marathon"))}${b.pr_rank === 1 ? ' <i class="pr">PR</i>' : ""}</span>`).join("")}</div>` : ""}
    ${r.source === "whoop" ? "" : `<p class="hint"><a class="link" href="https://www.strava.com/activities/${esc(r.id)}" target="_blank" rel="noopener">View on Strava</a></p>`}`;
  $("runClose").onclick = () => $("runDialog").close();
  const line = decodePolyline(r.polyline_full || r.polyline);
  if (line.length > 1) drawMap("runMapOne", [line], { weight: 4 });
  else $("runMapOne").classList.add("hidden");
}
$("runDialog").addEventListener("click", (e) => { if (e.target === $("runDialog")) $("runDialog").close(); });
$("runYear").onchange = () => { runYear = +$("runYear").value; runListAll = false; loadRunning().catch((e) => toast(e.message)); };
$("runSync").onclick = () => (runData?.strava.connected ? syncStrava() : syncWhoop());

async function syncStrava(quiet = false) {
  if (runSyncing) return;
  runSyncing = true;
  const btns = [$("runSync"), $("stravaSync")];
  btns.forEach((b) => { b.disabled = true; });
  $("runSync").textContent = "Syncing…";
  try {
    const r = await api("/api/strava/sync", { method: "POST" });
    if (!quiet || r.note) toast(r.note || `Strava synced · ${r.runs} runs`);
    const { panel } = parseRoute();
    if (panel === "running") await loadRunning();
    if (panel === "connect") await loadConnect();
  } catch (e) { toast(e.message); }
  finally { runSyncing = false; btns.forEach((b) => { b.disabled = false; }); $("runSync").textContent = "Sync"; }
}

$("stravaConnect").onclick = async () => {
  try { const { url } = await api("/api/strava/connect", { method: "POST" }); location.href = url; }
  catch (e) { toast(e.message); }
};
$("stravaSync").onclick = () => syncStrava();
$("stravaDisconnect").onclick = async () => {
  if (!confirm("Disconnect Strava? Your synced runs stay in LifeTracker.")) return;
  await api("/api/strava", { method: "DELETE" }); toast("Strava disconnected"); loadConnect();
};

// Strava data export (zip) → runs, parsed in the browser, sent in batches
async function importStravaExport(file) {
  const status = (t) => { $("runStatus").textContent = t; toast(t); };
  try {
    status("Reading your Strava export…");
    const { parseStravaExport } = await import("./stravaexport.js");
    let shown = 0;
    const { runs, noMap } = await parseStravaExport(file, (n, total) => {
      if (n - shown >= 25 || n === total) { shown = n; $("runStatus").textContent = `Reading runs · ${n} of ${total}`; }
    });
    if (!runs.length) return toast("No runs found in that export");
    for (let i = 0; i < runs.length; i += 40) {
      $("runStatus").textContent = `Saving · ${Math.min(i + 40, runs.length)} of ${runs.length}`;
      await api("/api/strava/import", { method: "POST", body: JSON.stringify({ activities: runs.slice(i, i + 40) }) });
    }
    toast(`Imported ${runs.length} runs${noMap ? ` · ${noMap} without a route file LifeTracker can read` : ""}`);
    if (parseRoute().panel !== "running") location.hash = "#/health/running/runs"; else loadRunning();
  } catch (e) { toast(e.message); }
}
$$(".strava-zip").forEach((inp) => (inp.onchange = () => { const f = inp.files[0]; inp.value = ""; if (f) importStravaExport(f); }));

// ---------- Race training ----------
const RACE_KIND = { easy: "Easy", long: "Long", tempo: "Tempo", intervals: "Intervals", race: "Race", rest: "Rest" };
const PACE_NAMES = { E: "Easy", M: "Marathon", T: "Threshold", I: "Interval", R: "Repetition", G: "Goal race" };
let raceState = { plans: [], fitness: null, current: null, preview: null, race: "half" };

function parseClock(v) {
  v = String(v || "").trim();
  if (!v) return null;
  const parts = v.split(":").map(Number);
  if (parts.some((x) => !isFinite(x)) || parts.length > 3) throw new Error("Goal time must look like 1:45:00 or 24:30");
  return parts.reduce((a, b) => a * 60 + b, 0);
}

async function loadRace() {
  const d = await api("/api/raceplans");
  raceState.plans = d.plans; raceState.fitness = d.fitness;
  const F = d.fitness;
  $("raceFitness").textContent = F.runs_4wk
    ? `From Strava, last 4 weeks: ${F.weekly_mi} mi a week, longest run ${F.long_mi} mi.` +
      (F.vdot ? ` Paces use your ${F.source} (VDOT ${F.vdot}).` : " No recent race or best effort yet, so paces come from your goal time if you add one.")
    : "No recent Strava runs found. Enter your current mileage, and add a goal time to get paces.";
  const f = $("raceForm");
  if (!f.elements.current_weekly_mi.value && F.runs_4wk) f.elements.current_weekly_mi.value = F.weekly_mi;
  if (!f.elements.current_long_mi.value && F.runs_4wk) f.elements.current_long_mi.value = F.long_mi;
  $("racePicker").classList.toggle("hidden", !d.plans.length);
  if (!d.plans.length) { showRaceForm(true); return; }
  const pick = raceState.current && d.plans.some((p) => p.id === raceState.current) ? raceState.current
    : (d.plans.find((p) => p.race_date >= iso(new Date())) || d.plans[d.plans.length - 1]).id;
  $("racePlanSel").innerHTML = d.plans.map((p) => `<option value="${esc(p.id)}" ${p.id === pick ? "selected" : ""}>${esc(p.name)} · ${shortDate(p.race_date)}</option>`).join("");
  if (!raceState.preview) { showRaceForm(false); await showPlan(pick); }
}

function showRaceForm(on) {
  $("raceForm").classList.toggle("hidden", !on);
  $("raceCancel").classList.toggle("hidden", !raceState.plans.length);
  if (on) { raceState.preview = null; $("racePlan").innerHTML = ""; }
}

async function showPlan(id) {
  raceState.current = id;
  const p = await api(`/api/raceplans/${encodeURIComponent(id)}`);
  renderPlan(p, true);
}

function renderPlan(p, saved) {
  const today = iso(new Date());
  const days = Math.ceil((new Date(p.race_date + "T00:00") - new Date(today + "T00:00")) / 864e5);
  const fit = p.fitness || {};
  const paces = p.paces ? Object.entries(PACE_NAMES).filter(([k]) => p.paces[k] != null).map(([k, n]) => {
    const v = p.paces[k];
    return `<div><span>${n}</span><b>${Array.isArray(v) ? `${fmtPace(v[0])}–${fmtPace(v[1])}` : fmtPace(v)}</b></div>`; }).join("") : "";
  const weeks = p.weeks.map((w) => {
    const cur = today >= w.start && today < iso(new Date(new Date(w.start + "T00:00").getTime() + 7 * 864e5));
    const done = w.done_miles ?? null;
    const pct = w.miles ? Math.min(100, ((done || 0) / w.miles) * 100) : 0;
    return `<div class="week-card${cur ? " week-now" : ""}${w.start > today ? "" : " week-past"}" ${cur ? 'id="weekNow"' : ""}>
      <div class="week-head">
        <div><b>Week ${w.n}</b><span class="week-phase">${w.phase === "race" ? "Race week" : w.phase[0].toUpperCase() + w.phase.slice(1)}${w.cutback ? " · recovery week" : ""}</span></div>
        <div class="week-miles">${saved && w.start <= today ? `<span class="bar-track"><span class="bar-fill" style="display:block;width:${pct}%"></span></span><span>${(done || 0).toFixed(1)} of ${w.miles} mi</span>` : `<span>${w.miles} mi</span>`}</div>
      </div>
      <div class="week-days">${w.days.map((d) => {
        const a = d.actual, past = d.date < today;
        const state = d.kind === "rest" ? "" : a ? " day-done" : past ? " day-missed" : "";
        return `<div class="day k-${d.kind}${state}${d.date === today ? " day-today" : ""}" title="${esc(d.detail)}">
          <span class="day-dow">${new Date(d.date + "T00:00").toLocaleDateString("en-US", { weekday: "short", day: "numeric" })}</span>
          <span class="day-title">${esc(d.title)}</span>
          ${d.miles ? `<span class="day-mi">${d.miles} mi</span>` : ""}
          ${a ? `<span class="day-actual">Ran ${a.miles.toFixed(1)} mi · ${fmtPace(a.pace_s_per_mi)}</span>` : ""}
          ${d.detail ? `<span class="day-detail">${esc(d.detail)}</span>` : ""}
        </div>`; }).join("")}</div>
    </div>`; }).join("");
  $("racePlan").innerHTML = `
    <div class="race-hero">
      <div>
        <p class="eyebrow">${esc(p.label)} · ${new Date(p.race_date + "T00:00").toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" })}</p>
        <h2 class="title">${esc(p.name)}</h2>
        <p class="lede-sm">${days > 0 ? `${days} days to go · ` : days === 0 ? "Race day · " : ""}${p.weeks.length} weeks from ${shortDate(p.start)} · peak ${p.peak_miles} mi a week</p>
      </div>
      <div class="race-actions">${saved ? `<button class="text-btn" id="raceDelete">Delete plan</button>` : `<button class="btn btn-primary" id="raceSave2">Save plan</button>`}</div>
    </div>
    <div class="kpis kpis-3">
      <div class="kpi"><span class="kpi-label">Goal</span><span class="kpi-value">${p.goal_seconds ? fmtClock(p.goal_seconds) : "—"}</span><span class="kpi-sub">${p.goal_seconds ? `${fmtPace(p.paces?.G)} per mile` : "No goal time set"}</span></div>
      <div class="kpi"><span class="kpi-label">Current fitness predicts</span><span class="kpi-value">${fit.predicted_seconds && fit.source !== "your goal time" ? fmtClock(fit.predicted_seconds) : "—"}</span><span class="kpi-sub">${fit.source && fit.source !== "your goal time" ? `From your ${esc(fit.source)}` : "Needs a recent race or best effort on Strava"}</span></div>
      <div class="kpi"><span class="kpi-label">Weeks</span><span class="kpi-value">${p.weeks.length}</span><span class="kpi-sub">${p.weeks.filter((w) => w.start <= today).length} started</span></div>
    </div>
    ${p.notes?.length ? `<div class="card race-notes">${p.notes.map((n) => `<p>${esc(n)}</p>`).join("")}</div>` : ""}
    ${paces ? `<div class="card"><div class="card-head"><h3>Training paces</h3><span class="card-meta">Per mile · Daniels' VDOT ${fit.vdot}${fit.source === "your goal time" ? " from your goal time" : ""}</span></div><div class="pace-grid">${paces}</div></div>`
            : `<div class="card"><p>Workouts are described by effort. Add a goal time, or sync a recent race or hard effort from Strava, to get paces.</p></div>`}
    <div class="weeks">${weeks}</div>`;
  if (saved) $("raceDelete").onclick = async () => {
    if (!confirm(`Delete the plan "${p.name}"?`)) return;
    await api(`/api/raceplans/${encodeURIComponent(p.id)}`, { method: "DELETE" });
    raceState.current = null; toast("Plan deleted"); loadRace();
  };
  else $("raceSave2").onclick = saveRace;
  if (saved) $("weekNow")?.scrollIntoView({ block: "center" });
}

function raceBody() {
  const f = $("raceForm").elements;
  return { race: raceState.race, name: f.name.value.trim(), race_date: f.race_date.value, goal_seconds: parseClock(f.goal.value),
           runs_per_week: +f.runs_per_week.value, long_day: +f.long_day.value,
           current_weekly_mi: f.current_weekly_mi.value, current_long_mi: f.current_long_mi.value };
}
$$("#raceDist [data-race]").forEach((b) => (b.onclick = () => {
  raceState.race = b.dataset.race;
  $$("#raceDist [data-race]").forEach((x) => x.classList.toggle("seg-on", x === b));
}));
$("raceForm").onsubmit = async (e) => {
  e.preventDefault();
  try { raceState.preview = await api("/api/raceplans/preview", { method: "POST", body: JSON.stringify(raceBody()) }); renderPlan(raceState.preview, false); }
  catch (err) { toast(err.message); }
};
async function saveRace() {
  if (!$("raceForm").reportValidity()) return;
  try {
    const { id } = await api("/api/raceplans", { method: "POST", body: JSON.stringify(raceBody()) });
    raceState.current = id; raceState.preview = null; toast("Plan saved"); await loadRace();
  } catch (err) { toast(err.message); }
}
$("raceSave").onclick = saveRace;
$("raceNew").onclick = () => showRaceForm(true);
$("raceCancel").onclick = () => { showRaceForm(false); showPlan($("racePlanSel").value).catch((e) => toast(e.message)); };
$("racePlanSel").onchange = () => { showRaceForm(false); showPlan($("racePlanSel").value).catch((e) => toast(e.message)); };

// ---------- Health dashboard ----------
const heroLines = {};
const LIFT_RE = /weight|strength|powerlift|functional|lifting|crossfit/i;
const isSport = (w) => w.source !== "session" && !/run/i.test(w.type || "") && !LIFT_RE.test(w.type || "");
const n0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
let dashCache = null;

function dashData() {
  if (dashCache && Date.now() - dashCache.at < 6e4) return dashCache.p;
  const today = new Date(), y = today.getFullYear();
  const safe = (p) => p.catch(() => null);
  const p = Promise.all([
    safe(api(`/api/training/history?start=${y}-01-01`)),
    safe(api(`/api/running?year=${y}`)),
    safe(api(`/api/health/summary?start=${iso(new Date(Date.now() - 7 * 864e5))}&end=${iso(today)}`)),
    safe(api(`/api/workouts?start=${y}-01-01&end=${iso(today)}`)),
    safe(api(`/api/food/day?date=${localDay()}`)),
  ]).then(([lift, run, sleep, wk, food]) => {
    const month = localDay().slice(0, 7);
    const D = {};
    const sess = lift?.sessions || [];
    const mSess = sess.filter((s) => s.date.startsWith(month)).length;
    D.lifting = sess.length ? { big: String(mSess), unit: mSess === 1 ? "session this month" : "sessions this month",
      line: `Last: ${esc(sess[0].type || "Workout")}, ${shortDate(sess[0].date)}` } : { big: "—", unit: "", line: "Plan your week and log sessions live" };
    const wkMi = run?.weekly?.length ? run.weekly[run.weekly.length - 1].miles : 0;
    D.running = run?.runs?.length ? { big: wkMi.toFixed(1), unit: "mi this week", line: `${run.totals.miles.toFixed(0)} mi in ${y} · ${run.totals.runs} runs` }
      : { big: "—", unit: "", line: "Import Strava or connect WHOOP" };
    const L = sleep?.latest, lastSleep = sleep ? [...sleep.days].reverse().find((d) => d.sleep_hours != null) : null;
    D.sleep = L ? { big: `${Math.round(L.recovery)}%`, unit: "recovery", line: [lastSleep && `Slept ${fmtHours(lastSleep.sleep_hours)}`, L.hrv != null && `HRV ${Math.round(L.hrv)} ms`].filter(Boolean).join(" · ") }
      : { big: "—", unit: "", line: sleep?.whoop?.connected ? "Waiting for WHOOP data" : "Connect WHOOP" };
    const sp = (wk?.workouts || []).filter(isSport);
    const mSp = sp.filter((w) => w.date.startsWith(month));
    D.sports = sp.length ? { big: String(mSp.length), unit: mSp.length === 1 ? "session this month" : "sessions this month", line: `Last: ${esc(sp[0].type)}, ${shortDate(sp[0].date)}` }
      : { big: "—", unit: "", line: "Golf, basketball and more from WHOOP" };
    const kc = food?.totals?.kcal || 0, goal = food?.goals?.kcal;
    D.food = { big: n0.format(kc), unit: goal ? `of ${n0.format(goal)} cal today` : "cal today",
               line: goal ? (kc <= goal ? `${n0.format(goal - kc)} cal left` : `${n0.format(kc - goal)} cal over`) : "Set a daily goal in Food → Goals" };
    for (const [k, v] of Object.entries(D)) heroLines[k] = v.big === "—" ? v.line.replace(/<[^>]+>/g, "") : `${v.big} ${v.unit} · ${v.line}`.replace(/&amp;/g, "&");
    const r = parseRoute();
    if (r.section === "health" && HEALTH[r.area]) $("heroSub").textContent = heroLines[r.area] || "";
    return D;
  });
  dashCache = { at: Date.now(), p };
  return p;
}
function localDay(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function drawDash(D = {}) {
  $("hdGrid").innerHTML = Object.entries(HEALTH).map(([k, A]) => {
    const v = D[k];
    return `<a class="hd-card hd-${k}" data-area="${k}" href="#/health/${k}">
      <span class="hd-media"><img src="${UNSPLASH(A.img)}" alt="" loading="eager" /></span>
      <span class="hd-shade"></span>
      <span class="hd-body">
        <span class="hd-title">${esc(A.title)}</span>
        <span class="hd-stat">${v ? `<b>${v.big}</b>${v.unit ? `<span>${v.unit}</span>` : ""}` : `<b class="hd-skel"></b>`}</span>
        <span class="hd-line">${v ? v.line : "&nbsp;"}</span>
      </span>
      <span class="hd-go" aria-hidden="true">›</span>
    </a>`; }).join("");
}

async function loadHealthDash() {
  $("hdDate").textContent = new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  if (!$("hdGrid").children.length) { drawDash(); enter($("hdGrid"), { step: 60 }); }
  const D = await dashData();
  if (parseRoute().panel === "dashboard") drawDash(D);
}

// ---------- Sports (WHOOP + manual workouts other than runs and lifting) ----------
let sportYear = null;
async function loadSports() {
  const y = sportYear || new Date().getFullYear();
  const { workouts } = await api(`/api/workouts?start=${y}-01-01&end=${y}-12-31`);
  const sp = workouts.filter(isSport);
  const years = [];
  for (let yy = new Date().getFullYear(); yy >= new Date().getFullYear() - 3; yy--) years.push(yy);
  $("sportYear").innerHTML = years.map((v) => `<option ${v === y ? "selected" : ""}>${v}</option>`).join("");
  const mins = sp.reduce((a, w) => a + (w.duration_min || 0), 0);
  const strains = sp.map((w) => w.strain).filter((v) => v != null);
  const cals = sp.map((w) => w.calories).filter((v) => v != null);
  $("s-count").textContent = sp.length || "—";
  $("s-count-sub").textContent = sp.length ? `${new Set(sp.map((w) => w.type)).size} sports in ${y}` : `None logged in ${y}`;
  $("s-time").textContent = mins ? fmtHours(mins / 60) : "—";
  $("s-time-sub").textContent = sp.length && mins ? `${Math.round(mins / sp.length)} min per session` : "";
  $("s-strain").textContent = strains.length ? (strains.reduce((a, b) => a + b, 0) / strains.length).toFixed(1) : "—";
  $("s-cal").textContent = cals.length ? n0.format(cals.reduce((a, b) => a + b, 0)) : "—";
  $("s-cal-sub").textContent = cals.length ? `From ${cals.length} sessions with WHOOP data` : "";
  $("sportStatus").textContent = sp.length ? "" : "No sports sessions yet. They come from WHOOP, or log one under Weight Lifting → History.";
  const byType = {};
  sp.forEach((w) => { byType[w.type] = (byType[w.type] || 0) + (w.duration_min || 0) / 60; });
  const rows = Object.entries(byType).sort((a, b) => b[1] - a[1]);
  const top = rows[0]?.[1] || 1;
  $("sportBars").innerHTML = rows.length ? rows.map(([t, h]) => `<div class="bar-row"><span class="bar-label">${esc(t)}</span>
      <span class="bar-track"><span class="bar-fill" style="display:block;width:${Math.max(2, (h / top) * 100)}%"></span></span>
      <span class="bar-value">${h.toFixed(1)} h</span></div>`).join("") : `<div class="empty">Nothing yet</div>`;
  const last = y === new Date().getFullYear() ? new Date().getMonth() : 11;
  const months = Array.from({ length: last + 1 }, (_, m) => `${y}-${String(m + 1).padStart(2, "0")}`);
  drawChart("chart-sport-months", "bar", months.map((m) => new Date(m + "-01T00:00").toLocaleDateString("en-US", { month: "short" })),
    months.map((m) => sp.filter((w) => w.date.startsWith(m)).length), { tip: (v) => `${v} sessions`, tick: (v) => `${v}` }, { y: { min: 0, ticks: { precision: 0 } } });
  $("sportListMeta").textContent = sp.length ? `${Math.min(sp.length, 30)} of ${sp.length}` : "";
  $("sportList").innerHTML = sp.slice(0, 30).map((w) => `<div class="run-row sport-row">
      <span class="sport-date">${shortDate(w.date)}</span>
      <span class="run-main"><span class="run-name">${esc(w.type)}${w.source === "whoop" ? '<span class="source-tag">WHOOP</span>' : ""}</span><span class="run-date">${new Date(w.date + "T00:00").toLocaleDateString("en-US", { weekday: "long" })}</span></span>
      <span class="run-stat"><b>${w.duration_min ?? "—"}</b> min</span>
      <span class="run-stat"><b>${w.strain ?? "—"}</b> strain</span>
      <span class="run-stat run-opt"><b>${w.avg_hr ?? "—"}</b> bpm</span>
      <span class="run-stat run-opt"><b>${w.calories ?? "—"}</b> cal</span>
    </div>`).join("") || `<div class="empty">No sessions in ${y}</div>`;
}
$("sportYear").onchange = () => { sportYear = +$("sportYear").value; loadSports().catch(showError); };

// ---------- Food ----------
const MEAL_NAMES = { breakfast: "Breakfast", lunch: "Lunch", dinner: "Dinner", snacks: "Snacks" };
let foodDate = localDay();
let foodDay = null;
const addDays = (d, n) => localDay(new Date(new Date(d + "T00:00").getTime() + n * 864e5 + 36e5));
const kcal = (v) => n0.format(Math.round(v || 0));
const g1 = (v) => (v == null ? "—" : `${Math.round(v)} g`);

async function loadFoodDay() {
  $("foodDate").value = foodDate;
  const t = localDay();
  $("foodToday").classList.toggle("hidden", foodDate === t);
  $("foodDateLabel").textContent = foodDate === t ? "Today" : foodDate === addDays(t, -1) ? "Yesterday"
    : new Date(foodDate + "T00:00").toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" });
  const d = foodDay = await api(`/api/food/day?date=${foodDate}`);
  const G = d.goals || {}, T = d.totals;
  $("fs-goal").textContent = G.kcal ? kcal(G.kcal) : "—";
  $("fs-food").textContent = kcal(T.kcal);
  const left = G.kcal ? G.kcal - T.kcal : null;
  $("fs-left").textContent = left == null ? "—" : kcal(left);
  $("fs-left").parentElement.classList.toggle("over", left != null && left < 0);
  $("foodGoalHint").innerHTML = G.kcal ? "" : `No calorie goal yet. <a class="link-sm" href="#/health/food/goals">Set one</a>`;
  $("macroBars").innerHTML = [["protein", "Protein", G.protein_g], ["carbs", "Carbs", G.carbs_g], ["fat", "Fat", G.fat_g]].map(([k, label, goal]) => `
    <div class="macro"><div class="macro-top"><span>${label}</span><span><b>${Math.round(T[k] || 0)}</b>${goal ? ` / ${Math.round(goal)}` : ""} g</span></div>
      <span class="bar-track"><span class="bar-fill macro-${k}" style="display:block;width:${goal ? Math.min(100, ((T[k] || 0) / goal) * 100) : 0}%"></span></span></div>`).join("");
  $("foodMeals").innerHTML = Object.entries(MEAL_NAMES).map(([m, label]) => {
    const items = d.meals[m] || [];
    return `<div class="card meal-card">
      <div class="meal-head"><h3>${label}</h3><span class="meal-kcal">${items.length ? `${kcal(d.meal_totals[m])} cal` : ""}</span></div>
      ${items.map((e) => `<button class="food-row" data-entry="${esc(e.id)}">
          <span class="fr-main"><span class="fr-name">${esc(e.name)}</span><span class="fr-sub">${esc(entryAmount(e))}${e.brand ? ` · ${esc(e.brand)}` : ""}</span></span>
          <span class="fr-macros">${e.protein != null ? `P ${Math.round(e.protein)}` : ""}${e.carbs != null ? ` · C ${Math.round(e.carbs)}` : ""}${e.fat != null ? ` · F ${Math.round(e.fat)}` : ""}</span>
          <span class="fr-kcal">${kcal(e.kcal)}</span></button>`).join("")}
      <div class="meal-actions"><button class="text-btn accent" data-add="${m}">Add food</button>
        ${items.length ? "" : `<button class="text-btn" data-copy="${m}">Copy from yesterday</button>`}</div>
    </div>`; }).join("");
  $$("[data-add]").forEach((b) => (b.onclick = () => openFoodDialog(b.dataset.add)));
  $$("[data-copy]").forEach((b) => (b.onclick = async () => {
    try { const r = await api("/api/food/copy", { method: "POST", body: JSON.stringify({ from: addDays(foodDate, -1), to: foodDate, meal: b.dataset.copy }) });
          toast(`Copied ${r.copied} item${r.copied === 1 ? "" : "s"}`); dashCache = null; loadFoodDay(); }
    catch (e) { toast(e.message); }
  }));
  $$("[data-entry]").forEach((b) => (b.onclick = () => editEntry(b.dataset.entry)));
}
function entryAmount(e) {
  if (e.quick) return "Quick add";
  if (e.food?.per_serving) return `${+e.servings} × ${e.serving_label || "serving"}`;
  if (e.servings && e.serving_label) return `${+e.servings} × ${e.serving_label}`;
  return `${Math.round(e.grams)} g`;
}
$("foodPrev").onclick = () => { foodDate = addDays(foodDate, -1); loadFoodDay().catch(showError); };
$("foodNext").onclick = () => { foodDate = addDays(foodDate, 1); loadFoodDay().catch(showError); };
$("foodToday").onclick = () => { foodDate = localDay(); loadFoodDay().catch(showError); };
$("foodDate").onchange = () => { if ($("foodDate").value) { foodDate = $("foodDate").value; loadFoodDay().catch(showError); } };
$("foodDateBtn").onclick = () => { try { $("foodDate").showPicker(); } catch { $("foodDate").focus(); } };

// Add / edit dialog
let foodCtx = { meal: "breakfast", food: null, entry: null, tab: "results", results: [], recent: null, mine: null };
function openFoodDialog(meal) {
  foodCtx = { ...foodCtx, meal, food: null, entry: null };
  $("foodDlgTitle").textContent = `Add to ${MEAL_NAMES[meal]}`;
  showFoodPick(true);
  $("foodDialog").showModal();
  $("foodSearch").focus();
  setFoodTab(foodCtx.tab === "quick" || foodCtx.tab === "create" ? "results" : foodCtx.tab);
}
function showFoodPick(on) {
  $("foodPick").classList.toggle("hidden", !on);
  $("foodAmount").classList.toggle("hidden", on);
}
async function setFoodTab(tab) {
  foodCtx.tab = tab;
  $$("#foodTabs [data-ft]").forEach((b) => b.classList.toggle("seg-on", b.dataset.ft === tab));
  $("quickForm").classList.toggle("hidden", tab !== "quick");
  $("createForm").classList.toggle("hidden", tab !== "create");
  $("foodResults").classList.toggle("hidden", tab === "quick" || tab === "create");
  $("foodSearch").classList.toggle("hidden", tab === "quick" || tab === "create");
  if (tab === "recent" && !foodCtx.recent) foodCtx.recent = (await api("/api/food/recent")).recent;
  if (tab === "mine" && !foodCtx.mine) foodCtx.mine = (await api("/api/food/custom")).foods;
  drawFoodResults();
}
$$("#foodTabs [data-ft]").forEach((b) => (b.onclick = () => setFoodTab(b.dataset.ft).catch((e) => toast(e.message))));

function foodLine(f) {
  const per = f.per_serving ? `${kcal(f.per_serving.kcal)} cal per ${f.serving_label || "serving"}`
    : f.servings?.[0] ? `${kcal((f.per100g.kcal * f.servings[0].grams) / 100)} cal per ${f.servings[0].label}` : `${kcal(f.per100g.kcal)} cal per 100 g`;
  return per;
}
function drawFoodResults() {
  const t = foodCtx.tab;
  const q = $("foodSearch").value.trim().toLowerCase();
  let list = t === "recent" ? (foodCtx.recent || []).map((r) => ({ ...r.food, _last: r })) : t === "mine" ? (foodCtx.mine || []) : foodCtx.results;
  if (t !== "results" && q) list = list.filter((f) => `${f.name} ${f.brand || ""}`.toLowerCase().includes(q));
  const empty = t === "results" ? (q.length < 2 ? "Type at least two letters to search USDA and Open Food Facts." : foodCtx.searching ? "Searching…" : "No matches. Try fewer words, or Create food.")
    : t === "recent" ? "Foods you log show up here." : "Foods you create show up here.";
  $("foodResults").innerHTML = list.map((f, i) => `<button class="fres" data-i="${i}">
      <span class="fr-main"><span class="fr-name">${esc(f.name)}</span><span class="fr-sub">${[f.brand, foodLine(f)].filter(Boolean).map(esc).join(" · ")}</span></span>
      <span class="fr-src">${f.source === "usda" ? (f.kind === "Branded" ? "USDA branded" : "USDA") : f.source === "off" ? "Open Food Facts" : f.source === "custom" ? "Mine" : ""}</span>
    </button>`).join("") || `<div class="empty">${empty}</div>`;
  $$("#foodResults [data-i]").forEach((b) => (b.onclick = () => pickFood(list[+b.dataset.i])));
}
let searchTimer = null, searchSeq = 0;
$("foodSearch").oninput = () => {
  if (foodCtx.tab !== "results") return drawFoodResults();
  clearTimeout(searchTimer);
  const q = $("foodSearch").value.trim();
  if (q.length < 2) { foodCtx.results = []; return drawFoodResults(); }
  foodCtx.searching = true; drawFoodResults();
  searchTimer = setTimeout(async () => {
    const seq = ++searchSeq;
    try {
      const r = await api(`/api/food/search?q=${encodeURIComponent(q)}`);
      if (seq !== searchSeq) return;
      foodCtx.results = r.foods; foodCtx.searching = false;
      if (r.errors?.length && !r.foods.length) toast("Food search is unavailable right now. Use Quick add or Create food.");
      drawFoodResults();
    } catch (e) { foodCtx.searching = false; toast(e.message); drawFoodResults(); }
  }, 350);
};

// amount step: units are the food's servings, grams, and 100 g (or servings for my foods)
function unitsFor(f) {
  if (f.per_serving) return [{ label: f.serving_label || "serving", grams: null }];
  return [...(f.servings || []), { label: "g", grams: 1 }, { label: "100 g", grams: 100 }, { label: "oz", grams: 28.3495 }];
}
function pickFood(f, entry = null) {
  foodCtx.food = f; foodCtx.entry = entry;
  showFoodPick(false);
  $("faName").textContent = f.name;
  $("faBrand").textContent = [f.brand, f.source === "usda" ? "USDA FoodData Central" : f.source === "off" ? "Open Food Facts" : f.source === "custom" ? "My food" : ""].filter(Boolean).join(" · ");
  const units = unitsFor(f);
  $("faUnit").innerHTML = units.map((u, i) => `<option value="${i}">${esc(u.label)}</option>`).join("");
  const last = entry || f._last;
  let qty = 1, ui = 0;
  if (last && !f.per_serving) {
    const j = units.findIndex((u) => u.label === last.serving_label);
    if (last.servings && j >= 0) { ui = j; qty = last.servings; } else { ui = units.findIndex((u) => u.label === "g"); qty = Math.round(last.grams); }
  } else if (last?.servings) qty = last.servings;
  $("faUnit").value = String(ui); $("faQty").value = qty;
  $("faMeal").value = entry?.meal || foodCtx.meal;
  $("faSave").textContent = entry ? "Save" : "Add";
  $("faDelete").classList.toggle("hidden", !entry);
  $("foodBack").classList.toggle("hidden", !!entry);
  $("foodDlgTitle").textContent = entry ? "Edit entry" : `Add to ${MEAL_NAMES[foodCtx.meal]}`;
  drawAmount();
}
function amountNow() {
  const f = foodCtx.food, u = unitsFor(f)[+$("faUnit").value], q = +$("faQty").value || 0;
  if (f.per_serving) return { servings: q, n: Object.fromEntries(Object.entries(f.per_serving).map(([k, v]) => [k, v * q])) };
  const grams = q * u.grams;
  return { grams, servings: u.grams > 1 && !["100 g", "oz"].includes(u.label) ? q : null, serving_label: u.grams > 1 && !["100 g", "oz"].includes(u.label) ? u.label : "",
           n: Object.fromEntries(Object.entries(f.per100g).map(([k, v]) => [k, (v * grams) / 100])) };
}
function drawAmount() {
  const { n, grams } = amountNow();
  $("faNutr").innerHTML = [["kcal", "Calories", ""], ["protein", "Protein", " g"], ["carbs", "Carbs", " g"], ["fat", "Fat", " g"], ["fiber", "Fiber", " g"], ["sugar", "Sugar", " g"], ["sodium_mg", "Sodium", " mg"]]
    .filter(([k]) => n[k] != null).map(([k, l, u]) => `<div><b>${k === "kcal" ? kcal(n[k]) : Math.round(n[k] * 10) / 10}${u}</b><span>${l}</span></div>`).join("")
    + (grams ? `<div><b>${Math.round(grams)} g</b><span>Weight</span></div>` : "");
}
$("faQty").oninput = drawAmount; $("faUnit").onchange = drawAmount;
$("foodBack").onclick = () => showFoodPick(true);
$("foodDlgClose").onclick = () => $("foodDialog").close();
$("foodDialog").addEventListener("click", (e) => { if (e.target === $("foodDialog")) $("foodDialog").close(); });
$("foodAmount").onsubmit = async (e) => {
  e.preventDefault();
  const a = amountNow(), f = foodCtx.food;
  if (!(+$("faQty").value > 0)) return toast("Enter an amount");
  try {
    if (foodCtx.entry) {
      await api(`/api/food/entries/${foodDate}/${foodCtx.entry.id}`, { method: "PATCH", body: JSON.stringify(
        f.per_serving ? { servings: a.servings, meal: $("faMeal").value } : { grams: a.grams, servings: a.servings, meal: $("faMeal").value }) });
    } else {
      const { _last, ...food } = f;
      await api("/api/food/entries", { method: "POST", body: JSON.stringify({ date: foodDate, meal: $("faMeal").value, food, grams: a.grams,
        servings: a.servings, serving_label: a.serving_label }) });
      foodCtx.recent = null;
    }
    $("foodDialog").close(); dashCache = null; loadFoodDay();
  } catch (err) { toast(err.message); }
};
$("faDelete").onclick = async () => {
  await api(`/api/food/entries/${foodDate}/${foodCtx.entry.id}`, { method: "DELETE" });
  $("foodDialog").close(); dashCache = null; loadFoodDay();
};
function editEntry(id) {
  const e = Object.values(foodDay.meals).flat().find((x) => x.id === id);
  if (!e) return;
  if (e.quick || !e.food) {
    if (confirm(`Remove "${e.name}" (${kcal(e.kcal)} cal)?`))
      api(`/api/food/entries/${foodDate}/${id}`, { method: "DELETE" }).then(() => { dashCache = null; loadFoodDay(); }).catch((x) => toast(x.message));
    return;
  }
  foodCtx.meal = e.meal;
  $("foodDialog").showModal();
  pickFood(e.food, e);
}
$("quickForm").onsubmit = async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData($("quickForm")));
  try {
    await api("/api/food/entries", { method: "POST", body: JSON.stringify({ date: foodDate, meal: foodCtx.meal, quick: true, ...f }) });
    $("quickForm").reset(); $("foodDialog").close(); dashCache = null; loadFoodDay();
  } catch (err) { toast(err.message); }
};
$("createForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const { food } = await api("/api/food/custom", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData($("createForm")))) });
    $("createForm").reset(); foodCtx.mine = null;
    pickFood(food);
  } catch (err) { toast(err.message); }
};

async function loadFoodHistory() {
  const h = await api("/api/food/history?days=30");
  const A = h.average, G = h.goals || {};
  $("fh-kcal").textContent = A ? kcal(A.kcal) : "—";
  $("fh-kcal-sub").textContent = A ? `${h.logged_days} logged day${h.logged_days === 1 ? "" : "s"}${G.kcal ? ` · goal ${kcal(G.kcal)}` : ""}` : "Nothing logged in the last 30 days";
  $("fh-p").textContent = A ? g1(A.protein) : "—"; $("fh-c").textContent = A ? g1(A.carbs) : "—"; $("fh-f").textContent = A ? g1(A.fat) : "—";
  $("fh-meta").textContent = G.kcal ? `Line marks your ${kcal(G.kcal)} cal goal` : "";
  drawChart("chart-food", "bar", h.days.map((d) => shortDate(d.date)), h.days.map((d) => d.kcal || 0), { tip: (v) => `${kcal(v)} cal`, tick: (v) => kcal(v) }, { y: { min: 0 } });
  if (G.kcal && charts["chart-food"]) {
    const c = charts["chart-food"];
    c.data.datasets.push({ type: "line", data: h.days.map(() => G.kcal), borderColor: cssVar("--text-3"), borderDash: [4, 4], borderWidth: 1.5, pointRadius: 0, fill: false });
    c.update();
  }
  $("foodDays").innerHTML = [...h.days].reverse().filter((d) => d.kcal).map((d) => `<button class="food-day-row" data-day="${d.date}">
      <span>${new Date(d.date + "T00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}</span>
      <span class="fr-macros">P ${Math.round(d.protein)} · C ${Math.round(d.carbs)} · F ${Math.round(d.fat)}</span>
      <span class="fr-kcal">${kcal(d.kcal)}</span></button>`).join("") || `<div class="empty">Log food on the Today tab and your days show up here.</div>`;
  $$("[data-day]").forEach((b) => (b.onclick = () => { foodDate = b.dataset.day; location.hash = "#/health/food/today"; }));
}

async function loadFoodGoals() {
  const g = await api("/api/food/goals");
  const f = $("foodGoalsForm").elements;
  for (const k of ["kcal", "protein_g", "carbs_g", "fat_g"]) f[k].value = g[k] ?? "";
  macroCheck();
}
function macroCheck() {
  const f = $("foodGoalsForm").elements;
  const p = +f.protein_g.value || 0, c = +f.carbs_g.value || 0, fat = +f.fat_g.value || 0, k = +f.kcal.value || 0;
  const m = p * 4 + c * 4 + fat * 9;
  $("macroCheck").textContent = m ? `Your macros add up to ${kcal(m)} cal${k ? ` (${m > k ? "+" : ""}${kcal(m - k)} vs. the calorie goal)` : ""}. Protein and carbs are 4 cal per gram, fat 9.` : "";
}
$("foodGoalsForm").oninput = macroCheck;
$("foodGoalsForm").onsubmit = async (e) => {
  e.preventDefault();
  try { await api("/api/food/goals", { method: "PUT", body: JSON.stringify(Object.fromEntries(new FormData($("foodGoalsForm")))) }); toast("Goals saved"); dashCache = null; }
  catch (err) { toast(err.message); }
};
let tdee = null;
$("tdeeForm").onsubmit = (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData($("tdeeForm")));
  const kg = f.weight_lb * 0.45359237, cm = f.height_in * 2.54;
  const bmr = 10 * kg + 6.25 * cm - 5 * f.age + (f.sex === "m" ? 5 : -161);
  const maint = bmr * +f.activity;
  tdee = Math.round((maint + +f.delta) / 10) * 10;
  $("tdeeOut").innerHTML = `Resting about <b>${kcal(bmr)}</b> cal · maintenance about <b>${kcal(maint)}</b> · goal <b>${kcal(tdee)}</b> cal a day`;
  $("tdeeUse").classList.remove("hidden");
};
$("tdeeUse").onclick = () => { $("foodGoalsForm").elements.kcal.value = tdee; macroCheck(); toast("Calorie goal filled in. Save goals to keep it."); };

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
  if (tab === "today") return loadToday();
  if (tab === "tasks") return loadTasks();
  if (tab === "plan") tab = EDU_PLAN_SUBS.includes(routeQuery.get("sub")) ? routeQuery.get("sub") : "overview";
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
  if (tab === "calendar") renderAcadCal();
  if (tab === "syllabi") renderSyllabi();
}

// Same math as education.compute(), used for instant what-if on the Grades tab.
function liveGpa() {
  const GP = edu.computed.grade_points, target = +$("targetGpa").value || edu.computed.profile.target_gpa;
  const counts = new Set(["completed", "enrolled", "planned"]);
  let aq = 0, ag = 0, pq = 0, pg = 0; const terms = {};
  edu.courses.forEach((c) => {
    if (!counts.has(c.status)) return;
    const d = gradeDraft[c.id] || {}; const g = d.grade ?? c.grade, gs = d.grade_status ?? c.grade_status;
    if (!(g in GP) || (c.pf_elected && gs !== "actual")) return;
    const cr = +c.credits || 0, q = GP[g] * cr;
    pq += q; pg += cr; (terms[c.term] ||= { q: 0, g: 0 }); terms[c.term].q += q; terms[c.term].g += cr;
    if (gs === "actual") { aq += q; ag += cr; }
  });
  const rem = edu.courses.filter((c) => ["enrolled", "planned"].includes(c.status) && (gradeDraft[c.id]?.grade_status ?? c.grade_status) !== "actual"
    && c.graded !== false && !c.pf_elected && !["P", "H", "CR"].includes(gradeDraft[c.id]?.grade ?? c.grade));
  const remCr = rem.reduce((a, c) => a + (+c.credits || 0), 0);
  return { actual: ag ? r2(aq / ag) : null, projected: pg ? r2(pq / pg) : null,
           terms: Object.fromEntries(Object.entries(terms).map(([t, v]) => [t, v.g ? r2(v.q / v.g) : null])),
           needed: remCr ? (target * (ag + remCr) - aq) / remCr : null, remCr, target };
}

const pfStatus = (a) => (a.remaining < 0 ? "Over" : a.remaining === 0 ? "All used" : "Available");

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
  ch.data.datasets[0].backgroundColor = terms.map((t) => (t.gpa_actual != null ? acc : acc + "66"));
  ch.options.scales.x.ticks.autoSkip = false; ch.update("none");          // every term labeled, even at phone width

  $("eduAuditMini").innerHTML = C.audit.map((a) => `<div class="audit-mini"><span class="pill pill-${a.status}">${a.type === "pf" ? pfStatus(a) : { met: "Met", on_track: "On track", missing: "Missing" }[a.status]}</span><span>${esc(a.label)}</span></div>`).join("");

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
        return `<tr class="${gradeDraft[c.id] ? "dirty" : ""}"><td>${esc(c.title)}<button class="gt-type" data-gs="${c.id}">${gs === "actual" ? "Actual" : "Projected"}</button></td><td class="num">${c.credits ?? "—"}</td>
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
    if (a.type === "pf") {
      const cells = []; const push = (n, cls) => { for (let i = 0; i < n; i++) cells.push(cls); };
      push(a.used, "used"); push(a.in_progress, "prog"); push(a.planned, "plan");
      const shown = cells.slice(0, a.cap).concat(Array(Math.max(0, a.cap - cells.length)).fill(""));
      if (cells.length > a.cap) shown.push(...Array(cells.length - a.cap).fill("over"));
      body = `<div class="pf-meter">${shown.map((c) => `<i class="${c}"></i>`).join("")}</div>
        <div class="stack-legend"><span><i class="lg lg-done"></i>${a.used} used${a.prior ? ` (${a.prior} from earlier terms)` : ""}</span>${a.in_progress ? `<span><i class="lg lg-prog"></i>${a.in_progress} this term</span>` : ""}<span><i class="lg lg-plan"></i>${a.planned} planned</span><span class="${a.remaining < 0 ? "neg" : ""}"><strong>${a.remaining < 0 ? `${-a.remaining} over the limit` : `${a.remaining} left`}</strong></span></div>
        <div class="pf-rows">${a.courses.map((c) => `<div><span>${esc(c.title)} <span class="tag">${esc(c.term)} · ${c.credits} cr</span>${c.inferred ? ` <button class="text-btn" data-pfmand="${c.id}" title="Required pass/fail courses don't use the option">Was a required P/F course</button>` : ""}</span><span>${c.outcome === "planned" ? "planned" : c.outcome === "P" ? "Pass" : esc(c.outcome) + " (above target)"}</span></div>`).join("")}</div>
        <form class="pf-prior" id="pfPriorForm"><label>Used in terms not listed here <input class="input mini" id="pfPrior" type="number" min="0" max="20" step="0.5" value="${a.prior || 0}" /> credits</label><button class="btn btn-secondary btn-sm">Save</button></form>
        <p class="hint">Credits count against the limit whether you get a Pass or keep the letter grade. One P/F course per semester; not for required courses, writing seminars, clinics or supervised research.</p>`;
      return `<div class="audit-row"><div class="audit-title"><span class="pill pill-${a.status}">${pfStatus(a)}</span><h3>${esc(a.label)}</h3></div>${body}</div>`;
    }
    if (a.courses) body += `<div class="audit-courses">${a.courses.length ? a.courses.map(esc).join(" · ") : "No course tagged yet. Open a course in the Planner and check this requirement."}</div>`;
    return `<div class="audit-row"><div class="audit-title"><span class="pill pill-${a.status}">${{ met: "Met", on_track: "On track", missing: "Missing" }[a.status]}</span><h3>${esc(a.label)}</h3></div>${body}</div>`;
  }).join("");
  const pf = $("pfPriorForm");
  if (pf) pf.onsubmit = async (e) => {
    e.preventDefault();
    try { edu = await api("/api/edu/profile", { method: "PUT", body: JSON.stringify({ pf_prior_credits: +$("pfPrior").value || 0 }) }); toast("P/F credits updated"); renderAudit(); }
    catch (err) { toast(err.message); }
  };
  $$("[data-pfmand]").forEach((b) => (b.onclick = async () => {
    try { edu = await api(`/api/edu/courses/${b.dataset.pfmand}`, { method: "PATCH", body: JSON.stringify({ pf_mandatory: true, graded: false }) }); toast("Marked as a required P/F course"); renderAudit(); }
    catch (err) { toast(err.message); }
  }));
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
    (missing.length ? `<div class="alert">Still unmet after this plan: ${missing.map((m) => esc(m.label)).join(", ")}</div>` : "") +
    ((pl.pf_elected || []).length > 1 ? `<div class="alert alert-bad">Only one P/F option course per semester. Checked: ${pl.pf_elected.map(esc).join(", ")}</div>` : "") +
    (C.pass_fail.remaining < 0 ? `<div class="alert alert-bad">P/F option is ${-C.pass_fail.remaining} credit${C.pass_fail.remaining === -1 ? "" : "s"} over the ${C.pass_fail.cap}-credit limit.</div>` : "");
  const pfLeft = C.pass_fail.remaining;
  const pfBox = (c) => {
    if (c.status === "completed" || c.status === "dropped") return "";
    if (c.pf_mandatory || (c.graded === false && !c.pf_elected)) return "";   // mandatory P/F
    if ((c.tags || []).some((t) => t === "pr" || t === "ulwr")) return "";      // not eligible
    const cr = +c.credits || 0;
    if (!cr) return "";
    const sameTerm = edu.courses.find((x) => x.pf_elected && x.id !== c.id && x.term === c.term && !["dropped", "waitlisted"].includes(x.status));
    if (!c.pf_elected && sameTerm && sameTerm.status === "completed") return "";   // one P/F option course per semester
    let swap = null;
    if (!c.pf_elected && cr > pfLeft) {   // budget used: offer to move it from another not-yet-taken course
      swap = edu.courses.find((x) => x.pf_elected && x.id !== c.id && !["completed", "dropped"].includes(x.status) && pfLeft + (+x.credits || 0) >= cr);
      if (!swap) return "";
    }
    const label = c.pf_elected ? "Use P/F credit" : swap ? `Use P/F credit here instead of ${esc(swap.code || swap.title)}` : `Use P/F credit (${pfLeft} left)`;
    return `<label class="pf-check ${c.pf_elected ? "on" : ""}"><input type="checkbox" data-pf="${c.id}" ${swap ? `data-swap="${swap.id}"` : ""} ${c.pf_elected ? "checked" : ""} /> ${label}</label>`;
  };
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
        <div class="course-meta">${[c.code, c.credits != null ? `${c.credits} cr` : null, c.graded === false && !c.pf_elected ? "Mandatory P/F" : null, c.exam_type].filter(Boolean).map(esc).join(" · ")}${c.pf_elected ? ' <span class="pf-badge">P/F option</span>' : ""}</div>
        ${c.exam_date ? `<div class="course-meta">Final ${esc(fmtExam(c))}</div>` : ""}
        ${c.meetings_text ? `<div class="course-meta">${esc(c.meetings_text)}</div>` : ""}
        ${c.notes ? `<div class="course-note">${esc(c.notes)}</div>` : ""}
        ${pfBox(c)}
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
  $$("[data-pf]").forEach((b) => {
    b.closest("label").onclick = (e) => e.stopPropagation();
    b.onchange = async () => {
      const c = edu.courses.find((x) => x.id === b.dataset.pf);
      const body = { pf_elected: b.checked, graded: true };
      if (!b.checked && c.grade === "P" && c.grade_status !== "actual") body.grade = null;
      try {
        if (b.checked && b.dataset.swap) {
          const o = edu.courses.find((x) => x.id === b.dataset.swap);
          await api(`/api/edu/courses/${o.id}`, { method: "PATCH", body: JSON.stringify({ pf_elected: false, ...(o.grade === "P" && o.grade_status !== "actual" ? { grade: null } : {}) }) });
        }
        edu = await api(`/api/edu/courses/${c.id}`, { method: "PATCH", body: JSON.stringify(body) }); renderPlanner();
        toast(b.checked ? (b.dataset.swap ? "P/F credit moved" : "P/F credit applied") : "P/F credit released");
      }
      catch (e) { toast(e.message); b.checked = !b.checked; }
    };
  });
  $$("[data-course]").forEach((card) => (card.onclick = () => openCourse(edu.courses.find((c) => c.id === card.dataset.course))));
}
const fmtExam = (c) => `${new Date(c.exam_date + "T00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}${c.exam_time ? `, ${c.exam_time}` : ""}`;
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
  ["title", "code", "crn", "term", "credits", "status", "priority", "grade_status", "meetings_text", "professor", "exam_type", "exam_date", "exam_time", "category", "waitlist", "notes", "experiential_credits"]
    .forEach((k) => { if (f.elements[k]) f.elements[k].value = v[k] ?? (k === "grade_status" ? "projected" : ""); });
  f.elements.grade.value = v.grade || "";
  f.elements.pf.checked = !!v.pf_mandatory || (v.graded === false && !v.pf_elected && v.grade !== "P");
  f.elements.pf_elected.checked = !!v.pf_elected;
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
    pf_elected: (f.elements.pf_elected.checked || (val("grade") === "P" && val("status") === "completed")) && !f.elements.pf.checked,
    pf_mandatory: f.elements.pf.checked, exam_date: val("exam_date") || null, exam_time: val("exam_time"),
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

const eduImport = (inp) => async () => {
  const f = inp.files[0]; inp.value = ""; if (!f) return;
  try {
    const payload = JSON.parse(await f.text());
    if (!payload.merge && edu?.courses?.length && !confirm("This file replaces all your courses. Continue?")) return;
    const r = await api("/api/edu/import", { method: "POST", body: JSON.stringify(payload) });
    toast(payload.merge ? `Updated ${r.updated} courses${r.added ? `, added ${r.added}` : ""}` : `Imported ${r.imported} courses`); render();
  } catch (e) { toast(e instanceof SyntaxError ? "That file isn't valid JSON" : e.message); }
};
$("eduImportFile").onchange = eduImport($("eduImportFile"));
$("eduImportFile2").onchange = eduImport($("eduImportFile2"));

// Syllabi: one card per current course — what's next, grading, contacts, the full class schedule, the original file
let sylUploadTarget = null;   // {syllabus_id} or {course_id} for the hidden file input
const SYL_KIND = { assignment: "Due", paper: "Paper", presentation: "Presentation", exam: "Exam", reading: "Reading", other: "Note" };
function sylTerm() {
  const terms = edu.computed.profile.terms || [];
  return terms.find((t) => edu.courses.some((c) => c.term === t && c.status === "enrolled")) || terms[terms.length - 1];
}
function sylItems(s) {   // sessions + deadlines on one dated list
  const rows = (s.sessions || []).map((r) => ({ ...r, type: r.no_class ? "off" : "class" }))
    .concat((s.deadlines || []).map((d) => ({ ...d, type: "due" })));
  return rows.sort((a, b) => (a.date || "9999").localeCompare(b.date || "9999") || (a.type === "due") - (b.type === "due"));
}
function renderSyllabi() {
  const term = sylTerm(), today = dstr(new Date()), syl = edu.syllabi || [];
  const courses = edu.courses.filter((c) => c.term === term && ["enrolled", "planned"].includes(c.status));
  const byCourse = Object.fromEntries(syl.filter((s) => s.course_id).map((s) => [s.course_id, s]));
  const loose = syl.filter((s) => !s.course_id || !courses.some((c) => c.id === s.course_id));
  $("sylTerm").textContent = term ? `${term} · ${courses.length} course${courses.length === 1 ? "" : "s"}` : "";
  const fd = (d, o = { weekday: "short", month: "short", day: "numeric" }) => new Date(d + "T00:00").toLocaleDateString("en-US", o);

  // next 7 days across every syllabus
  const week = dstr(new Date(Date.now() + 7 * 864e5));
  const soon = syl.flatMap((s) => sylItems(s).filter((r) => r.date && r.date >= today && r.date <= week && (r.type === "due" || r.reading || r.type === "off"))
    .map((r) => ({ ...r, course: s.short || edu.courses.find((c) => c.id === s.course_id)?.title || s.course_title })))
    .sort((a, b) => a.date.localeCompare(b.date));
  $("sylWeek").innerHTML = syl.length ? `<div class="card-head"><h3>Next 7 days</h3><span class="card-meta">From your syllabi</span></div>` +
    (soon.length ? soon.map((r) => `<div class="syl-row ${r.type}"><span class="syl-date">${fd(r.date)}</span><span><strong>${esc(r.course)}</strong> · ${r.type === "due" ? `<span class="syl-badge">${SYL_KIND[r.kind] || "Due"}</span> ${esc(r.title)}${r.time ? ` · ${esc(r.time)}` : ""}` : r.type === "off" ? "No class" : esc(r.reading)}</span></div>`).join("")
      : `<p class="hint">Nothing due or assigned this week.</p>`) : "";
  $("sylWeek").classList.toggle("hidden", !syl.length);

  const card = (c, s) => {
    const title = c?.title || s?.course_title || "Course";
    const head = `<div class="syl-head"><div><h3>${esc(title)}</h3><p class="syl-sub">${esc([s?.professor || c?.professor, s?.meets || c?.meetings_text, s?.location].filter(Boolean).join(" · "))}</p></div>
      <div class="syl-actions">${s?.has_file ? `<button class="btn btn-secondary btn-sm" data-sylfile="${s.id}">Original</button>` : ""}
        <button class="btn ${s ? "btn-secondary" : "btn-primary"} btn-sm" data-sylup="${s ? `s:${s.id}` : `c:${c.id}`}">${s?.has_file ? "Replace file" : "Upload syllabus"}</button></div></div>`;
    if (!s) return `<div class="card syl-card">${head}<p class="hint">No syllabus yet. Upload the PDF or Word file and it's read into dates, readings and grading.</p></div>`;
    const items = sylItems(s), next = items.filter((r) => r.date && r.date >= today);
    const nextDue = next.find((r) => r.type === "due"), nextClass = next.find((r) => r.type === "class");
    const link = !c ? `<label class="syl-link">Course <select class="select" data-syllink="${s.id}"><option value="">Not linked</option>${courses.map((x) => `<option value="${x.id}">${esc(x.title)}</option>`).join("")}</select></label>` : "";
    return `<div class="card syl-card">${head}${link}
      ${s.summary ? `<p class="syl-summary">${esc(s.summary)}</p>` : ""}
      <div class="syl-next">
        <div><span class="kpi-label">Next class</span>${nextClass ? `<strong>${fd(nextClass.date)}</strong><span>${esc(nextClass.title || "")}</span>${nextClass.reading ? `<span class="syl-read">${esc(nextClass.reading)}</span>` : ""}` : `<span class="muted">No more classes listed</span>`}</div>
        <div><span class="kpi-label">Next due</span>${nextDue ? `<strong>${fd(nextDue.date)}${nextDue.time ? ` · ${esc(nextDue.time)}` : ""}</strong><span>${esc(nextDue.title)}</span>` : `<span class="muted">Nothing else due</span>`}</div>
      </div>
      <div class="syl-grid">
        ${(s.grading || []).length ? `<div><h4>Grading</h4>${s.grading.map((g) => `<div class="syl-kv"><span>${esc(g.item)}</span><span>${esc(g.weight || "")}</span></div>`).join("")}${s.exam ? `<p class="syl-note">${esc(s.exam)}</p>` : ""}</div>` : ""}
        <div><h4>Contact</h4>${[["Email", s.email], ["Office", s.office], ["Office hours", s.office_hours], ["Materials", s.textbook]].filter(([, v]) => v).map(([k, v]) => `<div class="syl-kv"><span>${k}</span><span>${k === "Email" ? `<a href="mailto:${esc(v)}">${esc(v)}</a>` : esc(v)}</span></div>`).join("") || `<p class="hint">—</p>`}</div>
      </div>
      ${(s.policies || []).length ? `<details class="syl-more"><summary>Policies (${s.policies.length})</summary><ul>${s.policies.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></details>` : ""}
      ${items.length ? `<details class="syl-more"><summary>Full schedule (${items.length})</summary><div class="syl-sched">${items.map((r) => `<div class="syl-row ${r.type} ${r.date && r.date < today ? "past" : ""}"><span class="syl-date">${r.date ? fd(r.date) : "TBA"}</span><span>${r.type === "due" ? `<span class="syl-badge">${SYL_KIND[r.kind] || "Due"}</span> ` : ""}${r.type === "off" ? "<em>No class</em> " : ""}<strong>${esc(r.title || "")}</strong>${r.time ? ` · ${esc(r.time)}` : ""}${r.reading ? `<span class="syl-read">${esc(r.reading)}</span>` : ""}${r.note ? `<span class="syl-note">${esc(r.note)}</span>` : ""}</span></div>`).join("")}</div></details>` : ""}
      ${s.parse_error ? `<p class="hint neg">Couldn't read the file automatically: ${esc(s.parse_error)}</p>` : ""}
      <div class="syl-foot">${s.file_name ? `<span class="tag">${esc(s.file_name)}</span>` : ""}<span style="flex:1"></span><button class="text-btn danger" data-syldel="${s.id}">Remove</button></div>
    </div>`;
  };
  $("sylList").innerHTML = courses.map((c) => card(c, byCourse[c.id])).join("") + loose.map((s) => card(null, s)).join("")
    || `<div class="card empty-state"><h3>No courses this term</h3><p>Add your courses in the Planner, then upload each syllabus here.</p></div>`;

  $$("[data-sylup]").forEach((b) => (b.onclick = () => {
    const [k, id] = b.dataset.sylup.split(":");
    sylUploadTarget = k === "s" ? { syllabus_id: id } : { course_id: id };
    $("sylFile").click();
  }));
  $$("[data-sylfile]").forEach((b) => (b.onclick = async () => {
    const w = window.open("", "_blank");
    try {
      const res = await fetch(`${API_BASE}/api/edu/syllabi/${b.dataset.sylfile}/file`, { headers: { Authorization: `Bearer ${await auth.currentUser.getIdToken()}` } });
      if (!res.ok) throw new Error("Couldn't open the file");
      const url = URL.createObjectURL(await res.blob());
      if (w) w.location = url; else location.href = url;
    } catch (e) { w?.close(); toast(e.message); }
  }));
  $$("[data-syldel]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Remove this syllabus and its file?")) return;
    try { edu = await api(`/api/edu/syllabi/${b.dataset.syldel}`, { method: "DELETE" }); renderSyllabi(); } catch (e) { toast(e.message); }
  }));
  $$("[data-syllink]").forEach((sel) => (sel.onchange = async () => {
    if (!sel.value) return;
    try { edu = await api(`/api/edu/syllabi/${sel.dataset.syllink}`, { method: "PATCH", body: JSON.stringify({ course_id: sel.value }) }); renderSyllabi(); } catch (e) { toast(e.message); }
  }));
}
$("sylFile").onchange = async () => {
  const f = $("sylFile").files[0]; $("sylFile").value = ""; if (!f || !sylUploadTarget) return;
  const fdta = new FormData(); fdta.append("file", f);
  Object.entries(sylUploadTarget).forEach(([k, v]) => fdta.append(k, v));
  toast("Reading the syllabus…");
  try {
    const r = await api("/api/edu/syllabi/upload", { method: "POST", body: fdta });
    edu = r; toast(r.parsed ? "Syllabus saved and read" : "Syllabus saved"); renderSyllabi();
  } catch (e) { toast(e.message); }
};
$("sylImport").onchange = async () => {
  const f = $("sylImport").files[0]; $("sylImport").value = ""; if (!f) return;
  try {
    const payload = JSON.parse(await f.text());
    const r = await api("/api/edu/syllabi/import", { method: "POST", body: JSON.stringify(payload) });
    edu = r; toast(`Imported ${r.imported} syllabi`); renderSyllabi();
  } catch (e) { toast(e instanceof SyntaxError ? "That file isn't valid JSON" : e.message); }
};

// Academic calendar: GULC key dates + your finals, one semester at a time
let acalTerm = null, acalMonth = null, acalDay = null;
const KIND_LABEL = { assignment: "Due", classes: "Classes", deadline: "Deadline", holiday: "Holiday", break: "Break", exams: "Exam period", final: "Your final", other: "Other" };
const dstr = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function acalEvents() { return (edu.computed.calendar || {})[acalTerm] || []; }
function renderAcadCal() {
  const cal = edu.computed.calendar || {}, terms = Object.keys(cal).filter((t) => cal[t].length);
  const today = dstr(new Date());
  if (!acalTerm || !terms.includes(acalTerm)) acalTerm = terms.find((t) => cal[t].some((e) => (e.end || e.date) >= today)) || terms[terms.length - 1];
  if (!acalTerm) { $("acalList").innerHTML = `<div class="empty">No academic calendar yet.</div>`; return; }
  const evs = acalEvents(), first = evs[0].date, lastD = evs.reduce((m, e) => ((e.end || e.date) > m ? e.end || e.date : m), first);
  const inTerm = (m) => m >= first.slice(0, 7) && m <= lastD.slice(0, 7);
  if (!acalMonth || !inTerm(acalMonth)) acalMonth = today >= first && today <= lastD ? today.slice(0, 7) : first.slice(0, 7);
  $("acalTerms").innerHTML = terms.map((t) => `<button class="week-tab ${t === acalTerm ? "active" : ""}" data-aterm="${esc(t)}">${esc(t)}</button>`).join("");
  $$("[data-aterm]").forEach((b) => (b.onclick = () => { acalTerm = b.dataset.aterm; acalMonth = null; acalDay = null; renderAcadCal(); }));
  $("acalLegend").innerHTML = ["classes", "deadline", "assignment", "holiday", "exams", "final"].map((k) => `<span><i class="k-dot k-${k}"></i>${k === "holiday" ? "Holiday / break" : k === "assignment" ? "Course due date" : KIND_LABEL[k]}</span>`).join("");

  // month grid
  const [y, m] = acalMonth.split("-").map(Number), f1 = new Date(y, m - 1, 1), last = new Date(y, m, 0);
  $("acalTitle").textContent = f1.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  $("acalPrev").disabled = !inTerm(dstr(new Date(y, m - 2, 1)).slice(0, 7));
  $("acalNext").disabled = !inTerm(dstr(new Date(y, m, 1)).slice(0, 7));
  const onDay = (key) => evs.filter((e) => e.date === key || (e.end && e.date <= key && key <= e.end));
  let html = ["M", "T", "W", "T", "F", "S", "S"].map((d) => `<span class="cc-dow">${d}</span>`).join("");
  for (let i = 0; i < (f1.getDay() + 6) % 7; i++) html += "<span></span>";
  for (let d = 1; d <= last.getDate(); d++) {
    const key = dstr(new Date(y, m - 1, d)), here = onDay(key);
    const starts = here.filter((e) => e.date === key), span = here.find((e) => e.end && e.date !== e.end);
    const fin = here.some((e) => e.kind === "final");
    const kinds = [...new Set(starts.map((e) => e.kind))].slice(0, 3);
    html += `<button class="cc-day ${here.length ? "has" : ""} ${span ? `span span-${span.kind}` : ""} ${fin ? "final" : ""} ${key === today ? "today" : ""} ${key === acalDay ? "sel" : ""}" data-aday="${key}" ${here.length ? "" : "disabled"} title="${esc(here.map((e) => e.label).join(" · "))}">${d}<span class="dots">${kinds.map((k) => `<i class="k-dot k-${k}"></i>`).join("")}</span></button>`;
  }
  $("acalGrid").innerHTML = html;
  $$("[data-aday]").forEach((b) => (b.onclick = () => { acalDay = acalDay === b.dataset.aday ? null : b.dataset.aday; renderAcadCal(); }));

  // your finals summary under the grid
  const finals = evs.filter((e) => e.kind === "final");
  const hasFinal = new Set(finals.map((e) => e.course_id));
  const pick = edu.courses.filter((c) => c.term === acalTerm && c.exam_options?.length && !hasFinal.has(c.id));
  const missingExam = edu.courses.filter((c) => c.term === acalTerm && ["enrolled", "planned"].includes(c.status) && !hasFinal.has(c.id) && !c.exam_options?.length && c.graded !== false);
  const sd = (d) => new Date(d + "T00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  $("acalFinals").innerHTML = `<h4>Your finals & papers</h4>` + (finals.length ? finals.map((e) => `<div class="acal-final"><span>${esc(e.label.replace(/^(Final|Paper due): /, ""))}${e.label.startsWith("Paper due") ? ' <span class="tag">paper</span>' : ""}${e.conflict ? ' <span class="neg" title="Same slot as ' + esc(e.conflict.join(", ")) + '">⚠</span>' : ""}</span><span>${new Date(e.date + "T00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}${e.time ? ` · ${esc(e.time)}` : ""}</span></div>`).join("") : `<p class="hint">No exam dates yet.</p>`) +
    pick.map((c) => `<div class="exam-pick"><p class="hint"><strong>${esc(c.title)}</strong> has ${c.exam_options.length} sections on the Registrar's schedule. Which is yours?</p>
      ${c.exam_options.map((o, i) => `<button class="btn btn-secondary btn-sm" data-pick="${c.id}:${i}">${esc(o.professor)} · ${sd(o.date)}, ${esc(o.time)}</button>`).join("")}</div>`).join("") +
    (finals.some((e) => e.source === "registrar") ? `<p class="hint">Exam dates come from the Registrar's Fall 2026 schedule. Edit a course to change one.</p>` : "") +
    (missingExam.length ? `<p class="hint">No exam on the schedule: ${missingExam.map((c) => `<button class="text-btn" data-examfix="${c.id}">${esc(c.title)}</button>`).join(", ")}</p>` : "");
  $$("[data-examfix]").forEach((b) => (b.onclick = () => openCourse(edu.courses.find((c) => c.id === b.dataset.examfix))));
  $$("[data-pick]").forEach((b) => (b.onclick = async () => {
    const [id, i] = b.dataset.pick.split(":"), c = edu.courses.find((x) => x.id === id), o = c.exam_options[+i];
    try { edu = await api(`/api/edu/courses/${id}`, { method: "PATCH", body: JSON.stringify({ exam_date: o.date, exam_time: o.time, professor: o.professor, exam_type: c.exam_type || "In-class exam" }) }); toast(`${c.title}: final set`); renderAcadCal(); }
    catch (e) { toast(e.message); }
  }));

  // agenda
  const list = acalDay ? onDay(acalDay) : evs;
  const nextIdx = acalDay ? -1 : list.findIndex((e) => (e.end || e.date) >= today);
  const nowOn = (e) => e.end && e.date <= today && today <= e.end;     // a range already under way
  $("acalFilter").classList.toggle("hidden", !acalDay);
  if (acalDay) $("acalFilterText").textContent = new Date(acalDay + "T00:00").toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  const groups = {};
  list.forEach((e, i) => ((groups[e.date.slice(0, 7)] ||= []).push([e, i])));
  const fd = (s, o) => new Date(s + "T00:00").toLocaleDateString("en-US", o);
  $("acalList").innerHTML = Object.entries(groups).map(([mo, items]) => `<div class="acal-month"><h3>${fd(mo + "-01", { month: "long", year: "numeric" })}</h3>
    ${items.map(([e, i]) => `<div class="acal-item ${(e.end || e.date) < today ? "past" : ""} ${i === nextIdx ? "next" : ""} ${e.kind === "final" ? "is-final" : ""}">
      <div class="acal-date">${fd(e.date, { month: "short", day: "numeric" })}${e.end ? ` – ${fd(e.end, { month: e.end.slice(5, 7) === e.date.slice(5, 7) ? undefined : "short", day: "numeric" })}` : ""}<small>${fd(e.date, { weekday: "long" })}</small></div>
      <div class="acal-label"><i class="k-dot k-${e.kind}"></i><span>${esc(e.label)}${e.conflict ? ` <span class="warn">Same slot as ${esc(e.conflict.join(", "))}</span>` : ""}</span></div>
      <div class="acal-when">${i === nextIdx ? (nowOn(e) ? "Now" : "Up next") : e.time ? esc(e.time) : KIND_LABEL[e.kind]}</div></div>`).join("")}</div>`).join("") || `<div class="empty">Nothing on this day.</div>`;
}
$("acalPrev").onclick = () => { const [y, m] = acalMonth.split("-").map(Number); acalMonth = dstr(new Date(y, m - 2, 1)).slice(0, 7); renderAcadCal(); };
$("acalNext").onclick = () => { const [y, m] = acalMonth.split("-").map(Number); acalMonth = dstr(new Date(y, m, 1)).slice(0, 7); renderAcadCal(); };
$("acalClear").onclick = () => { acalDay = null; renderAcadCal(); };

// ---------- Finances: paychecks, withholding math, year-end tax ----------
let earn = null, payChart = null;
const openPay = new Set();
const money = (v) => (v == null ? "—" : usd.format(v));
const pct = (r) => `${+(r * 100).toFixed(2)}%`;
async function loadPaychecks() { earn = await api("/api/earnings"); renderPaychecks(); }
function renderPaychecks() {
  const s = earn.settings;
  $("payEmpty").classList.toggle("hidden", earn.ready);
  $("payBody").classList.toggle("hidden", !earn.ready);
  $("payAddStub").classList.toggle("hidden", !earn.ready || !!s.archived);
  $("payEdit").classList.toggle("hidden", !!s.archived);
  $("payJob").innerHTML = esc([s.employer, s.role].filter(Boolean).join(" · ") || "Paychecks") + (s.archived ? ' <span class="pf-badge">Past job</span>' : "");
  if (!earn.ready) { $("payTitle").textContent = "Money earned"; $("paySub").textContent = earn.error || ""; return; }
  const T = earn.totals, Y = earn.year_end, F = Y.federal;
  const fd = (d, o = { month: "short", day: "numeric" }) => new Date(d + "T00:00").toLocaleDateString("en-US", o);
  const freq = { weekly: "weekly", biweekly: "every two weeks", semimonthly: "twice a month", monthly: "monthly" }[s.frequency];
  $("payTitle").textContent = `${money(T.gross)} earned`;
  $("paySub").textContent = `${fd(s.start)} – ${fd(s.end, { month: "short", day: "numeric", year: "numeric" })} · ${money(s.annual_salary)} a year, paid ${freq} · ${T.checks} paycheck${T.checks === 1 ? "" : "s"}${T.deposit_checks ? `, all ${T.deposit_checks === T.checks ? "" : T.deposit_checks + " "}matched to bank deposits` : T.actual_checks ? ` (${T.actual_checks} from pay stubs)` : " (projected)"}`;
  const withheld = T.federal + T.state + T.dc, banked = T.deposited ?? T.net;
  const perDed = T.deposit_checks ? T.other_deductions / T.deposit_checks : 0;
  $("payKpis").innerHTML = [
    ["Gross pay", money(T.gross), `${money(s.annual_salary)} a year, prorated`],
    [T.deposited != null ? "Deposited" : "Take-home", money(banked), T.deposited != null ? `${T.deposit_checks} payroll deposits · ${(T.gross ? (banked / T.gross) * 100 : 0).toFixed(1)}% of gross` : `${(T.gross ? (T.net / T.gross) * 100 : 0).toFixed(1)}% of gross`],
    ["Income tax withheld", money(withheld), `+ ${money(T.ss + T.medicare)} Social Security & Medicare`],
    [Y.net_settle >= 0 ? "Expected back at tax time" : "Expected to owe", money(Math.abs(Y.net_settle)), `Keep ${money(Y.true_take_home)} after filing`],
  ].map(([l, v, sub], i) => `<div class="kpi"><span class="kpi-label">${l}</span><span class="kpi-value ${i === 3 ? (Y.net_settle >= 0 ? "pos" : "neg") : ""}">${v}</span><span class="kpi-sub">${sub}</span></div>`).join("");

  // stacked bars per paycheck
  const k = { net: cssVar("--accent"), fed: cssVar("--exam"), st: cssVar("--warn"), fica: cssVar("--text-3") };
  const series = [["Take-home", "net", k.net], ["Federal", "federal", k.fed], ["State", (c) => c.state + c.dc, k.st], ["Social Security + Medicare", (c) => c.ss + c.medicare, k.fica]];
  payChart?.destroy();
  payChart = new Chart($("chart-pay"), {
    type: "bar",
    data: { labels: earn.checks.map((c) => fd(c.pay_date)), datasets: series.map(([label, f, color]) => ({ label, data: earn.checks.map((c) => (typeof f === "function" ? f(c) : c[f])), backgroundColor: color, borderRadius: 4, maxBarThickness: 44 })) },
    options: { aspectRatio: 2.6, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${usd.format(c.parsed.y)}` } } },
      scales: { x: { stacked: true, grid: { display: false }, border: { display: false }, ticks: { color: cssVar("--text-3"), maxRotation: 0 } },
                y: { stacked: true, border: { display: false }, grid: { color: cssVar("--line") }, ticks: { color: cssVar("--text-3"), maxTicksLimit: 5, callback: (v) => usd0.format(v) } } } },
  });
  $("payLegend").innerHTML = series.map(([l, , c]) => `<span><i class="k-dot" style="background:${c}"></i>${l}</span>`).join("");

  // table with expandable math
  const hasDc = T.dc > 0, hasDep = T.deposit_checks > 0, ncol = 8 + hasDc + hasDep;
  $("payTable").innerHTML = `<thead><tr><th>Pay date</th><th>Period</th>${["Gross", "Federal", s.work_state === "IL" ? "Illinois" : "State", ...(hasDc ? ["DC"] : []), "Soc. Sec.", "Medicare", "Net", ...(hasDep ? ["In Chase"] : [])].map((h) => `<th class="num">${h}</th>`).join("")}</tr></thead><tbody>` +
    earn.checks.map((c, i) => `<tr class="pay-row ${openPay.has(i) ? "open" : ""}" data-payrow="${i}"><td><strong>${fd(c.pay_date)}</strong>${c.source === "actual" ? ' <span class="pf-badge">stub</span>' : ""}</td>
      <td class="muted">${c.period_start ? `${fd(c.period_start)} – ${fd(c.period_end)}${c.days_worked != null && c.days_worked < c.days_in_period ? ` · ${c.days_worked}/${c.days_in_period} days` : ""}` : ""}</td>
      ${[c.gross, c.federal, c.state, ...(hasDc ? [c.dc] : []), c.ss, c.medicare].map((v) => `<td class="num">${money(v)}</td>`).join("")}<td class="num"><strong>${money(c.net)}</strong></td>${hasDep ? `<td class="num">${c.deposit != null ? `<strong class="pos">${money(c.deposit)}</strong>` : "—"}</td>` : ""}</tr>
      ${openPay.has(i) ? `<tr class="pay-math"><td colspan="${ncol}">${payMath(c)}</td></tr>` : ""}`).join("") +
    `</tbody><tfoot><tr><td><strong>Total</strong></td><td></td>${[T.gross, T.federal, T.state, ...(hasDc ? [T.dc] : []), T.ss, T.medicare].map((v) => `<td class="num"><strong>${money(v)}</strong></td>`).join("")}<td class="num"><strong>${money(T.net)}</strong></td>${hasDep ? `<td class="num"><strong class="pos">${money(T.deposited)}</strong></td>` : ""}</tr></tfoot>` +
    (hasDep ? `<caption class="pay-caption">Projected net matches each Chase deposit to within ${money(Math.max(...earn.checks.filter((c) => c.deposit != null).map((c) => Math.abs(c.other_deductions))))}: about ${money(perDed)} a check of other payroll deductions the stub would itemize.${T.reimbursements ? ` Separately, ${money(T.reimbursements)} in expense reimbursements came from the same employer and isn't counted as pay.` : ""}</caption>` : "");
  $$("[data-payrow]").forEach((r) => (r.onclick = () => { const i = +r.dataset.payrow; openPay.has(i) ? openPay.delete(i) : openPay.add(i); renderPaychecks(); }));

  // federal brackets: what payroll assumed vs. your actual year
  const assumedTaxable = Math.max(0, F.payroll_assumed_income - F.std_deduction);
  $("payFedMeta").textContent = `${pct(F.marginal)} marginal · ${F.effective}% effective`;
  $("payBrackets").innerHTML = bracketBar(`Your ${earn.year}`, F.taxable, earn.tables.federal, Math.max(assumedTaxable, F.taxable)) +
    bracketBar(`What payroll assumed (${money(F.payroll_assumed_income)} for a full year)`, assumedTaxable, earn.tables.federal, Math.max(assumedTaxable, F.taxable), true) +
    `<p class="solver-text">Payroll withholds as if each paycheck repeats all year, so it taxed your summer at the <strong>${pct(F.payroll_assumed_marginal)}</strong> rate. Over the whole year you only reach the <strong>${pct(F.marginal)}</strong> bracket${Y.other_income ? "" : " if this is your only income"}, which is why the federal refund is large.</p>` +
    mathLines([
      ["Wages", money(Y.wages)], ...(Y.other_income ? [["+ Other income", money(Y.other_income)]] : []),
      [`− Standard deduction`, money(F.std_deduction)], ["= Taxable income", money(F.taxable), "sum"],
      ...F.fill.filter((b) => b.amount > 0).map((b) => [`${pct(b.rate)} × ${money(b.amount)}`, money(b.tax), "sub"]),
      ["= Federal tax for the year", money(F.tax), "sum"], ["Withheld from paychecks", money(F.withheld)],
      [F.refund >= 0 ? "Federal refund" : "Federal tax owed", money(Math.abs(F.refund)), F.refund >= 0 ? "pos" : "neg"],
    ]);

  // state cards
  const cards = [];
  if (Y.il) cards.push(`<div class="card"><div class="card-head"><h3>Illinois${Y.il.resident ? "" : " (nonresident)"}</h3><span class="card-meta">Flat ${pct(earn.tables.il_rate)}</span></div>` + mathLines([
    ["Illinois wages", money(Y.il.wages)], [`− Exemption ${money(Y.il.exemption)} × ${Y.il.ratio}% earned in IL`, money(Y.il.exemption * Y.il.ratio / 100)],
    [`× ${pct(earn.tables.il_rate)}`, money(Y.il.tax), "sum"], ["Withheld", money(Y.il.withheld)],
    [Y.il.refund >= 0 ? "Illinois refund" : "Illinois owed", money(Math.abs(Y.il.refund)), Y.il.refund >= 0 ? "pos" : "neg"]]) +
    `<p class="hint">Illinois taxes wages earned there even if you live elsewhere. Each check subtracts ${money(earn.checks.find((c) => c.math?.il)?.math.il.allowance_per_period ?? 0)} for the exemption before the 4.95%; the full-year exemption is prorated to your Illinois share.</p></div>`);
  if (Y.dc) cards.push(`<div class="card"><div class="card-head"><h3>District of Columbia (resident)</h3><span class="card-meta">${earn.tables.dc.length} brackets</span></div>` +
    bracketBar("DC taxable income", Y.dc.taxable, earn.tables.dc, Math.max(Y.dc.taxable * 1.25, 70000)) + mathLines([
    ["Income", money(Y.agi)], [`− Standard deduction${Y.dc.std_assumed ? " (assumed same as federal)" : ""}`, money(Y.dc.std_deduction)], ["= DC taxable", money(Y.dc.taxable), "sum"],
    ...Y.dc.fill.filter((b) => b.amount > 0).map((b) => [`${pct(b.rate)} × ${money(b.amount)}`, money(b.tax), "sub"]),
    ["= DC tax", money(Y.dc.tax), "sum"], ["− Credit for Illinois tax", money(Y.dc.credit_other_state)],
    ...(Y.dc.withheld ? [["− DC withheld", money(Y.dc.withheld)]] : []),
    [Y.dc.due > 0 ? "Owed to DC" : "Owed to DC", money(Math.max(0, Y.dc.due)), Y.dc.due > 0 ? "neg" : "pos"]]) +
    `<p class="hint">DC taxes residents on all income but credits tax paid to another state on the same wages, up to DC's own tax on them. Nothing was withheld for DC unless you told payroll to.</p></div>`);
  $("payStates").innerHTML = cards.join("");
  $("payStates").classList.toggle("hidden", !cards.length);

  $("payBottom").innerHTML = mathLines([
    ["Gross pay", money(Y.wages)], ["− Federal income tax (actual for the year)", money(F.tax)],
    ["− Social Security & Medicare", money(Y.fica.ss + Y.fica.medicare)],
    ...(Y.il ? [["− Illinois tax", money(Y.il.tax)]] : []), ...(Y.dc ? [["− DC tax after credit", money(Y.dc.after_credit)]] : []),
    ["= What you keep", money(Y.true_take_home), "sum"],
    [T.deposited != null ? "Already deposited" : "Already in your bank from paychecks", money(banked)],
    ...(T.other_deductions ? [["Other payroll deductions", money(T.other_deductions)]] : []),
    [Y.net_settle >= 0 ? "Comes back when you file" : "You'll pay when you file", money(Math.abs(Y.net_settle)), Y.net_settle >= 0 ? "pos" : "neg"],
  ]) + `<p class="hint">${Y.fica.note}</p>`;
}
function payMath(c) {
  if (!c.math) return `<p class="hint">Entered from a pay stub.</p>`;
  const f = c.math.federal, il = c.math.il, pp = f.pay_periods;
  const fed = [
    [`Gross this check${c.days_worked < c.days_in_period ? ` (${money(earn.settings.annual_salary)} ÷ 260 × ${c.days_worked} workdays)` : ` (${money(earn.settings.annual_salary)} ÷ ${pp})`}`, money(c.gross)],
    [`× ${pp} pay periods = annualized`, money(f.annualized_wages)], [`− W-4 adjustment (Pub 15-T line 1g)`, money(f.line_1g)],
    ["= Adjusted annual wage", money(f.adjusted_annual), "sum"],
    [`Table row: ${money(f.row_base)} + ${pct(f.row_rate)} × (${money(f.adjusted_annual)} − ${money(f.row_from)})`, money(f.annual_withholding)],
    [`÷ ${pp} = federal withholding${c.source === "actual" ? " (projected; your stub shows " + money(c.federal) + ")" : ""}`, money(f.annual_withholding / pp), "sum"],
  ];
  const st = il ? [[`Illinois: (${money(c.gross)} − ${money(il.allowance_per_period)} allowance) × 4.95%`, money(c.state), "sum"]] : [];
  const fica = [[`Social Security: ${money(c.gross)} × 6.2%`, money(c.ss)], [`Medicare: ${money(c.gross)} × 1.45%${c.math.addl_medicare_wages ? " + 0.9% over $200k" : ""}`, money(c.medicare)]];
  const dep = c.deposit != null ? [["Projected net", money(c.net), "sum"], [`Bank deposit ${new Date(c.deposit_date + "T00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })}`, money(c.deposit)], ["Other deductions on the stub", money(c.other_deductions), "sub"]] : [];
  return `<div class="pay-math-grid"><div><h4>Federal</h4>${mathLines(fed)}</div><div><h4>State & payroll taxes</h4>${mathLines([...st, ...fica, ...dep])}</div></div>`;
}
function mathLines(rows) {
  return `<div class="math">${rows.map(([l, v, cls]) => `<div class="math-row ${cls || ""}"><span>${l}</span><span>${v}</span></div>`).join("")}</div>`;
}
function bracketBar(label, income, brackets, scaleMax, ghost = false) {
  const max = scaleMax * 1.08 || 1;
  const segs = brackets.map(([lo, rate], i) => {
    const hi = i + 1 < brackets.length ? brackets[i + 1][0] : Infinity;
    if (lo >= max) return "";
    const w = ((Math.min(hi, max) - lo) / max) * 100, filled = Math.max(0, Math.min(1, (income - lo) / (Math.min(hi, max) - lo)));
    return `<div class="bseg" style="width:${w}%"><span class="bfill" style="width:${filled * 100}%;opacity:${0.35 + rate * 1.6}"></span><span class="brate">${pct(rate)}</span></div>`;
  }).join("");
  return `<div class="bracket ${ghost ? "ghost" : ""}"><div class="bracket-label"><span>${label}</span><strong>${money(income)} taxable</strong></div><div class="bbar">${segs}</div></div>`;
}
$("payEdit").onclick = $("paySetup").onclick = () => {
  const f = $("payForm"), s = earn?.settings || {};
  [...f.elements].forEach((el) => { if (!el.name) return; if (el.type === "checkbox") el.checked = !!s[el.name]; else el.value = s[el.name] ?? ""; });
  if (!s.frequency) f.elements.frequency.value = "weekly";
  $("payDialog").showModal();
};
$("payCancel").onclick = () => $("payDialog").close();
$("payForm").onsubmit = async (e) => {
  e.preventDefault();
  const body = {};
  [...$("payForm").elements].forEach((el) => { if (el.name) body[el.name] = el.type === "checkbox" ? el.checked : el.value; });
  try { earn = await api("/api/earnings/settings", { method: "PUT", body: JSON.stringify(body) }); $("payDialog").close(); renderPaychecks(); toast("Saved"); }
  catch (err) { toast(err.message); }
};
$("payAddStub").onclick = () => { $("stubForm").reset(); $("stubDialog").showModal(); };
$("stubCancel").onclick = () => $("stubDialog").close();
$("stubForm").onsubmit = async (e) => {
  e.preventDefault();
  const body = Object.fromEntries([...$("stubForm").elements].filter((el) => el.name).map((el) => [el.name, el.value]));
  try { earn = await api("/api/earnings/paychecks", { method: "POST", body: JSON.stringify(body) }); $("stubDialog").close(); renderPaychecks(); toast("Pay stub added"); }
  catch (err) { toast(err.message); }
};

// ---------- Training: planner, live session, history ----------
const DAYS = [["mon", "Mon"], ["tue", "Tue"], ["wed", "Wed"], ["thu", "Thu"], ["fri", "Fri"], ["sat", "Sat"], ["sun", "Sun"]];
const COMMON_WORKOUTS = [["Push", "strength"], ["Pull", "strength"], ["Legs", "strength"], ["Upper", "strength"], ["Lower", "strength"],
  ["Full Body", "strength"], ["Core/Yoga", "strength"], ["Run", "cardio"], ["Bike", "cardio"], ["Swim", "cardio"], ["Basketball", "cardio"], ["Cardio", "cardio"]];
const UNIT_LABEL = { lb: "lb", plates: "plates/side", pin: "pin", plates_side: "lb plates/side" };
const LIB = {
  Chest: { Barbell: ["Flat Bench Press", "Incline Bench Press", "Decline Bench Press", "Close-Grip Bench Press"], Dumbbell: ["DB Flat Press", "Incline DB Press", "DB Flat Flyes", "Incline DB Flyes"], Cable: ["Low to High Cable Flyes", "High to Low Cable Flyes", "Cable Crossover"], Machine: ["Chest Press Machine", "Pec Deck"], Bodyweight: ["Push Ups", "Dips"] },
  Back: { Barbell: ["Deadlift", "Barbell Row", "Smith Machine Rows"], Dumbbell: ["Single-Arm DB Row", "DB Chest-Supported Row"], Cable: ["Mid Grip Lat Pulldowns", "Wide Grip Lat Pulldowns", "Close Grip Lat Pulldowns", "Single-Arm Lat Pulldowns", "Mid-Grip Seated Cable Row", "Close-Grip Seated Cable Row", "Straight Arm Pulldowns", "Face Pull"], Machine: ["Low to High Row Machine", "Assisted Pull Ups"], Bodyweight: ["Pull Ups", "Chin Ups"] },
  Shoulders: { Barbell: ["Overhead Press"], Dumbbell: ["DB Shoulder Press", "DB Lateral Raises", "Seated DB Lateral Raises", "Rear Delt Flyes", "Front Raises"], Cable: ["Cable Lateral Raises"], Machine: ["Shoulder Press Machine"] },
  Arms: { Barbell: ["Barbell Curls", "Skullcrushers"], Dumbbell: ["DB Curls", "Seated DB Curls", "Incline DB Curls", "Alternating DB Hammer Curls", "Seated Hammer Curls", "DB Skullcrushers", "Overhead Tricep Extensions"], Cable: ["Cable Curls", "Tricep Pushdowns", "Single-Arm Tricep Pushdowns"], Machine: ["Preacher Curls", "Single-Arm Preacher Curls", "Assisted Dips"], Bodyweight: ["Bench Dips"] },
  Legs: { Barbell: ["Back Squat", "Front Squat", "Romanian Deadlift", "Hip Thrust"], Machine: ["Smith Machine Squats", "Smith Machine Romanian Deadlift", "Leg Press", "Leg Extensions", "Single-Leg Leg Extensions", "Seated Leg Curls", "Lying Leg Curls", "Calf Raises", "Hip Abductor"], Dumbbell: ["DB Romanian Deadlift", "DB Goblet Squats", "DB Split Squats", "Bulgarian Split Squats", "Step Ups"], Bodyweight: ["Bodyweight Lunges", "Walking Lunges"] },
  Core: { Bodyweight: ["Plank", "Hanging Leg Raises", "Crunches", "Russian Twists", "Dead Bug"], Cable: ["Cable Crunch", "Torso Rotation"], Other: ["Ab Wheel", "Yoga Flow"] },
  Cardio: { Other: ["Run", "Bike", "Swim", "Row Erg", "Stair Climber", "Basketball", "Walk"] },
};
const MUSCLES = Object.keys(LIB);
const EQUIP = ["Barbell", "Dumbbell", "Cable", "Machine", "Bodyweight", "Other"];
const uid = () => Math.random().toString(36).slice(2, 12);
let tr = null;                 // GET /api/training
let planWeek = 1;
let openWorkouts = new Set();  // expanded workout cards
let saveTimer = null;
let sortables = [];
let session = null;            // live session draft
let sessTick = null, restTick = null;
let pickerCtx = null;          // where picked exercises go
let ccMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
let historyData = null, historyDay = null;

const fmtW = (w, unit) => (w == null ? "" : `${+w % 1 ? (+w).toFixed(1) : +w}${unit === "lb" || !unit ? " lb" : unit === "pin" ? " pin" : unit === "plates" ? " pl" : " lb/side"}`);
const fmtDur = (sec) => { if (sec == null) return null; const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60); return h ? `${h}h ${m}m` : `${m}m`; };
const roundTo = (x, step) => Math.round(x / step) * step;
function allExerciseNames() {
  const names = new Set();
  Object.values(LIB).forEach((g) => Object.values(g).forEach((l) => l.forEach((n) => names.add(n))));
  (tr?.state?.custom_exercises || []).forEach((c) => names.add(c.name));
  (tr?.template?.weeks || []).forEach((wk) => [...Object.values(wk.days).flat(), ...wk.tray].forEach((w) => w.exercises.forEach((e) => names.add(e.name))));
  return names;
}
function muscleOf(name) {
  for (const [m, g] of Object.entries(LIB)) for (const [eq, l] of Object.entries(g)) if (l.includes(name)) return [m, eq];
  const c = (tr?.state?.custom_exercises || []).find((x) => x.name === name);
  return c ? [c.muscle, c.equipment] : ["Other", "Other"];
}
function targetWeight(e) {
  const max = e.max_lift ? tr?.state?.maxes?.[e.max_lift] : null;
  return e.pct_of_max && max ? roundTo((e.pct_of_max / 100) * max, 5) : null;
}

async function loadWeekPlan() {
  tr = await api("/api/training");
  if (!tr.template?.weeks?.length) tr.template = { weeks: [{ week: 1, days: Object.fromEntries(DAYS.map(([k]) => [k, []])), tray: [] }] };
  planWeek = Math.min(planWeek || tr.current_week, tr.template.weeks.length);
  if (!renderWeekPlan.didInit) { planWeek = tr.current_week; renderWeekPlan.didInit = true; }
  $("maxBench").value = tr.state?.maxes?.bench ?? "";
  $("maxSquat").value = tr.state?.maxes?.squat ?? "";
  $("resumeBanner").classList.toggle("hidden", !loadDraft());
  renderWeekPlan();
}

function renderWeekPlan() {
  const T = tr.template, wk = T.weeks[planWeek - 1];
  $("planProgram").textContent = `${tr.state?.program_name || "Your plan"} · current week ${tr.current_week} of ${T.weeks.length}`;
  $("planWeeks").innerHTML = T.weeks.map((w) => `<button class="week-tab ${w.week === planWeek ? "active" : ""}" data-week="${w.week}">Week ${w.week}${w.week === tr.current_week ? '<i class="now-dot"></i>' : ""}</button>`).join("");
  const todayW = wk.days[tr.weekday] || [];
  $("startToday").disabled = !(planWeek === tr.current_week && todayW.length);
  $("startToday").textContent = todayW.length && planWeek === tr.current_week ? `Start ${todayW[0].name}` : "Nothing planned today";
  const card = (w, where) => {
    const open = openWorkouts.has(w.id);
    return `<div class="w-card ${w.kind === "cardio" ? "is-cardio" : ""}" data-wid="${w.id}">
      <div class="w-card-head">
        <span class="drag" title="Drag to move">⋮⋮</span>
        <button class="w-name" data-toggle="${w.id}">${esc(w.name)}</button>
        <span class="w-count">${w.kind === "cardio" ? "Cardio" : `${w.exercises.length} ex`}</span>
      </div>
      ${open ? `<div class="w-ex-list" data-exlist="${w.id}">
        ${w.exercises.map((e) => `<div class="w-ex" data-eid="${e.id}"><span class="drag">⋮⋮</span>
          <span class="w-ex-name">${esc(e.name)}<small>${[e.sets && `${e.sets}×${e.reps || "—"}`, e.pct_of_max && `${e.pct_of_max}% max${targetWeight(e) ? ` → ${targetWeight(e)} lb` : ""}`, e.unit !== "lb" && UNIT_LABEL[e.unit]].filter(Boolean).join(" · ")}</small></span>
          <button class="icon-btn sm" data-delex="${w.id}:${e.id}" aria-label="Remove exercise">×</button></div>`).join("")}
        <div class="w-actions">
          <button class="text-btn accent" data-addex="${w.id}">+ Exercises</button>
          <button class="text-btn" data-rename="${w.id}">Rename</button>
          <button class="text-btn danger" data-delw="${w.id}">Delete</button>
        </div>
      </div>` : ""}
      ${where !== "tray" ? `<button class="btn btn-secondary btn-sm w-start" data-start="${w.id}">Start</button>` : ""}
    </div>`;
  };
  const isToday = (k) => planWeek === tr.current_week && k === tr.weekday;
  $("planBoard").innerHTML = DAYS.map(([k, label]) => `
    <div class="day-col ${isToday(k) ? "today" : ""}">
      <div class="day-head"><span>${label}</span>${isToday(k) ? '<span class="pill pill-on_track">Today</span>' : ""}</div>
      <div class="day-drop" data-day="${k}">${(wk.days[k] || []).map((w) => card(w, k)).join("")}</div>
      <button class="add-workout" data-addw="${k}">+ Add workout</button>
    </div>`).join("");
  $("planTray").innerHTML = `<div class="day-head"><span>Unscheduled${wk.tray.some((w) => w.program_day) ? " · program days to place" : ""}</span><span class="tag">${wk.tray.length}</span></div>
    <div class="day-drop tray-drop" data-day="tray">${wk.tray.map((w) => card(w, "tray")).join("") || '<p class="hint">Drag a workout here to park it.</p>'}</div>`;
  bindPlanner();
}

function findWorkout(id) {
  const wk = tr.template.weeks[planWeek - 1];
  for (const [k, list] of [...Object.entries(wk.days), ["tray", wk.tray]]) {
    const i = list.findIndex((w) => w.id === id);
    if (i >= 0) return { list, i, w: list[i], day: k };
  }
  return null;
}

function bindPlanner() {
  sortables.forEach((s) => s.destroy()); sortables = [];
  const wk = tr.template.weeks[planWeek - 1];
  const listFor = (key) => (key === "tray" ? wk.tray : wk.days[key]);
  $$(".day-drop").forEach((el) => sortables.push(Sortable.create(el, {
    group: "workouts", handle: ".w-card-head", draggable: ".w-card", animation: 160, ghostClass: "drag-ghost", filter: ".hint, button.w-name", preventOnFilter: false,
    onEnd: (ev) => {
      const from = listFor(ev.from.dataset.day), to = listFor(ev.to.dataset.day);
      const [moved] = from.splice(ev.oldIndex, 1); to.splice(ev.newIndex, 0, moved);
      renderWeekPlan(); queueSave();
    },
  })));
  $$("[data-exlist]").forEach((el) => sortables.push(Sortable.create(el, {
    handle: ".drag", draggable: ".w-ex", animation: 140,
    onEnd: (ev) => { const f = findWorkout(el.dataset.exlist); const [m] = f.w.exercises.splice(ev.oldIndex, 1); f.w.exercises.splice(ev.newIndex, 0, m); queueSave(); },
  })));
  $$("[data-week]").forEach((b) => (b.onclick = () => { planWeek = +b.dataset.week; renderWeekPlan(); }));
  $$("[data-toggle]").forEach((b) => (b.onclick = () => { const id = b.dataset.toggle; openWorkouts.has(id) ? openWorkouts.delete(id) : openWorkouts.add(id); renderWeekPlan(); }));
  $$("[data-delex]").forEach((b) => (b.onclick = () => { const [wid, eid] = b.dataset.delex.split(":"); const f = findWorkout(wid); f.w.exercises = f.w.exercises.filter((e) => e.id !== eid); renderWeekPlan(); queueSave(); }));
  $$("[data-delw]").forEach((b) => (b.onclick = () => { const f = findWorkout(b.dataset.delw); if (confirm(`Delete ${f.w.name}?`)) { f.list.splice(f.i, 1); renderWeekPlan(); queueSave(); } }));
  $$("[data-rename]").forEach((b) => (b.onclick = () => { const f = findWorkout(b.dataset.rename); const n = prompt("Workout name", f.w.name); if (n?.trim()) { f.w.name = n.trim().slice(0, 60); renderWeekPlan(); queueSave(); } }));
  $$("[data-addex]").forEach((b) => (b.onclick = () => openExercisePicker({ type: "template", wid: b.dataset.addex })));
  $$("[data-addw]").forEach((b) => (b.onclick = () => openWorkoutPicker(b.dataset.addw)));
  $$("[data-start]").forEach((b) => (b.onclick = () => startSession(findWorkout(b.dataset.start).w)));
}

function queueSave() {
  $("planSaved").textContent = "Saving…";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try { await api("/api/training/template", { method: "PUT", body: JSON.stringify(tr.template) }); $("planSaved").textContent = "Saved"; }
    catch (e) { $("planSaved").textContent = ""; toast(e.message); }
  }, 600);
}

$("copyLayout").onclick = () => {
  if (!confirm("Place the same program days on the same weekdays in every week?")) return;
  const src = tr.template.weeks[planWeek - 1];
  const where = {};   // program_day -> [weekday, order]
  DAYS.forEach(([k]) => (src.days[k] || []).forEach((w, i) => w.program_day && (where[w.program_day] = [k, i])));
  tr.template.weeks.forEach((wk) => {
    if (wk === src) return;
    const pool = [...wk.tray, ...Object.values(wk.days).flat()];
    wk.days = Object.fromEntries(DAYS.map(([k]) => [k, []])); wk.tray = [];
    pool.forEach((w) => { const t = where[w.program_day]; t ? wk.days[t[0]].push(w) : wk.tray.push(w); });
    // carry over custom (non-program) workouts from the source week, e.g. Basketball
    DAYS.forEach(([k]) => (src.days[k] || []).filter((w) => !w.program_day).forEach((w) => wk.days[k].push({ ...structuredClone(w), id: uid() })));
  });
  toast("Layout copied to every week"); queueSave();
};
const saveMaxes = async () => {
  try {
    const r = await api("/api/training/state", { method: "PUT", body: JSON.stringify({ maxes: { bench: $("maxBench").value, squat: $("maxSquat").value } }) });
    tr.state = r.state; renderWeekPlan();
  } catch (e) { toast(e.message); }
};
$("maxBench").onchange = saveMaxes; $("maxSquat").onchange = saveMaxes;
$("startToday").onclick = () => { const w = tr.template.weeks[tr.current_week - 1].days[tr.weekday]?.[0]; if (w) startSession(w); };

// Workout picker
function openWorkoutPicker(day) {
  pickerCtx = { day };
  $("wpDay").textContent = DAYS.find(([k]) => k === day)[1];
  $("wpCommon").innerHTML = COMMON_WORKOUTS.map(([n, kind]) => `<button type="button" class="pick" data-pick="${n}" data-kind="${kind}">${n}</button>`).join("");
  const wk = tr.template.weeks[planWeek - 1];
  const progs = [...wk.tray];
  $("wpProgramWrap").classList.toggle("hidden", !progs.length);
  $("wpProgram").innerHTML = progs.map((w) => `<button type="button" class="pick" data-fromtray="${w.id}">${esc(w.name)}${w.program_day ? ` · Day ${w.program_day}` : ""}</button>`).join("");
  $("wpCustom").value = ""; $("wpCardio").checked = false;
  $$("#wpCommon [data-pick]").forEach((b) => (b.onclick = () => addWorkout(day, b.dataset.pick, b.dataset.kind)));
  $$("#wpProgram [data-fromtray]").forEach((b) => (b.onclick = () => {
    const i = wk.tray.findIndex((w) => w.id === b.dataset.fromtray); const [w] = wk.tray.splice(i, 1);
    wk.days[day].push(w); $("workoutPicker").close(); renderWeekPlan(); queueSave();
  }));
  $("workoutPicker").showModal();
}
function addWorkout(day, name, kind) {
  const w = { id: uid(), name, kind, exercises: [] };
  tr.template.weeks[planWeek - 1].days[day].push(w);
  if (kind === "strength") openWorkouts.add(w.id);
  $("workoutPicker").close(); renderWeekPlan(); queueSave();
  if (kind === "strength") openExercisePicker({ type: "template", wid: w.id });
}
$("wpCancel").onclick = () => $("workoutPicker").close();
$("workoutPickerForm").onsubmit = (e) => { e.preventDefault(); const n = $("wpCustom").value.trim(); if (!n) return toast("Type a name or pick one above"); addWorkout(pickerCtx.day, n, $("wpCardio").checked ? "cardio" : "strength"); };

// Exercise library picker
let epSelected = new Set(), epMuscle = "All";
function openExercisePicker(ctx) {
  pickerCtx = ctx; epSelected = new Set(); epMuscle = "All"; $("epSearch").value = "";
  $("epNewMuscle").innerHTML = MUSCLES.map((m) => `<option>${m}</option>`).join("");
  $("epNewEquip").innerHTML = EQUIP.map((m) => `<option>${m}</option>`).join("");
  renderExercisePicker(); $("exercisePicker").showModal(); setTimeout(() => $("epSearch").focus(), 50);
}
function renderExercisePicker() {
  const q = $("epSearch").value.toLowerCase();
  $("epFilters").innerHTML = ["All", ...MUSCLES, "Other"].map((m) => `<button type="button" class="chip ${m === epMuscle ? "chip-on" : ""}" data-m="${m}">${m}</button>`).join("");
  const groups = {};
  [...allExerciseNames()].sort().forEach((n) => {
    const [m, eq] = muscleOf(n);
    if (epMuscle !== "All" && m !== epMuscle) return;
    if (q && !n.toLowerCase().includes(q)) return;
    (groups[m] ||= []).push([n, eq]);
  });
  $("epList").innerHTML = Object.entries(groups).map(([m, list]) => `<p class="pick-label">${m}</p>` + list.map(([n, eq]) =>
    `<label class="ep-row"><input type="checkbox" value="${esc(n)}" ${epSelected.has(n) ? "checked" : ""} /><span>${esc(n)}</span><small>${eq}</small></label>`).join("")).join("") || `<p class="hint">No matches. Create it below.</p>`;
  $$("#epFilters [data-m]").forEach((b) => (b.onclick = () => { epMuscle = b.dataset.m; renderExercisePicker(); }));
  $$("#epList input").forEach((c) => (c.onchange = () => { c.checked ? epSelected.add(c.value) : epSelected.delete(c.value); $("epCount").textContent = epSelected.size ? `${epSelected.size} selected` : ""; }));
  $("epCount").textContent = epSelected.size ? `${epSelected.size} selected` : "";
}
$("epSearch").oninput = renderExercisePicker;
$("epCancel").onclick = () => $("exercisePicker").close();
$("epCreate").onclick = async () => {
  const name = $("epNewName").value.trim(); if (!name) return toast("Name the exercise");
  const custom = [...(tr.state?.custom_exercises || []), { name, muscle: $("epNewMuscle").value, equipment: $("epNewEquip").value }];
  try { const r = await api("/api/training/state", { method: "PUT", body: JSON.stringify({ custom_exercises: custom }) }); tr.state = r.state; epSelected.add(name); $("epNewName").value = ""; renderExercisePicker(); }
  catch (e) { toast(e.message); }
};
$("epAdd").onclick = () => {
  const names = [...epSelected]; if (!names.length) return $("exercisePicker").close();
  const unitFor = (n) => (/Leg Press/.test(n) ? "plates" : /Cable Flyes|Single-Arm Tricep Pushdowns/.test(n) ? "pin" : "lb");
  if (pickerCtx.type === "template") {
    const f = findWorkout(pickerCtx.wid);
    names.forEach((n) => f.w.exercises.push({ id: uid(), name: n, unit: unitFor(n), rest_sec: tr.state?.default_rest_sec || 120, sets: 3, reps: null }));
    openWorkouts.add(f.w.id); renderWeekPlan(); queueSave();
  } else if (pickerCtx.type === "session") {
    names.forEach((n) => session.exercises.push(newSessionExercise({ name: n, unit: unitFor(n), sets: 3 })));
    renderSession(); saveDraft();
  } else if (pickerCtx.type === "replace") {
    const ex = session.exercises[pickerCtx.index]; Object.assign(ex, newSessionExercise({ name: names[0], unit: unitFor(names[0]), sets: ex.sets.length }));
    renderSession(); saveDraft();
  }
  $("exercisePicker").close();
};

// Live session
function newSessionExercise(e) {
  const prev = tr?.last_sets?.[e.name.toLowerCase()];
  const target = targetWeight(e);
  const n = Math.max(e.sets || 3, 1);
  return { name: e.name, unit: e.unit || prev?.unit || "lb", rest_sec: e.rest_sec || tr?.state?.default_rest_sec || 120,
           reps_target: e.reps || null, pct: e.pct_of_max || null, target, notes: e.notes || null, note: "",
           sets: Array.from({ length: n }, () => ({ weight: target ?? "", reps: "", done: false })) };
}
function startSession(w) {
  if (loadDraft() && !confirm("A workout is already in progress. Discard it and start this one?")) return;
  const now = new Date();
  session = { name: w.name, type: w.name, kind: w.kind, started_at: now.toISOString(), date: iso(new Date(now - now.getTimezoneOffset() * 6e4)),
              exercises: w.exercises.map(newSessionExercise), notes: "", rest: null };
  saveDraft(); openSession();
}
function saveDraft() { try { localStorage.setItem("lt-session", JSON.stringify(session)); } catch {} }
function loadDraft() { try { return JSON.parse(localStorage.getItem("lt-session") || "null"); } catch { return null; } }
function clearDraft() { try { localStorage.removeItem("lt-session"); } catch {} }
$("resumeSession").onclick = () => { session = loadDraft(); if (session) openSession(); };

function openSession() {
  $("session").classList.remove("hidden"); document.body.classList.add("no-scroll");
  $("sessName").value = session.name;
  const lower = /leg|lower|squat/i.test(session.name);
  const wu = tr?.state?.warmups; const list = wu ? (lower ? wu.lower : wu.upper) : null;
  $("sessWarmup").classList.toggle("hidden", !list?.length || session.kind === "cardio");
  if (list) $("sessWarmupList").innerHTML = list.map((x, i) => `<label class="check"><input type="checkbox" data-wu="${i}" /> ${esc(x)}</label>`).join("");
  $("sessNotes").value = session.notes || "";
  clearInterval(sessTick); sessTick = setInterval(tickClock, 1000); tickClock();
  renderSession();
}
function tickClock() {
  if (!session) return;
  const s = Math.floor((Date.now() - new Date(session.started_at)) / 1000);
  $("sessClock").textContent = `${Math.floor(s / 3600) ? Math.floor(s / 3600) + ":" : ""}${String(Math.floor((s % 3600) / 60)).padStart(Math.floor(s / 3600) ? 2 : 1, "0")}:${String(s % 60).padStart(2, "0")}`;
  $("sessMeta").textContent = new Date(session.started_at).toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" });
  tickRest();
}
function tickRest() {
  const r = session?.rest; const el = r && document.querySelector(`[data-rest="${r.ex}:${r.set}"]`);
  if (!r) return;
  const left = Math.max(0, Math.round((r.until - Date.now()) / 1000));
  if (el) { el.querySelector("span").textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`; el.querySelector("i").style.width = `${(left / r.total) * 100}%`; }
  if (left === 0) { session.rest = null; saveDraft(); try { navigator.vibrate?.([200, 100, 200]); } catch {} toast("Rest over. Next set"); renderSession(); }
}
function prevText(ex, i) {
  const p = tr?.last_sets?.[ex.name.toLowerCase()]?.sets?.[i];
  if (!p || (p.weight == null && p.reps == null)) return "—";
  const u = p.unit || tr.last_sets[ex.name.toLowerCase()].unit;
  return p.weight == null ? `${p.reps} reps` : `${fmtW(p.weight, u)} × ${p.reps ?? "?"}`;
}
function renderSession() {
  $("sessExercises").innerHTML = session.exercises.map((ex, ei) => `
    <div class="s-ex">
      <div class="s-ex-head">
        <button class="s-ex-name" data-progress="${esc(ex.name)}">${esc(ex.name)}</button>
        <button class="icon-btn" data-exmenu="${ei}" aria-label="Exercise options">⋯</button>
      </div>
      <div class="s-ex-sub">${[ex.reps_target && `${ex.sets.length} × ${ex.reps_target}`, ex.pct && `${ex.pct}% of max${ex.target ? ` → ${ex.target} lb` : " (set a max)"}`, ex.notes, `Rest ${fmtDur(ex.rest_sec) || "0m"}`, ex.unit !== "lb" && UNIT_LABEL[ex.unit]].filter(Boolean).map(esc).join(" · ")}</div>
      <div class="s-menu hidden" id="exmenu-${ei}">
        <button class="text-btn" data-act="replace:${ei}">Replace</button><button class="text-btn" data-act="note:${ei}">Note</button>
        <button class="text-btn" data-act="rest:${ei}">Rest time</button><button class="text-btn" data-act="unit:${ei}">Unit</button>
        <button class="text-btn danger" data-act="remove:${ei}">Remove</button>
      </div>
      ${ex.note ? `<p class="s-note">${esc(ex.note)}</p>` : ""}
      <div class="s-table">
        <div class="s-row s-th"><span>Set</span><span>Previous</span><span>${ex.unit === "lb" ? "lb" : UNIT_LABEL[ex.unit]}</span><span>Reps</span><span>✓</span></div>
        ${ex.sets.map((s, si) => `
          <div class="s-row ${s.done ? "done" : ""}">
            <span class="set-no">${si + 1}</span>
            <span class="prev">${prevText(ex, si)}</span>
            <input class="s-in" inputmode="decimal" type="number" step="2.5" value="${s.weight ?? ""}" data-w="${ei}:${si}" placeholder="${ex.target ?? ""}" />
            <input class="s-in" inputmode="numeric" type="number" min="0" max="500" value="${s.reps ?? ""}" data-r="${ei}:${si}" placeholder="${ex.reps_target ?? ""}" />
            <button class="s-check ${s.done ? "on" : ""}" data-done="${ei}:${si}" aria-label="Mark set done">✓</button>
          </div>
          ${session.rest && session.rest.ex === ei && session.rest.set === si ? `<div class="rest-bar" data-rest="${ei}:${si}"><i></i><span></span><button class="text-btn" data-skiprest>Skip</button></div>` : ""}`).join("")}
      </div>
      <button class="add-set" data-addset="${ei}">+ Add Set (${fmtDur(ex.rest_sec)} rest)</button>
    </div>`).join("") || `<p class="hint">${session.kind === "cardio" ? "Cardio session. The timer is running; add notes below or add exercises." : "No exercises yet. Add some below."}</p>`;
  tickRest();
  const upd = (sel, key) => $$(sel).forEach((inp) => (inp.oninput = () => { const [e, s] = inp.dataset[key].split(":").map(Number); session.exercises[e].sets[s][key === "w" ? "weight" : "reps"] = inp.value; saveDraft(); }));
  upd("[data-w]", "w"); upd("[data-r]", "r");
  $$("[data-done]").forEach((b) => (b.onclick = () => {
    const [e, s] = b.dataset.done.split(":").map(Number); const set = session.exercises[e].sets[s];
    if (!set.done && set.weight === "" && session.exercises[e].target) set.weight = session.exercises[e].target;
    set.done = !set.done;
    const rest = session.exercises[e].rest_sec;
    session.rest = set.done && rest ? { ex: e, set: s, until: Date.now() + rest * 1000, total: rest } : (session.rest?.ex === e && session.rest?.set === s ? null : session.rest);
    saveDraft(); renderSession();
  }));
  $$("[data-addset]").forEach((b) => (b.onclick = () => { const ex = session.exercises[+b.dataset.addset]; const last = ex.sets[ex.sets.length - 1]; ex.sets.push({ weight: last?.weight ?? "", reps: "", done: false }); saveDraft(); renderSession(); }));
  $$("[data-skiprest]").forEach((b) => (b.onclick = () => { session.rest = null; saveDraft(); renderSession(); }));
  $$("[data-exmenu]").forEach((b) => (b.onclick = () => $(`exmenu-${b.dataset.exmenu}`).classList.toggle("hidden")));
  $$("[data-progress]").forEach((b) => (b.onclick = () => openProgress(b.dataset.progress)));
  $$("[data-act]").forEach((b) => (b.onclick = () => {
    const [act, i] = b.dataset.act.split(":"); const ex = session.exercises[+i];
    if (act === "remove" && confirm(`Remove ${ex.name}?`)) session.exercises.splice(+i, 1);
    if (act === "note") { const n = prompt("Note for this exercise", ex.note || ""); if (n != null) ex.note = n.slice(0, 300); }
    if (act === "rest") { const n = prompt("Rest time in seconds", ex.rest_sec); if (n != null && !isNaN(+n)) ex.rest_sec = Math.min(Math.max(+n, 0), 900); }
    if (act === "unit") { const order = ["lb", "plates", "pin"]; ex.unit = order[(order.indexOf(ex.unit) + 1) % order.length]; toast(`Unit: ${UNIT_LABEL[ex.unit]}`); }
    if (act === "replace") return openExercisePicker({ type: "replace", index: +i });
    saveDraft(); renderSession();
  }));
}
$("sessName").oninput = () => { session.name = $("sessName").value; saveDraft(); };
$("sessNotes").oninput = () => { session.notes = $("sessNotes").value; saveDraft(); };
$("sessAddEx").onclick = () => openExercisePicker({ type: "session" });
function closeSession() { clearInterval(sessTick); session = null; clearDraft(); $("session").classList.add("hidden"); document.body.classList.remove("no-scroll"); }
$("sessCancel").onclick = () => { if (confirm("Discard this workout? Nothing will be saved.")) { closeSession(); render(); } };
$("sessFinish").onclick = async () => {
  const unchecked = session.exercises.reduce((a, e) => a + e.sets.filter((s) => !s.done && (s.weight !== "" || s.reps !== "")).length, 0);
  if (unchecked && !confirm(`${unchecked} set${unchecked > 1 ? "s have" : " has"} values but no check. Save them anyway?`)) return;
  const now = new Date();
  const body = {
    date: session.date, name: session.name.trim() || "Workout", type: session.type, started_at: session.started_at, finished_at: now.toISOString(),
    duration_sec: Math.round((now - new Date(session.started_at)) / 1000), notes: session.notes,
    exercises: session.exercises.map((e) => ({ name: e.name, unit: e.unit, rest_sec: e.rest_sec, note: e.note || null,
      sets: e.sets.filter((s) => s.done || s.weight !== "" || s.reps !== "").map((s) => ({ weight: s.weight === "" ? null : +s.weight, reps: s.reps === "" ? null : +s.reps, done: s.done })) }))
      .filter((e) => e.sets.length || session.kind === "cardio"),
  };
  $("sessFinish").disabled = true;
  try {
    const r = await api("/api/training/sessions", { method: "POST", body: JSON.stringify(body) });
    closeSession(); toast(r.whoop ? `Saved · merged with WHOOP (strain ${r.whoop.strain ?? "—"})` : "Workout saved");
    location.hash = "#/health/lifting/history";
  } catch (e) { toast(e.message); }
  finally { $("sessFinish").disabled = false; }
};

// History + check calendar
async function loadHistory() {
  const [h, wk] = await Promise.all([api("/api/training/history"), api(`/api/workouts?start=${iso(new Date(Date.now() - 400 * 864e5))}&end=${iso(new Date(Date.now() + 864e5))}`)]);
  historyData = { sessions: h.sessions, other: wk.workouts.filter((w) => w.source !== "session"), days: new Set(h.workout_days) };
  if (!$("w-date").value) $("w-date").value = iso(new Date(Date.now() - new Date().getTimezoneOffset() * 6e4));
  if (!$("exerciseRows").children.length) exerciseRow();
  renderCheckCal(); renderHistory();
}
function renderCheckCal() {
  const y = ccMonth.getFullYear(), m = ccMonth.getMonth(), first = new Date(y, m, 1), last = new Date(y, m + 1, 0);
  const ds = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  $("checkCalTitle").textContent = first.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  let html = ["M", "T", "W", "T", "F", "S", "S"].map((d) => `<span class="cc-dow">${d}</span>`).join("");
  for (let i = 0; i < (first.getDay() + 6) % 7; i++) html += "<span></span>";
  let count = 0; const todayStr = ds(new Date());
  for (let d = 1; d <= last.getDate(); d++) {
    const key = ds(new Date(y, m, d)), on = historyData.days.has(key); if (on) count++;
    html += `<button class="cc-day ${on ? "on" : ""} ${key === todayStr ? "today" : ""} ${key === historyDay ? "sel" : ""}" data-ccday="${key}" ${on ? "" : "disabled"}>${d}${on ? '<i aria-hidden="true">✓</i>' : ""}</button>`;
  }
  $("checkCal").innerHTML = html;
  $("checkCalCount").textContent = `${count} workout day${count === 1 ? "" : "s"} this month`;
  $$("[data-ccday]").forEach((b) => (b.onclick = () => { historyDay = historyDay === b.dataset.ccday ? null : b.dataset.ccday; renderCheckCal(); renderHistory(); }));
}
$("ccPrev").onclick = () => { ccMonth = new Date(ccMonth.getFullYear(), ccMonth.getMonth() - 1, 1); renderCheckCal(); };
$("ccNext").onclick = () => { ccMonth = new Date(ccMonth.getFullYear(), ccMonth.getMonth() + 1, 1); renderCheckCal(); };
$("clearFilter").onclick = () => { historyDay = null; renderCheckCal(); renderHistory(); };
function renderHistory() {
  const items = [...historyData.sessions.map((s) => ({ ...s, _k: "s" })), ...historyData.other.map((w) => ({ ...w, _k: "o" }))]
    .filter((x) => !historyDay || x.date === historyDay)
    .sort((a, b) => (b.date + (b.started_at || "")).localeCompare(a.date + (a.started_at || ""))).slice(0, 60);
  $("historyFilter").classList.toggle("hidden", !historyDay);
  if (historyDay) $("historyFilterText").textContent = new Date(historyDay + "T00:00").toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  const longDate = (d) => new Date(d + "T00:00").toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric", year: "numeric" });
  $("historyList").innerHTML = items.map((x) => x._k === "s" ? `
    <div class="card h-card">
      <div class="h-head"><div><h3>${esc(x.name)}</h3><p class="h-date">${longDate(x.date)}${x.imported ? " · imported" : ""}</p></div>
        <button class="text-btn danger" data-delsess="${x.id}">Delete</button></div>
      <div class="h-stats">${[fmtDur(x.duration_sec) && `<span><b>${fmtDur(x.duration_sec)}</b> duration</span>`, `<span><b>${x.volume.toLocaleString()}</b> lb volume</span>`, `<span><b>${x.prs}</b> PR${x.prs === 1 ? "" : "s"}</span>`, x.whoop && `<span><b>${x.whoop.strain ?? "—"}</b> strain</span>`, x.whoop?.avg_hr && `<span><b>${x.whoop.avg_hr}</b> avg HR</span>`].filter(Boolean).join("")}</div>
      <div class="h-table"><div class="h-row h-th"><span>Exercise</span><span>Best set</span></div>
        ${x.summary.map((r) => `<button class="h-row" data-progress="${esc(r.name)}"><span>${r.sets} × ${esc(r.name)}${r.pr ? ' <em class="pr">PR</em>' : ""}</span><span>${r.best ? (r.best.weight == null ? `${r.best.reps ?? "—"} reps` : `${fmtW(r.best.weight, r.best.unit)} × ${r.best.reps ?? "?"}`) : "—"}</span></button>`).join("")}</div>
    </div>` : `
    <div class="card h-card h-simple"><div class="h-head"><div><h3>${esc(x.type)}${x.source === "whoop" ? '<span class="source-tag">WHOOP</span>' : ""}</h3><p class="h-date">${longDate(x.date)}</p></div>
      ${x.source === "manual" ? `<button class="text-btn danger" data-del="${esc(x.id)}">Delete</button>` : ""}</div>
      <p class="h-date">${describeWorkout(x)}</p>${x.exercises?.length ? `<p class="hint">${exerciseText(x)}</p>` : ""}</div>`).join("")
    || `<div class="card empty-state"><h3>No workouts yet</h3><p>Start one from the Planner.</p></div>`;
  $$("[data-delsess]").forEach((b) => (b.onclick = async () => { if (!confirm("Delete this workout session?")) return; await api(`/api/training/sessions/${b.dataset.delsess}`, { method: "DELETE" }); toast("Session deleted"); loadHistory(); }));
  $$("#historyList [data-del]").forEach((b) => (b.onclick = async () => { if (!confirm("Delete this workout?")) return; await api(`/api/workouts/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" }); toast("Workout deleted"); loadHistory(); }));
  $$("#historyList [data-progress]").forEach((b) => (b.onclick = () => openProgress(b.dataset.progress)));
}

// Exercise progress
async function openProgress(name) {
  $("pgTitle").textContent = name; $("pgStats").innerHTML = ""; $("pgList").innerHTML = ""; $("progressDialog").showModal();
  const { points } = await api(`/api/training/exercise?name=${encodeURIComponent(name)}`);
  if (!points.length) { $("pgStats").innerHTML = `<p class="hint">No logged sets yet.</p>`; charts["chart-progress"]?.destroy(); return; }
  const lbPts = points.filter((p) => p.unit === "lb");
  const bestE = Math.max(...lbPts.map((p) => p.e1rm || 0)), bestW = Math.max(...points.map((p) => p.top_weight || 0));
  $("pgStats").innerHTML = `<div><span class="kpi-label">Heaviest</span><strong>${fmtW(bestW, points[0].unit)}</strong></div>
    ${bestE ? `<div><span class="kpi-label">Best est. 1RM</span><strong>${Math.round(bestE)} lb</strong></div>` : ""}
    <div><span class="kpi-label">Sessions</span><strong>${points.length}</strong></div>`;
  const useE = lbPts.length && bestE;
  const multiYear = points[0].date.slice(0, 4) !== points[points.length - 1].date.slice(0, 4);
  const lbl = (d) => (multiYear ? `${shortDate(d)} '${d.slice(2, 4)}` : shortDate(d));
  drawChart("chart-progress", "line", points.map((p) => lbl(p.date)), points.map((p) => (useE ? p.e1rm : p.top_weight)),
    { tip: (v) => (useE ? `${Math.round(v)} lb est. 1RM` : fmtW(v, points[0].unit)), tick: (v) => `${v}` },
    { ds: { tension: 0, pointRadius: 3 } });
  $("pgList").innerHTML = [...points].reverse().map((p) => `<div class="mini-row"><span>${lbl(p.date)}${p.pr ? ' <em class="pr">PR</em>' : ""}</span><span class="tag">top ${fmtW(p.top_weight, p.unit)}${p.volume ? ` · ${p.volume.toLocaleString()} lb vol` : ""}</span></div>`).join("");
}
$("pgClose").onclick = () => $("progressDialog").close();

$("planImport").onchange = async (e) => {
  const f = e.target.files[0]; e.target.value = "";
  if (!f) return;
  try {
    const data = JSON.parse(await f.text());
    if (!data.template || !Array.isArray(data.sessions)) throw new Error("Not a LifeTracker program file");
    if (!confirm(`Import ${data.state?.program_name || "program"}: ${data.template.weeks?.length || 0} weeks, ${data.sessions.length} past sessions? This replaces your plan and any previously imported history.`)) return;
    const r = await api("/api/training/import", { method: "POST", body: JSON.stringify(data) });
    toast(`Imported ${r.imported_sessions ?? data.sessions.length} sessions`);
    renderWeekPlan.didInit = false; loadWeekPlan();
  } catch (err) { toast(err.message || "Import failed"); }
};

// Resume an unfinished workout after a reload
if (loadDraft()) setTimeout(() => toast("You have a workout in progress. Resume it from Health → Planner."), 1500);

// ---------- Wiring ----------
$("signin").onclick = () => signInWithPopup(auth, new GoogleAuthProvider()).catch((e) => {
  if (!/popup-closed-by-user|cancelled-popup-request/.test(e.code || "")) toast("Sign-in didn't finish. Try again.");
});
$("signout").onclick = () => signOut(auth);
$("sync").onclick = doSync;
$("range").onchange = () => render();
$("tx-search").oninput = () => { txShown = 50; renderTx(); };
$("tx-more").onclick = () => { txShown += 100; renderTx(); };
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

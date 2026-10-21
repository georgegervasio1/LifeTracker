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
  health: { title: "Health", tabs: [] },
  education: { title: "Education", tabs: [] },
  goals: { title: "Goals", tabs: [] },
};

function parseRoute() {
  const [section, tab] = location.hash.replace(/^#\/?/, "").split("/");
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
    $("financeControls").classList.toggle("hidden", section !== "finances" || ["assistant", "accounts"].includes(tab));
    $$(`[data-route="${section}"] .panel`).forEach((p) => p.classList.toggle("active", p.dataset.panel === tab));
  }
  window.scrollTo({ top: 0 });
  document.title = section === "home" ? "LifeTracker" : `${cfg.title} · LifeTracker`;
  load(section, tab).catch((e) => toast(e.message));
}

async function load(section, tab) {
  if (section === "home") return loadHome();
  if (section !== "finances") return;
  if (!backendReady) return toast("Backend not connected yet");
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

function drawChart(id, type, labels, data) {
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
      tension: 0.35, borderRadius: 6, maxBarThickness: 48, pointRadius: 0, pointHoverRadius: 4,
    }] },
    options: {
      aspectRatio: type === "line" ? 2.8 : 1.8,
      plugins: { legend: { display: false },
                 tooltip: { displayColors: false, callbacks: { label: (c) => usd.format(c.parsed.y) } } },
      scales: {
        y: { border: { display: false }, ticks: { color: k.text, maxTicksLimit: 5, callback: (v) => usd0.format(v) }, grid: { color: k.line } },
        x: { border: { display: false }, ticks: { color: k.text, maxTicksLimit: 7, maxRotation: 0 }, grid: { display: false } },
      },
    },
  });
}

function barRows(elId, entries, max = 7) {
  const rows = entries.slice(0, max);
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
  $("sync").disabled = true; $("sync").textContent = "Syncing…";
  try { await api("/api/sync", { method: "POST" }); toast("Up to date"); render(); }
  catch (e) { toast("Sync failed: " + e.message); }
  finally { $("sync").disabled = false; $("sync").textContent = "Sync"; }
}

// ---------- Wiring ----------
$("signin").onclick = () => signInWithPopup(auth, new GoogleAuthProvider()).catch((e) => toast(e.message));
$("signout").onclick = () => signOut(auth);
$("sync").onclick = doSync;
$("range").onchange = () => render();
$("tx-search").oninput = renderTx;
$$("[data-link]").forEach((b) => (b.onclick = () => connect(b.dataset.link).catch((e) => toast(e.message))));
$("venmo-file").onchange = () => ($("venmoLabel").textContent = $("venmo-file").files[0]?.name || "Choose a CSV file");
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

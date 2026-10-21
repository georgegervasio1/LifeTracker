import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import { getAuth, GoogleAuthProvider, signInWithPopup, signOut, onAuthStateChanged }
  from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import { API_BASE, FIREBASE_CONFIG } from "./config.js";

const auth = getAuth(initializeApp(FIREBASE_CONFIG));
const $ = (id) => document.getElementById(id);
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const backendReady = !API_BASE.includes("YOUR-");
const charts = {};
let allTx = [];
const chatHistory = []; // [{role, content}] — kept in the browser only

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
  const iso = (d) => d.toISOString().slice(0, 10);
  return `?start=${iso(new Date(Date.now() - days * 864e5))}&end=${iso(new Date())}`;
}

// ---------- Theme ----------
function applyTheme(light) {
  document.body.classList.toggle("light-mode", light);
  $("themeBtn").textContent = light ? "☀️" : "🌙";
  try { localStorage.setItem("lt-theme", light ? "light" : "dark"); } catch {}
}
try { applyTheme(localStorage.getItem("lt-theme") === "light"); } catch {}
$("themeBtn").onclick = () => { applyTheme(!document.body.classList.contains("light-mode")); refresh().catch(() => {}); };

// ---------- Charts ----------
const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();

function gradientFill(ctx) {
  const g = ctx.chart.ctx.createLinearGradient(0, 0, 0, ctx.chart.height || 300);
  g.addColorStop(0, "rgba(96,165,250,.45)"); g.addColorStop(1, "rgba(167,139,250,.05)");
  return g;
}

function drawChart(id, type, labels, data) {
  charts[id]?.destroy();
  Chart.defaults.color = css("--text-muted");
  Chart.defaults.borderColor = css("--border");
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  charts[id] = new Chart($(id), {
    type,
    data: { labels, datasets: [{
      data,
      backgroundColor: type === "line" ? gradientFill : (c) => {
        const g = c.chart.ctx.createLinearGradient(0, c.chart.height, 0, 0);
        g.addColorStop(0, "#3b82f6"); g.addColorStop(1, "#8b5cf6"); return g;
      },
      borderColor: "#60a5fa", borderWidth: type === "line" ? 2 : 0, fill: type === "line",
      tension: 0.35, borderRadius: 8, pointRadius: 0, pointHoverRadius: 4,
    }] },
    options: {
      aspectRatio: type === "line" ? 3 : 2,
      plugins: { legend: { display: false },
                 tooltip: { callbacks: { label: (c) => usd.format(c.parsed.y) } } },
      scales: { y: { ticks: { callback: (v) => usd0.format(v) }, grid: { color: css("--border-sm") } },
                x: { grid: { display: false }, ticks: { maxTicksLimit: 8, maxRotation: 0 } } },
    },
  });
}

function barRows(elId, entries, max = 8) {
  const rows = entries.slice(0, max);
  if (!rows.length) { $(elId).innerHTML = `<div class="empty">No data yet — connect an account or upload a CSV</div>`; return; }
  const top = rows[0][1] || 1;
  $(elId).innerHTML = rows.map(([label, v]) => `
    <div class="bar-row">
      <div class="bar-label" title="${esc(label)}">${esc(label)}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, (v / top) * 100)}%"></div></div>
      <div class="bar-count">${usd0.format(v)}</div>
    </div>`).join("");
}

// ---------- Views ----------
async function loadOverview() {
  const [s, h, tx] = await Promise.all([
    api("/api/summary" + rangeParams()), api("/api/holdings"), api("/api/transactions" + rangeParams())]);
  $("kpi-spent").textContent = usd0.format(s.total_spent);
  $("kpi-in").textContent = usd0.format(s.total_in);
  const net = s.total_in - s.total_spent;
  $("kpi-net").textContent = (net < 0 ? "−" : "") + usd0.format(Math.abs(net));
  $("kpi-net").className = "stat-value " + (net >= 0 ? "pos" : "neg");
  $("kpi-portfolio").textContent = h.latest ? usd0.format(h.latest.total) : "—";
  $("kpi-spent-sub").textContent = `${s.start} → ${s.end}`;
  $("catSubtitle").textContent = `${Object.keys(s.by_category).length} categories`;

  barRows("categoryBars", Object.entries(s.by_category));
  barRows("sourceBars", Object.entries(s.by_source));
  drawChart("chart-month", "bar", Object.keys(s.by_month), Object.values(s.by_month));

  allTx = tx.transactions;
  const byMerchant = {};
  allTx.filter((t) => t.amount > 0).forEach((t) => (byMerchant[t.merchant] = (byMerchant[t.merchant] || 0) + t.amount));
  barRows("merchantBars", Object.entries(byMerchant).sort((a, b) => b[1] - a[1]));
  renderTx();
}

function renderTx() {
  const q = $("tx-search").value.toLowerCase();
  const rows = allTx.filter((t) => !q || `${t.merchant} ${t.category} ${t.source} ${t.note || ""}`.toLowerCase().includes(q));
  $("tx-count").textContent = rows.length;
  $("tx-total").textContent = usd.format(rows.reduce((a, t) => a + t.amount, 0));
  $("tx-body").innerHTML = rows.slice(0, 500).map((t) => `
    <tr><td>${t.date}</td>
      <td>${esc(t.merchant)}${t.pending ? ' <span class="pill">pending</span>' : ""}</td>
      <td><span class="pill">${esc(t.category)}</span></td><td>${esc(t.source)}</td>
      <td class="num ${t.amount < 0 ? "pos" : ""}">${t.amount < 0 ? "+" : "−"}${usd.format(Math.abs(t.amount))}</td></tr>`).join("")
    || `<tr><td colspan="5" class="empty">No transactions in this range</td></tr>`;
}

async function loadInvestments() {
  const h = await api("/api/holdings");
  drawChart("chart-portfolio", "line", h.history.map((x) => x.date), h.history.map((x) => x.total));
  $("holdings-body").innerHTML = (h.latest?.holdings || []).sort((a, b) => b.value - a.value).map((x) => {
    const gain = x.cost_basis != null ? x.value - x.cost_basis : null;
    return `<tr><td><strong>${esc(x.ticker || "—")}</strong></td><td>${esc(x.name)}</td><td class="num">${x.quantity}</td>
      <td class="num">${usd.format(x.value)}</td>
      <td class="num ${gain == null ? "" : gain >= 0 ? "pos" : "neg"}">${gain == null ? "—" : usd.format(gain)}</td></tr>`;
  }).join("") || `<tr><td colspan="5" class="empty">Connect a brokerage and sync to see holdings</td></tr>`;
}

async function refresh() {
  if (!backendReady) { toast(`Signed in as ${auth.currentUser.email}. Backend not connected yet.`); return; }
  const active = document.querySelector(".tab-button.active").dataset.view;
  if (active === "investments") await loadInvestments();
  else if (active === "overview" || active === "transactions") await loadOverview();
}

// ---------- AI assistant ----------
function addMessage(role, html, meta = "") {
  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;
  wrap.innerHTML = `<div class="message-avatar">${role === "user" ? "YOU" : "AI"}</div>
    <div class="message-body"><div class="message-content">${html}</div>${meta ? `<div class="message-meta">${esc(meta)}</div>` : ""}</div>`;
  $("chatMessages").appendChild(wrap);
  $("chatMessages").scrollTop = $("chatMessages").scrollHeight;
  return wrap;
}

const renderMarkdown = (md) => DOMPurify.sanitize(marked.parse(md || ""));

async function sendChat(text) {
  text = (text || "").trim();
  if (!text || $("chatSend").disabled) return;
  if (!backendReady) return toast("Backend not connected yet");
  $("chatInput").value = "";
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
    addMessage("assistant", `⚠️ ${esc(e.message)}`);
  } finally {
    $("chatSend").disabled = false;
    $("chatInput").focus();
  }
}

$("chatSend").onclick = () => sendChat($("chatInput").value);
$("chatInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat($("chatInput").value); }
});
document.querySelectorAll(".suggestion-chip").forEach((c) => (c.onclick = () => sendChat(c.textContent)));

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
  $("sync").disabled = true;
  try { await api("/api/sync", { method: "POST" }); toast("Synced ✓"); await refresh(); }
  catch (e) { toast("Sync failed: " + e.message); }
  finally { $("sync").disabled = false; }
}

// ---------- Wiring ----------
function switchTab(view) {
  document.querySelectorAll(".tab-button").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  document.querySelectorAll(".tab-content").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  if (view === "assistant") $("chatInput").focus();
  refresh().catch((e) => toast(e.message));
}

$("signin").onclick = () => signInWithPopup(auth, new GoogleAuthProvider()).catch((e) => toast(e.message));
$("signout").onclick = () => signOut(auth);
$("sync").onclick = doSync;
$("range").onchange = () => refresh().catch((e) => toast(e.message));
$("tx-search").oninput = renderTx;
document.querySelectorAll(".tab-button").forEach((b) => (b.onclick = () => switchTab(b.dataset.view)));
document.querySelectorAll("[data-goto]").forEach((b) => (b.onclick = () => switchTab(b.dataset.goto)));
document.querySelectorAll("[data-link]").forEach((b) => (b.onclick = () => connect(b.dataset.link).catch((e) => toast(e.message))));
$("venmo-file").onchange = () => ($("venmoLabel").textContent = $("venmo-file").files[0]?.name || "📁 Click to choose a Venmo CSV");
$("venmo-upload").onclick = async () => {
  const f = $("venmo-file").files[0];
  if (!f) return toast("Choose a CSV first");
  const fd = new FormData(); fd.append("file", f);
  try { const r = await api("/api/venmo/upload", { method: "POST", body: fd }); toast(`Imported ${r.imported} Venmo transactions`); refresh(); }
  catch (e) { toast(e.message); }
};

onAuthStateChanged(auth, (user) => {
  $("login").classList.toggle("hidden", !!user);
  $("app").classList.toggle("hidden", !user);
  if (user) {
    const first = (user.displayName || "").split(" ")[0];
    $("heroTagline").textContent = `${first ? `Welcome back, ${first} — ` : ""}spending, cash flow and investments in one place`;
    refresh().catch((e) => toast(e.message));
  }
});

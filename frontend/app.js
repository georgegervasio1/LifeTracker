import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import { getAuth, GoogleAuthProvider, signInWithPopup, signOut, onAuthStateChanged }
  from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import { API_BASE, FIREBASE_CONFIG } from "./config.js";

const auth = getAuth(initializeApp(FIREBASE_CONFIG));
const $ = (id) => document.getElementById(id);
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const charts = {};
let allTx = [];

// ---------- API helper (attaches Firebase ID token) ----------
async function api(path, opts = {}) {
  const token = await auth.currentUser.getIdToken();
  const res = await fetch(API_BASE + path, {
    ...opts,
    headers: { Authorization: `Bearer ${token}`,
               ...(opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
               ...(opts.headers || {}) },
  });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), 3500);
}

function rangeParams() {
  const days = +$("range").value;
  const end = new Date(), start = new Date(Date.now() - days * 864e5);
  const iso = (d) => d.toISOString().slice(0, 10);
  return `?start=${iso(start)}&end=${iso(end)}`;
}

// ---------- Charts ----------
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const PALETTE = ["#5b8cff", "#3ddc97", "#ffb84d", "#ff6b6b", "#b07cff", "#4dd4ff", "#ff8fcf", "#c9d35b", "#8b96ad"];

function drawChart(id, type, labels, data, extra = {}) {
  charts[id]?.destroy();
  Chart.defaults.color = css("--muted");
  Chart.defaults.borderColor = css("--border");
  charts[id] = new Chart($(id), {
    type,
    data: { labels, datasets: [{ data, backgroundColor: type === "line" ? css("--accent") + "33" : PALETTE,
             borderColor: type === "line" ? css("--accent") : undefined, fill: type === "line",
             tension: 0.3, borderRadius: 6, borderWidth: type === "doughnut" ? 0 : 2 }] },
    options: { plugins: { legend: { display: type === "doughnut", position: "right" },
                          tooltip: { callbacks: { label: (c) => usd.format(c.parsed.y ?? c.parsed) } } },
               ...extra },
  });
}

// ---------- Views ----------
async function loadOverview() {
  const [s, h] = await Promise.all([api("/api/summary" + rangeParams()), api("/api/holdings")]);
  $("kpi-spent").textContent = usd.format(s.total_spent);
  $("kpi-in").textContent = usd.format(s.total_in);
  const net = s.total_in - s.total_spent;
  $("kpi-net").textContent = usd.format(net);
  $("kpi-net").className = "value " + (net >= 0 ? "pos" : "neg");
  $("kpi-portfolio").textContent = h.latest ? usd.format(h.latest.total) : "—";

  drawChart("chart-category", "doughnut", Object.keys(s.by_category), Object.values(s.by_category));
  drawChart("chart-month", "bar", Object.keys(s.by_month), Object.values(s.by_month));
  drawChart("chart-source", "bar", Object.keys(s.by_source), Object.values(s.by_source), { indexAxis: "y" });

  const tx = await api("/api/transactions" + rangeParams());
  allTx = tx.transactions;
  const byMerchant = {};
  allTx.filter((t) => t.amount > 0).forEach((t) => (byMerchant[t.merchant] = (byMerchant[t.merchant] || 0) + t.amount));
  $("top-merchants").innerHTML = Object.entries(byMerchant).sort((a, b) => b[1] - a[1]).slice(0, 8)
    .map(([m, v]) => `<li><span>${esc(m)}</span><span>${usd.format(v)}</span></li>`).join("");
  renderTx();
}

function renderTx() {
  const q = $("tx-search").value.toLowerCase();
  $("tx-body").innerHTML = allTx
    .filter((t) => !q || `${t.merchant} ${t.category} ${t.source}`.toLowerCase().includes(q))
    .slice(0, 500)
    .map((t) => `<tr><td>${t.date}</td><td>${esc(t.merchant)}${t.pending ? ' <span class="pill">pending</span>' : ""}</td>
      <td><span class="pill">${esc(t.category)}</span></td><td>${esc(t.source)}</td>
      <td class="num ${t.amount < 0 ? "pos" : ""}">${usd.format(-t.amount)}</td></tr>`).join("");
}

async function loadInvestments() {
  const h = await api("/api/holdings");
  drawChart("chart-portfolio", "line", h.history.map((x) => x.date), h.history.map((x) => x.total));
  $("holdings-body").innerHTML = (h.latest?.holdings || []).sort((a, b) => b.value - a.value).map((x) => {
    const gain = x.cost_basis != null ? x.value - x.cost_basis : null;
    return `<tr><td>${esc(x.ticker || "—")}</td><td>${esc(x.name)}</td><td class="num">${x.quantity}</td>
      <td class="num">${usd.format(x.value)}</td>
      <td class="num ${gain == null ? "" : gain >= 0 ? "pos" : "neg"}">${gain == null ? "—" : usd.format(gain)}</td></tr>`;
  }).join("") || `<tr><td colspan="5" class="muted">Connect a brokerage and sync.</td></tr>`;
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------- Plaid Link ----------
async function connect(kind) {
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
  $("sync").disabled = true;
  try { await api("/api/sync", { method: "POST" }); toast("Synced"); await refresh(); }
  catch (e) { toast("Sync failed: " + e.message); }
  finally { $("sync").disabled = false; }
}

async function refresh() {
  const active = document.querySelector(".tab.active").dataset.view;
  if (active === "investments") await loadInvestments(); else await loadOverview();
}

// ---------- Wiring ----------
$("signin").onclick = () => signInWithPopup(auth, new GoogleAuthProvider());
$("signout").onclick = () => signOut(auth);
$("sync").onclick = doSync;
$("range").onchange = refresh;
$("tx-search").oninput = renderTx;
document.querySelectorAll("[data-link]").forEach((b) => (b.onclick = () => connect(b.dataset.link).catch((e) => toast(e.message))));
document.querySelectorAll(".tab").forEach((tab) => (tab.onclick = () => {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("hidden", v.id !== "view-" + tab.dataset.view));
  refresh().catch((e) => toast(e.message));
}));
$("venmo-upload").onclick = async () => {
  const f = $("venmo-file").files[0];
  if (!f) return toast("Pick a CSV first");
  const fd = new FormData(); fd.append("file", f);
  try { const r = await api("/api/venmo/upload", { method: "POST", body: fd }); toast(`Imported ${r.imported} Venmo transactions`); refresh(); }
  catch (e) { toast(e.message); }
};

onAuthStateChanged(auth, (user) => {
  $("login").classList.toggle("hidden", !!user);
  $("app").classList.toggle("hidden", !user);
  if (user) refresh().catch((e) => toast(e.message));
});

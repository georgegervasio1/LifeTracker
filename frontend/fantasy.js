// Sports → Fantasy Football: the user's ESPN league (backend/fantasy.py).
// Tabs: Matchup (this week, live scores and projections, my season), Roster, Trades (suggestions + a trade builder,
// priced with FantasyCalc values), Standings, League (connection). Several leagues: a switcher at the top.
// A private league needs the espn_s2 and SWID cookies from espn.com; they stay on the server.

let H;
let conn = null, data = null;
let trades = null, tradeFor = "", builder = { partner: null, give: new Set(), get: new Set() };

export function initFantasy(helpers) { H = helpers; }

const pts = (v) => (v == null ? "—" : (+v).toFixed(2));
const pts1 = (v) => (v == null ? "—" : (+v).toFixed(1));
const rec = (t) => `${t.wins}-${t.losses}${t.ties ? `-${t.ties}` : ""}`;
const team = (id) => (data.teams || []).find((t) => t.id === id) || { name: "Bye", abbrev: "", owners: [] };
const badge = (t, cls = "ff-logo") => t.logo ? `<img class="${cls}" src="${H.esc(t.logo)}" alt="" referrerpolicy="no-referrer" loading="lazy">`
  : `<span class="${cls} sp-logo-txt">${H.esc((t.abbrev || t.name || "?").slice(0, 4))}</span>`;
const INJ = { QUESTIONABLE: "Q", DOUBTFUL: "D", OUT: "O", INJURY_RESERVE: "IR", SUSPENSION: "SSPD", DAY_TO_DAY: "DTD" };

// the stat on the Sports dashboard card
export async function fantasyCard() {
  conn = await H.api("/api/fantasy");
  if (!conn.connected) return { big: "—", line: "Connect your ESPN league" };
  data = await H.api("/api/fantasy/league");
  const me = data.teams.find((t) => t.id === data.my_team_id);
  if (!me) return { big: "—", line: data.league.name || "Pick your team" };
  const rank = data.teams.indexOf(me) + 1;
  return { big: rec(me), unit: `${ordinal(rank)} of ${data.teams.length}`, line: weekLine() || data.league.name };
}
function ordinal(n) { const s = ["th", "st", "nd", "rd"], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); }
function myMatchup() {
  return (data.this_week || []).find((m) => [m.home?.team_id, m.away?.team_id].includes(data.my_team_id));
}
function weekLine() {
  const m = myMatchup(); if (!m) return "";
  const [me, op] = m.home?.team_id === data.my_team_id ? [m.home, m.away] : [m.away, m.home];
  return `Week ${data.league.week}: ${pts1(me.points)} to ${pts1(op?.points)} vs ${team(op?.team_id).name}`;
}

export async function loadFantasy(tab, el) {
  if (!conn || tab === "league") conn = await H.api("/api/fantasy");
  if (!conn.connected) return renderConnect(el);
  if (!data || data.conn?.league_id !== conn.active) { el.innerHTML = `<p class="empty">Loading your league…</p>`; trades = null; }
  try { data = await H.api("/api/fantasy/league"); }
  catch (e) {
    el.innerHTML = `<div class="card empty-state"><h3>Couldn't read your league</h3><p>${H.esc(e.message)}</p>
      <a class="btn btn-secondary" href="#/sports/fantasy/league">League settings</a></div>`;
    addSwitcher(el);
    return;
  }
  if (tab === "league") renderLeague(el);
  else if (data.my_team_id == null) renderLeague(el, "Pick which team is yours.");
  else if (tab === "roster") renderRoster(el);
  else if (tab === "standings") renderStandings(el);
  else if (tab === "trades") { addSwitcher(el, true); return renderTrades(el); }
  else renderMatchup(el);
  addSwitcher(el);
}

// one row of league pills above every tab (only when there's more than one league), plus "Add a league"
function addSwitcher(el, clear) {
  const L = conn.leagues || [];
  const html = `<div class="ff-switch">${L.length > 1 ? L.map((l) => `<button class="sp-filter${l.league_id === conn.active ? " on" : ""}" data-lg="${H.esc(l.league_id)}"><i></i>${H.esc(l.league_name || `League ${l.league_id}`)}</button>`).join("") : ""}
    <button class="text-btn accent" data-add-league>Add a league</button></div>`;
  if (clear) el.innerHTML = html; else el.insertAdjacentHTML("afterbegin", html);
  el.querySelectorAll("[data-lg]").forEach((b) => b.onclick = async () => {
    if (b.dataset.lg === conn.active) return;
    conn = await H.api("/api/fantasy/active", { method: "PUT", body: JSON.stringify({ league_id: b.dataset.lg }) });
    data = null; trades = null; builder = { partner: null, give: new Set(), get: new Set() };
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
  el.querySelector("[data-add-league]").onclick = () => renderConnect(el, "", true);
}

// ---------------------------------------------------------------- connect
function renderConnect(el, err = "", adding = false) {
  const cur = adding ? {} : (conn || {}), saved = !!(conn?.leagues || []).some((l) => l.has_cookies);
  const yr = new Date().getMonth() <= 1 ? new Date().getFullYear() - 1 : new Date().getFullYear();
  el.innerHTML = `<div class="grid-2 ff-connect">
    <form class="card" id="ffForm" autocomplete="off">
      <h3>${adding ? "Add another ESPN league" : "Connect your ESPN league"}</h3>
      <p>LifeTracker reads your league the same way the ESPN site does. It only reads: it can't set lineups or make moves.</p>
      <label class="ff-field"><span>League ID or league URL</span>
        <input class="input" name="league_id" required placeholder="e.g. 1234567 or https://fantasy.espn.com/football/league?leagueId=…" value="${H.esc(cur.league_id || "")}"></label>
      <label class="ff-field"><span>Season</span><input class="input" name="season" type="number" min="2018" max="${yr + 1}" value="${H.esc(cur.season || yr)}"></label>
      <details class="ff-private" ${saved ? "" : "open"}><summary>Private league? Add your ESPN cookies</summary>
        <label class="ff-field"><span>espn_s2</span><input class="input" name="espn_s2" placeholder="${saved ? "Saved. Paste a new one to replace it." : "A long value starting with AE…"}"></label>
        <label class="ff-field"><span>SWID</span><input class="input" name="swid" placeholder="${saved ? "Saved" : "{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}"}"></label>
      </details>
      ${saved ? `<p class="hint">Your saved ESPN cookies work for every league on the same ESPN account, so you can leave them blank.</p>` : ""}
      ${err ? `<p class="ff-err">${H.esc(err)}</p>` : ""}
      <div class="ff-actions"><button class="btn btn-primary" type="submit">${adding ? "Add league" : "Connect league"}</button>
        ${adding ? `<button class="btn btn-secondary" type="button" id="ffCancel">Cancel</button>` : ""}</div>
    </form>
    <div class="card ff-steps"><h3>Where to find these</h3>
      <ol>
        <li><b>League ID:</b> open your league on fantasy.espn.com on a computer. It's the number after <code>leagueId=</code> in the address bar. You can paste the whole address.</li>
        <li><b>Public league?</b> That's all you need. Most leagues are private, so keep going.</li>
        <li><b>Cookies:</b> while signed in to ESPN in Chrome, open View → Developer → Developer Tools, then Application → Cookies → https://fantasy.espn.com.</li>
        <li>Copy the values of <code>espn_s2</code> and <code>SWID</code> into the form.</li>
      </ol>
      <p class="hint">ESPN's sign-in can't be done from another site, so many fantasy tools link ESPN leagues this same way. The cookies are kept on the LifeTracker server and never shown again. If you sign out of ESPN or the league stops loading, paste new ones.</p>
    </div></div>`;
  if (H.$("ffCancel")) H.$("ffCancel").onclick = () => window.dispatchEvent(new HashChangeEvent("hashchange"));
  H.$("ffForm").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true; btn.textContent = "Connecting…";
    try {
      const r = await H.api("/api/fantasy/connect", { method: "PUT", body: JSON.stringify({ ...f, season: +f.season }) });
      conn = r; data = null; trades = null; builder = { partner: null, give: new Set(), get: new Set() };
      H.toast(`Connected ${r.league_name || "your league"}`);
      location.hash = r.team_id == null ? "#/sports/fantasy/league" : "#/sports/fantasy/matchup";
      if (location.hash === "#/sports/fantasy/league") loadFantasy("league", el).catch(H.showError);
    } catch (err) { renderConnect(el, err.message, adding); }
  };
}

// ---------------------------------------------------------------- matchup
function side(m) {
  const mine = m.home?.team_id === data.my_team_id;
  return mine ? [m.home, m.away] : [m.away, m.home];
}
function renderMatchup(el) {
  const L = data.league, m = myMatchup();
  let top;
  if (!m) top = `<div class="card empty-state"><h3>No matchup this week</h3><p>${L.in_playoffs ? "Your season may be over, or this is a bye in the playoffs." : "ESPN shows no game for your team this week."}</p></div>`;
  else {
    const [me, op] = side(m), A = team(me.team_id), B = op ? team(op.team_id) : null;
    const lead = me.points != null && op?.points != null ? me.points - op.points : null;
    const done = !!m.winner;
    const status = done ? (m.winner === "tie" ? "Tied" : (m.winner === (m.home?.team_id === data.my_team_id ? "home" : "away") ? "Won" : "Lost"))
      : lead == null ? "" : lead > 0 ? `Up ${pts1(lead)}` : lead < 0 ? `Down ${pts1(-lead)}` : "Tied";
    const col = (s, t, me_) => `<div class="ff-side ${me_ ? "me" : ""}">${badge(t, "ff-logo-lg")}
        <span class="ff-team">${H.esc(t.name)}</span><span class="muted ff-rec">${t.wins != null ? rec(t) : ""}${t.owners?.length ? ` · ${H.esc(t.owners[0])}` : ""}</span>
        <span class="ff-score">${pts(s?.points)}</span><span class="muted">Projected ${pts1(s?.projected)}</span></div>`;
    top = `<div class="card ff-board">
      <div class="ff-board-head"><span class="eyebrow">Week ${L.week}${m.playoff ? " · Playoffs" : ""}</span>
        <span class="pill ${/Up|Won/.test(status) ? "pill-met" : /Down|Lost/.test(status) ? "pill-missing" : "pill-plain"}">${status || "Not started"}</span></div>
      <div class="ff-vs">${col(me, A, true)}<span class="ff-at">vs</span>${B ? col(op, B) : `<div class="ff-side"><span class="ff-team">Bye</span></div>`}</div>
    </div>`;
  }
  const others = (data.this_week || []).filter((x) => x !== m);
  const season = (data.my_schedule || []).map((g) => {
    const [me, op] = side(g), o = op ? team(op.team_id) : { name: "Bye" };
    const won = g.winner && g.winner !== "tie" ? (g.winner === (g.home?.team_id === data.my_team_id ? "home" : "away")) : null;
    const res = !g.winner ? "" : g.winner === "tie" ? "T" : won ? "W" : "L";
    return `<tr class="${g.week === L.week ? "ff-cur" : ""}"><td>Week ${g.week}${g.playoff ? ` <span class="muted">Playoffs</span>` : ""}</td>
      <td><span class="sp-opp">${badge(o, "sp-logo-sm")}${H.esc(o.name)}</span></td>
      <td class="num">${res ? `<span class="sp-res sp-res-${res}">${res}</span> ` : ""}${g.winner || g.week === L.week ? `${pts1(me.points)}–${pts1(op?.points)}` : ""}</td></tr>`;
  }).join("");
  el.innerHTML = `${top}
    ${others.length ? `<h3 class="ff-h3">Around the league</h3><div class="ff-others">${others.map((x) => {
      const h = team(x.home?.team_id), a = x.away ? team(x.away.team_id) : null;
      return `<div class="ff-mini"><div><span class="sp-opp">${badge(h, "sp-logo-sm")}${H.esc(h.name)}</span><b>${pts1(x.home?.points)}</b></div>
        ${a ? `<div><span class="sp-opp">${badge(a, "sp-logo-sm")}${H.esc(a.name)}</span><b>${pts1(x.away?.points)}</b></div>` : ""}</div>`; }).join("")}</div>` : ""}
    <h3 class="ff-h3">Your season</h3>
    <div class="card card-flush"><table class="table"><thead><tr><th>Week</th><th>Opponent</th><th class="num">Score</th></tr></thead><tbody>${season}</tbody></table></div>
    ${foot()}`;
}
const foot = () => `<p class="sp-note">${H.esc(data.league.name)} · ${data.league.season} season · from ESPN, refreshed every couple of minutes.
  <button class="text-btn" id="ffRefresh">Refresh now</button></p>`;
document.addEventListener("click", async (e) => {
  if (e.target.id !== "ffRefresh") return;
  data = await H.api("/api/fantasy/league?fresh=1").catch((er) => { H.showError(er); return data; });
  window.dispatchEvent(new HashChangeEvent("hashchange"));
});

// ---------------------------------------------------------------- roster
function renderRoster(el) {
  const me = team(data.my_team_id), R = data.roster || [];
  const row = (p) => `<tr class="${p.starter ? "" : "ff-bench"}"><td class="ff-slot">${H.esc(p.slot)}</td>
    <td><span class="ff-pname">${H.esc(p.name)}</span>${p.injury ? ` <span class="ff-inj">${H.esc(INJ[p.injury] || p.injury)}</span>` : ""}
      <span class="muted ff-pmeta">${H.esc(p.pos)} · ${H.esc(p.pro)}</span></td>
    <td class="num">${pts1(p.projected)}</td><td class="num"><b>${p.points == null ? "—" : pts1(p.points)}</b></td>
    <td class="num ff-hide-sm">${pts1(p.season_points)}</td></tr>`;
  const starters = R.filter((p) => p.starter), bench = R.filter((p) => !p.starter);
  const sum = (k) => starters.reduce((s, p) => s + (p[k] || 0), 0);
  el.innerHTML = `<div class="sp-head"><div><p class="eyebrow">Week ${data.league.week} lineup</p><h2 class="sp-h2">${H.esc(me.name)}</h2>
      <p class="muted">${rec(me)}${me.owners?.length ? ` · ${H.esc(me.owners.join(", "))}` : ""}</p></div>
      <div class="ff-totals"><div><span class="muted">Projected</span><b>${pts1(sum("projected"))}</b></div><div><span class="muted">Scored</span><b>${pts1(sum("points"))}</b></div></div></div>
    ${R.length ? `<div class="card card-flush"><table class="table ff-roster"><thead><tr><th>Slot</th><th>Player</th><th class="num">Proj</th><th class="num">Pts</th><th class="num ff-hide-sm">Season</th></tr></thead>
      <tbody>${starters.map(row).join("")}${bench.length ? `<tr class="ff-divider"><td colspan="5">Bench</td></tr>${bench.map(row).join("")}` : ""}</tbody></table></div>`
      : `<p class="empty">ESPN didn't send a roster. If the season hasn't drafted yet, check back after the draft.</p>`}
    ${foot()}`;
}

// ---------------------------------------------------------------- standings
function renderStandings(el) {
  const L = data.league, cut = L.playoff_teams || 0;
  const anyPct = data.teams.some((t) => t.playoff_pct != null);
  el.innerHTML = `<div class="sp-head"><div><p class="eyebrow">Standings</p><h2 class="sp-h2">${H.esc(L.name)}</h2>
      <p class="muted">${[L.regular_season_weeks ? `${L.regular_season_weeks}-week regular season` : "", cut ? `top ${cut} make the playoffs` : ""].filter(Boolean).join(", ")}</p></div></div>
    <div class="card card-flush"><div class="table-scroll"><table class="table ff-stand"><thead><tr><th>Team</th><th class="num">W-L</th><th class="num">PF</th><th class="num">PA</th><th class="num">Strk</th>${anyPct ? `<th class="num" title="ESPN's simulation">Playoff %</th>` : ""}</tr></thead>
    <tbody>${data.teams.map((t, i) => `<tr class="${t.id === data.my_team_id ? "sp-fav" : ""}${cut && i === cut - 1 ? " ff-cut" : ""}">
      <td><span class="sp-opp"><span class="sp-rank">${t.seed || i + 1}</span>${badge(t, "sp-logo-sm")}<span><span class="ff-pname">${H.esc(t.name)}</span>
        <span class="muted ff-pmeta">${H.esc(t.owners.join(", "))}</span></span></span></td>
      <td class="num">${rec(t)}</td><td class="num">${pts1(t.pf)}</td><td class="num">${pts1(t.pa)}</td><td class="num">${H.esc(t.streak)}</td>
      ${anyPct ? `<td class="num">${t.playoff_pct == null ? "—" : `${Math.round(t.playoff_pct)}%`}</td>` : ""}</tr>`).join("")}</tbody></table></div></div>
    <p class="sp-note">${cut ? "The line marks the playoff cut. " : ""}Playoff % is ESPN's own simulation.</p>${foot()}`;
}

// ---------------------------------------------------------------- league settings
function renderLeague(el, msg = "") {
  const opts = data.teams.map((t) => `<option value="${t.id}"${t.id === data.my_team_id ? " selected" : ""}>${H.esc(t.name)}${t.owners.length ? ` (${H.esc(t.owners[0])})` : ""}</option>`).join("");
  el.innerHTML = `<div class="grid-2">
    <div class="card"><h3>${H.esc(data.league.name || "Your league")}</h3>
      <p>${data.league.season} season · ${data.league.size} teams · week ${data.league.week}${conn.has_cookies ? " · private league" : ""}</p>
      ${msg ? `<p class="ff-err">${H.esc(msg)}</p>` : ""}
      <label class="ff-field"><span>Your team</span><select class="select ff-select" id="ffTeam"><option value="">Pick your team</option>${opts}</select></label>
      <div class="ff-actions"><button class="btn btn-secondary btn-sm" id="ffEdit">Change league or cookies</button>
        <button class="text-btn danger" id="ffDrop">${(conn.leagues || []).length > 1 ? "Remove this league" : "Disconnect"}</button></div></div>
    <div class="card"><h3>What LifeTracker reads</h3>
      <p>Standings, every team's record and points, this week's scores and projections, and your roster. Other managers' names and rosters are read live from ESPN and aren't saved.</p></div></div>`;
  H.$("ffTeam").onchange = async (e) => {
    if (!e.target.value) return;
    conn = await H.api("/api/fantasy/team", { method: "PUT", body: JSON.stringify({ team_id: +e.target.value }) });
    trades = null;
    data.my_team_id = conn.team_id;
    H.toast("Saved your team"); location.hash = "#/sports/fantasy/matchup";
  };
  H.$("ffEdit").onclick = () => renderConnect(el);
  H.$("ffDrop").onclick = async () => {
    const last = (conn.leagues || []).length <= 1;
    if (!confirm(last ? "Disconnect this league? Your ESPN cookies are deleted from LifeTracker." : "Remove this league from LifeTracker?")) return;
    conn = await H.api(`/api/fantasy/connect?league_id=${encodeURIComponent(conn.active)}`, { method: "DELETE" }); data = null; trades = null;
    if (conn.connected) window.dispatchEvent(new HashChangeEvent("hashchange")); else renderConnect(el);
  };
}

// ---------------------------------------------------------------- trades (FantasyCalc values)
const val = (v) => (v == null ? "—" : Math.round(v).toLocaleString("en-US"));
const signed = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${val(Math.abs(v))}`;
const FC_LINK = `<a href="https://fantasycalc.com/trade-calculator" target="_blank" rel="noopener">FantasyCalc</a>`;
function fmtLine(f) {
  const slots = Object.entries(f.slots).map(([k, v]) => `${v} ${k}`).join(", ");
  return `${f.qbs === 2 ? "Superflex / 2QB" : "1QB"} · ${f.ppr === 1 ? "PPR" : f.ppr === 0.5 ? "Half PPR" : "Standard"} · ${f.teams} teams · starters: ${slots}`;
}
const chip = (p) => `<span class="tr-player"><b>${H.esc(p.name)}</b><span class="muted">${H.esc(p.pos)}${p.pro ? ` · ${H.esc(p.pro)}` : ""}</span><span class="tr-val">${val(p.value)}</span></span>`;

async function renderTrades(el) {
  const box = document.createElement("div");
  el.appendChild(box);
  if (!trades) {
    box.innerHTML = `<p class="empty">Pricing every roster with FantasyCalc and testing trades…</p>`;
    try { trades = await H.api("/api/fantasy/trades"); }
    catch (e) { box.innerHTML = `<div class="card empty-state"><h3>Couldn't build trade ideas</h3><p>${H.esc(e.message)}</p></div>`; return; }
  }
  const T = trades, tname = (id) => (T.teams.find((t) => t.id === id) || {}).name || "";
  const list = T.trades.filter((t) => !tradeFor || String(t.team_id) === tradeFor);
  box.innerHTML = `<div class="sp-head"><div><p class="eyebrow">Trade ideas</p><h2 class="sp-h2">Fair trades that make your lineup better</h2>
      <p class="muted">${H.esc(fmtLine(T.format))}</p></div>
      <select class="select" id="trFor" aria-label="Trade partner"><option value="">Every team</option>${T.teams.filter((t) => t.id !== T.my_team_id)
        .map((t) => `<option value="${t.id}"${String(t.id) === tradeFor ? " selected" : ""}>${H.esc(t.name)}</option>`).join("")}</select></div>
    ${list.length ? `<div class="tr-list">${list.map((t, i) => `<div class="card tr-card">
        <div class="tr-top"><span class="eyebrow">With ${H.esc(t.team)}</span>
          <span class="pill ${t.their_gain > 0 ? "pill-met" : "pill-plain"}">${t.their_gain > 0 ? "Helps both teams" : "Even for them"}</span></div>
        <div class="tr-sides"><div><p class="tr-label">You give</p>${t.give.map(chip).join("")}<p class="tr-sum">${val(t.value_give)}</p></div>
          <div><p class="tr-label">You get</p>${t.get.map(chip).join("")}<p class="tr-sum">${val(t.value_get)}</p></div></div>
        <p class="tr-why">${H.esc(t.why)}</p>
        <div class="tr-foot"><span>Your lineup <b class="pos">${signed(t.my_gain)}</b></span><span>Theirs <b>${signed(t.their_gain)}</b></span>
          <span class="muted">Values ${t.fair_pct}% apart</span><button class="text-btn accent" data-load="${i}">Open in trade builder</button></div>
      </div>`).join("")}</div>`
      : `<div class="card empty-state"><h3>No fair trade improves your lineup${tradeFor ? " with this team" : ""}</h3>
          <p>Every trade within 12% on FantasyCalc value either weakens your starters or theirs. Try the builder below to test your own idea.</p></div>`}
    ${T.unpriced.length ? `<p class="sp-note">No FantasyCalc value for: ${H.esc(T.unpriced.join(", "))}. They count as 0.</p>` : ""}
    <div id="trBuilder"></div>
    <p class="sp-note">Trade values from ${FC_LINK} (redraft, crowd-sourced from real trades), refreshed every few hours. Lineup change = the value of your best legal starting lineup after the trade minus before, plus a little for bench depth. Kickers and D/ST have no values and aren't traded.</p>`;
  H.$("trFor").onchange = (e) => { tradeFor = e.target.value; box.remove(); renderTrades(el); };
  box.querySelectorAll("[data-load]").forEach((b) => b.onclick = () => {
    const t = list[+b.dataset.load];
    builder = { partner: t.team_id, give: new Set(t.give.map((p) => p.id)), get: new Set(t.get.map((p) => p.id)) };
    drawBuilder(); H.$("trBuilder").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  drawBuilder();
}

let evalTimer = null;
function drawBuilder() {
  const T = trades, wrap = H.$("trBuilder");
  if (!wrap) return;
  const others = T.teams.filter((t) => t.id !== T.my_team_id);
  if (builder.partner == null) builder.partner = others[0]?.id ?? null;
  const mine = T.rosters[String(T.my_team_id)] || [], theirs = T.rosters[String(builder.partner)] || [];
  const col = (players, set, side) => players.filter((p) => ["QB", "RB", "WR", "TE"].includes(p.pos)).map((p) => `
    <label class="tr-pick${set.has(p.id) ? " on" : ""}"><input type="checkbox" data-side="${side}" value="${p.id}"${set.has(p.id) ? " checked" : ""}>
      <span><b>${H.esc(p.name)}</b> <span class="muted">${H.esc(p.pos)} · ${H.esc(p.pro || "")}${p.priced ? "" : " · no value"}</span></span><span class="tr-val">${val(p.value)}</span></label>`).join("");
  wrap.innerHTML = `<h3 class="ff-h3">Trade builder</h3>
    <div class="card tr-builder">
      <label class="ff-field"><span>Trade with</span><select class="select ff-select" id="trPartner">${others.map((t) =>
        `<option value="${t.id}"${t.id === builder.partner ? " selected" : ""}>${H.esc(t.name)}${t.owners?.length ? ` (${H.esc(t.owners[0])})` : ""}</option>`).join("")}</select></label>
      <div class="tr-cols"><div><p class="tr-label">You give</p>${col(mine, builder.give, "give")}</div>
        <div><p class="tr-label">You get</p>${col(theirs, builder.get, "get")}</div></div>
      <div id="trResult" class="tr-result"><p class="muted">Pick players on both sides to see how the trade grades.</p></div>
    </div>`;
  H.$("trPartner").onchange = (e) => { builder = { partner: +e.target.value, give: builder.give, get: new Set() }; drawBuilder(); };
  wrap.querySelectorAll("input[data-side]").forEach((cb) => cb.onchange = () => {
    const set = builder[cb.dataset.side], id = +cb.value;
    cb.checked ? set.add(id) : set.delete(id);
    cb.closest(".tr-pick").classList.toggle("on", cb.checked);
    clearTimeout(evalTimer); evalTimer = setTimeout(gradeBuilder, 250);
  });
  if (builder.give.size || builder.get.size) gradeBuilder();
}
async function gradeBuilder() {
  const out = H.$("trResult");
  if (!out) return;
  if (!builder.give.size || !builder.get.size) { out.innerHTML = `<p class="muted">Pick players on both sides to see how the trade grades.</p>`; return; }
  try {
    const { trade: t } = await H.api("/api/fantasy/trades", { method: "POST",
      body: JSON.stringify({ partner: builder.partner, give: [...builder.give], get: [...builder.get] }) });
    const verdict = !t.fair ? (t.value_diff > 0 ? "You'd win this trade on value. They probably won't accept." : "You'd overpay on value.")
      : t.my_gain > 0 && t.their_gain >= 0 ? "Fair, and both lineups get better. Worth proposing."
      : t.my_gain > 0 ? "Fair and helps you, but their lineup gets worse." : "Fair on value, but it doesn't make your lineup better.";
    const good = t.fair && t.my_gain > 0;
    const max = Math.max(t.value_give, t.value_get) || 1;
    out.innerHTML = `<div class="tr-bars"><div><span>You give</span><i style="width:${(t.value_give / max) * 100}%"></i><b>${val(t.value_give)}</b></div>
        <div><span>You get</span><i class="get" style="width:${(t.value_get / max) * 100}%"></i><b>${val(t.value_get)}</b></div></div>
      <p class="tr-verdict ${good ? "pos" : t.fair ? "" : "neg"}">${verdict}</p>
      <div class="tr-foot"><span>Your lineup <b class="${t.my_gain > 0 ? "pos" : t.my_gain < 0 ? "neg" : ""}">${signed(t.my_gain)}</b></span>
        <span>Theirs <b>${signed(t.their_gain)}</b></span><span class="muted">Values ${t.fair_pct}% apart</span></div>`;
  } catch (e) { out.innerHTML = `<p class="ff-err">${H.esc(e.message)}</p>`; }
}

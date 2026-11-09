// Sports → Fantasy Football: the user's ESPN league (backend/fantasy.py).
// Tabs: Matchup (this week, live scores and projections, my season), Roster, Standings, League (connection).
// A private league needs the espn_s2 and SWID cookies from espn.com; they stay on the server.

let H;
let conn = null, data = null;

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
  if (!data) { el.innerHTML = `<p class="empty">Loading your league…</p>`; }
  try { data = await H.api("/api/fantasy/league"); }
  catch (e) {
    el.innerHTML = `<div class="card empty-state"><h3>Couldn't read your league</h3><p>${H.esc(e.message)}</p>
      <a class="btn btn-secondary" href="#/sports/fantasy/league">League settings</a></div>`;
    return;
  }
  if (tab === "league") return renderLeague(el);
  if (data.my_team_id == null) return renderLeague(el, "Pick which team is yours.");
  if (tab === "roster") return renderRoster(el);
  if (tab === "standings") return renderStandings(el);
  return renderMatchup(el);
}

// ---------------------------------------------------------------- connect
function renderConnect(el, err = "") {
  const yr = new Date().getMonth() <= 1 ? new Date().getFullYear() - 1 : new Date().getFullYear();
  el.innerHTML = `<div class="grid-2 ff-connect">
    <form class="card" id="ffForm" autocomplete="off">
      <h3>Connect your ESPN league</h3>
      <p>LifeTracker reads your league the same way the ESPN site does. It only reads: it can't set lineups or make moves.</p>
      <label class="ff-field"><span>League ID or league URL</span>
        <input class="input" name="league_id" required placeholder="e.g. 1234567 or https://fantasy.espn.com/football/league?leagueId=…" value="${H.esc(conn?.league_id || "")}"></label>
      <label class="ff-field"><span>Season</span><input class="input" name="season" type="number" min="2018" max="${yr + 1}" value="${H.esc(conn?.season || yr)}"></label>
      <details class="ff-private" ${conn?.has_cookies ? "" : "open"}><summary>Private league? Add your ESPN cookies</summary>
        <label class="ff-field"><span>espn_s2</span><input class="input" name="espn_s2" placeholder="${conn?.has_cookies ? "Saved. Paste a new one to replace it." : "A long value starting with AE…"}"></label>
        <label class="ff-field"><span>SWID</span><input class="input" name="swid" placeholder="${conn?.has_cookies ? "Saved" : "{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}"}"></label>
      </details>
      ${err ? `<p class="ff-err">${H.esc(err)}</p>` : ""}
      <div class="ff-actions"><button class="btn btn-primary" type="submit">Connect league</button></div>
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
  H.$("ffForm").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true; btn.textContent = "Connecting…";
    try {
      const r = await H.api("/api/fantasy/connect", { method: "PUT", body: JSON.stringify({ ...f, season: +f.season }) });
      conn = r; data = null;
      H.toast(`Connected ${r.league_name || "your league"}`);
      location.hash = r.team_id == null ? "#/sports/fantasy/league" : "#/sports/fantasy/matchup";
      if (location.hash === "#/sports/fantasy/league") loadFantasy("league", el).catch(H.showError);
    } catch (err) { renderConnect(el, err.message); conn = conn || {}; }
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
        <button class="text-btn danger" id="ffDrop">Disconnect</button></div></div>
    <div class="card"><h3>What LifeTracker reads</h3>
      <p>Standings, every team's record and points, this week's scores and projections, and your roster. Other managers' names and rosters are read live from ESPN and aren't saved.</p></div></div>`;
  H.$("ffTeam").onchange = async (e) => {
    if (!e.target.value) return;
    conn = await H.api("/api/fantasy/team", { method: "PUT", body: JSON.stringify({ team_id: +e.target.value }) });
    data.my_team_id = conn.team_id;
    H.toast("Saved your team"); location.hash = "#/sports/fantasy/matchup";
  };
  H.$("ffEdit").onclick = () => renderConnect(el);
  H.$("ffDrop").onclick = async () => {
    if (!confirm("Disconnect this league? Your ESPN cookies are deleted from LifeTracker.")) return;
    conn = await H.api("/api/fantasy/connect", { method: "DELETE" }); data = null; renderConnect(el);
  };
}

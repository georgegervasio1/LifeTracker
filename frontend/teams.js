// Sports → Teams: starred teams with records and next games, a month calendar of every favorite's games
// (times in your time zone, venue, TV), standings, and team search. Schedules download as .ics
// or subscribe in Apple / Google Calendar. Data: ESPN's public feeds through the backend (sports.py).
// sports.js passes its helpers in with initTeams() and calls loadTeams(tab) on #/sports/teams/<tab>.

let H;                                   // { api, $, esc, toast, showError, API_BASE, routeQuery }
let home = null;                         // { leagues, favorites }
let sched = null;                        // { games, teams, errors } for the favorites
const teamLists = {};                    // league -> teams
let calMonth = null, calDay = null, hidden = new Set();
let standLeague = "", findLeague = "", findQuery = "";

export function initTeams(helpers) { H = helpers; }
export async function teamsData() {
  if (!home) home = await H.api("/api/sports");
  if (!sched && home.favorites.length) await refreshSchedule();
  return { favorites: home.favorites, games: sched ? sched.games : [] };
}

const STAR = (on) => `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z" fill="${on ? "currentColor" : "none"}" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
const key = (t) => `${t.league}:${t.id}`;
const isFav = (t) => home.favorites.some((f) => key(f) === key(t));
const leagueName = (k) => (home.leagues.find((l) => l.key === k) || {}).label || k.toUpperCase();
const logo = (t, cls = "sp-logo") => t.logo ? `<img class="${cls}" src="${H.esc(t.logo)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
  : `<span class="${cls} sp-logo-txt">${H.esc((t.abbr || t.short || "?").slice(0, 4))}</span>`;
const icsUrl = (teams, dl) => `${H.API_BASE}/api/sports/calendar.ics?teams=${teams.map(key).join(",")}${dl ? "&download=1" : ""}`;

// ---------- dates (shown in the viewer's time zone)
const dt = (g) => new Date(g.date);
const dayKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const gDay = (g) => g.tbd ? g.date.slice(0, 10) : dayKey(dt(g));
const fTime = (g) => g.tbd ? "Time TBD" : dt(g).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
const fDate = (g, opts = {}) => new Date(gDay(g) + "T12:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", ...opts });
const where = (v) => [v.name, [v.city, v.state].filter(Boolean).join(", ")].filter(Boolean).join(" · ");

// the game from one team's side: opponent, home/away, result
function side(g, t) {
  const home_ = g.home.id === t.id && g.home.league === t.league;
  const me = home_ ? g.home : g.away, op = home_ ? g.away : g.home;
  let res = "";
  if (g.completed && me.score != null && op.score != null)
    res = me.winner === true ? "W" : op.winner === true ? "L" : "T";
  return { me, op, at: g.neutral ? "vs" : home_ ? "vs" : "@", res };
}
const resultHtml = (s, g) => s.res ? `<span class="sp-res sp-res-${s.res}">${s.res}</span> ${s.me.score}–${s.op.score}${/OT|SO|\//.test(g.detail) ? ` <span class="muted">${H.esc(g.detail.replace(/^Final\/?/, ""))}</span>` : ""}`
  : g.state === "in" ? `<span class="sp-live">Live</span> ${H.esc(g.detail)}` : H.esc(fTime(g));

// ---------- load
export async function loadTeams(tab) {
  const root = H.$("sportsArea");
  if (!home) { root.innerHTML = `<p class="empty">Loading…</p>`; home = await H.api("/api/sports"); }
  if (tab === "find") return renderFind();
  if (tab === "standings") return renderStandings();
  const team = H.routeQuery().get("team");
  if (tab === "my" && team) return renderTeam(team);
  if (!sched) { root.innerHTML = `<p class="empty">Loading schedules…</p>`; await refreshSchedule(); }
  if (tab === "schedule") return renderCalendar();
  return renderMyTeams();
}
async function refreshSchedule() {
  sched = await H.api("/api/sports/schedule");
  (sched.errors || []).slice(0, 1).forEach((e) => H.toast(`${e.team}: ${e.error}`));
}
async function saveFavorites(list) {
  const r = await H.api("/api/sports/favorites", { method: "PUT", body: JSON.stringify({ teams: list }) });
  home.favorites = r.favorites;
  sched = null;
}
async function toggleStar(t) {
  const list = isFav(t) ? home.favorites.filter((f) => key(f) !== key(t)) : [...home.favorites, t];
  await saveFavorites(list);
  H.toast(isFav(t) ? `${t.name} added to your teams` : `${t.name} removed`);
}

function calendarButtons(teams, label) {
  if (!teams.length) return "";
  const url = icsUrl(teams);
  return `<div class="sp-cal-btns">
    <a class="btn btn-secondary btn-sm" href="${H.esc(icsUrl(teams, true))}" download>Download ${H.esc(label)} (.ics)</a>
    <a class="btn btn-secondary btn-sm" href="${H.esc(url.replace(/^https?:/, "webcal:"))}">Subscribe in Calendar</a>
    <button class="btn btn-secondary btn-sm" data-copy="${H.esc(url)}">Copy calendar link</button>
  </div>`;
}
function wireCopy(root) {
  root.querySelectorAll("[data-copy]").forEach((b) => b.onclick = async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); H.toast("Link copied. In Google Calendar: Other calendars → From URL."); }
    catch { prompt("Calendar link", b.dataset.copy); }
  });
}

// ---------- My Teams
function nextAndLast(t) {
  const mine = (sched.games || []).filter((g) => g.favorites.includes(key(t)));
  const next = mine.find((g) => !g.completed), last = [...mine].reverse().find((g) => g.completed);
  return { next, last };
}
function renderMyTeams() {
  const root = H.$("sportsArea");
  const favs = home.favorites;
  if (!favs.length) {
    root.innerHTML = `<div class="card empty-state"><h3>Star the teams you follow</h3>
      <p>Their records, next games, venues and TV show up here, on a calendar you can download or subscribe to.</p>
      <a class="btn btn-primary" href="#/sports/teams/find">Find teams</a></div>`;
    return;
  }
  const rec = Object.fromEntries((sched.teams || []).map((t) => [key(t), t]));
  root.innerHTML = `<div class="sp-head"><div><p class="eyebrow">My teams</p><h2 class="sp-h2">${favs.length} team${favs.length === 1 ? "" : "s"}</h2></div>
      ${calendarButtons(favs, "all schedules")}</div>
    <div class="sp-grid">${favs.map((f) => {
      const t = { ...f, ...(rec[key(f)] || {}) }, { next, last } = nextAndLast(f);
      const n = next && side(next, f), l = last && side(last, f);
      return `<article class="card sp-team" style="--team:${H.esc(t.color || "var(--line-strong)")}">
        <div class="sp-team-top">
          <a class="sp-team-id" href="#/sports/teams/my?team=${key(f)}">${logo(t)}<span><span class="sp-team-name">${H.esc(t.name)}</span>
            <span class="sp-team-league">${H.esc(leagueName(f.league))}</span></span></a>
          <button class="sp-star on" data-star="${key(f)}" aria-label="Remove ${H.esc(t.name)} from my teams" title="Unstar">${STAR(true)}</button>
        </div>
        <div class="sp-rec"><span class="sp-rec-num">${H.esc(t.record || "—")}</span><span class="muted">${H.esc(t.standing || (t.record ? "" : "No games yet this season"))}</span></div>
        <dl class="sp-games">
          <div><dt>Next</dt><dd>${n ? `${n.at} ${H.esc(n.op.short || n.op.name)} · ${fDate(next)}, ${H.esc(fTime(next))}
            <span class="muted sp-where">${H.esc(where(next.venue))}${next.tv ? ` · ${H.esc(next.tv)}` : ""}</span>` : `<span class="muted">Nothing scheduled</span>`}</dd></div>
          <div><dt>Last</dt><dd>${l ? `${resultHtml(l, last)} ${l.at} ${H.esc(l.op.short || l.op.name)} <span class="muted">${fDate(last)}</span>` : `<span class="muted">—</span>`}</dd></div>
        </dl>
        <a class="sp-more" href="#/sports/teams/my?team=${key(f)}">Full schedule</a>
      </article>`;
    }).join("")}</div>
    <p class="sp-note">Times are in your time zone. Schedules and scores come from ESPN.</p>`;
  root.querySelectorAll("[data-star]").forEach((b) => b.onclick = async () => {
    const f = favs.find((x) => key(x) === b.dataset.star);
    await toggleStar(f).catch(H.showError); await loadTeams("my").catch(H.showError);
  });
  wireCopy(root);
}

// ---------- one team
async function renderTeam(k) {
  const root = H.$("sportsArea");
  const [league, id] = k.split(":");
  root.innerHTML = `<p class="empty">Loading schedule…</p>`;
  const r = await H.api(`/api/sports/team/${encodeURIComponent(league)}/${encodeURIComponent(id)}`);
  const t = { ...r.team, league };
  const games = r.games;
  const rows = games.map((g) => {
    const s = side(g, t);
    return `<tr class="${g.completed ? "sp-past" : ""}">
      <td class="sp-date">${fDate(g)}${g.week ? `<span class="muted sp-sub">${H.esc(g.week)}</span>` : ""}</td>
      <td><span class="sp-opp">${s.at} ${logo(s.op, "sp-logo-sm")}${H.esc(s.op.name)}</span>${g.season_type && !/regular/i.test(g.season_type) ? `<span class="muted sp-sub">${H.esc(g.season_type)}</span>` : ""}</td>
      <td class="sp-when">${resultHtml(s, g)}</td>
      <td class="sp-loc">${H.esc(where(g.venue)) || "—"}</td>
      <td class="sp-tv">${H.esc(g.tv) || ""}</td></tr>`;
  }).join("");
  root.innerHTML = `<a class="sp-back" href="#/sports/teams/my">My teams</a>
    <div class="sp-head sp-team-head" style="--team:${H.esc(t.color || "var(--line-strong)")}">
      <div class="sp-team-id">${logo(t, "sp-logo-lg")}<div><p class="eyebrow">${H.esc(leagueName(league))}${r.season ? ` · ${H.esc(String(r.season))}` : ""}</p>
        <h2 class="sp-h2">${H.esc(t.name)}</h2>
        <p class="muted">${H.esc([t.record, t.standing].filter(Boolean).join(" · ") || "No record yet")}</p></div></div>
      <button class="btn btn-secondary btn-sm sp-star-btn" id="spStarTeam">${STAR(isFav(t))}<span>${isFav(t) ? "In my teams" : "Add to my teams"}</span></button>
    </div>
    ${calendarButtons([t], "schedule")}
    <div class="card card-flush sp-table-card">${games.length ? `<div class="table-scroll"><table class="table sp-sched">
      <thead><tr><th>Date</th><th>Opponent</th><th>Time / result</th><th>Location</th><th>TV</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : `<p class="empty">No games on ESPN's schedule for this season yet.</p>`}</div>
    <p class="sp-note">Times are in your time zone. "Time TBD" means the league hasn't set it yet.</p>`;
  H.$("spStarTeam").onclick = async () => { await toggleStar(t).catch(H.showError); renderTeam(k).catch(H.showError); };
  wireCopy(root);
}

// ---------- Schedule (month calendar)
function renderCalendar() {
  const root = H.$("sportsArea");
  const favs = home.favorites;
  if (!favs.length) { renderMyTeams(); return; }
  const games = (sched.games || []).filter((g) => g.favorites.some((f) => !hidden.has(f)));
  const color = Object.fromEntries(favs.map((f) => [key(f), f.color || "var(--accent)"]));
  if (!calMonth) {                           // open on the month of the next game (or this month)
    const nx = games.find((g) => !g.completed);
    const d = nx ? new Date(gDay(nx) + "T12:00") : new Date();
    calMonth = new Date(d.getFullYear(), d.getMonth(), 1);
  }
  const y = calMonth.getFullYear(), m = calMonth.getMonth();
  const byDay = {};
  games.forEach((g) => (byDay[gDay(g)] ||= []).push(g));
  const first = new Date(y, m, 1).getDay(), days = new Date(y, m + 1, 0).getDate();
  const today = dayKey(new Date());
  const label = (g) => {
    const f = favs.find((x) => g.favorites.includes(key(x)));
    const s = side(g, f);
    return { f, s, text: `${s.at} ${s.op.abbr || s.op.short}` };
  };
  let cells = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d) => `<div class="cal-dow">${d}</div>`).join("");
  for (let i = 0; i < first; i++) cells += `<div class="cal-cell cal-pad"></div>`;
  for (let d = 1; d <= days; d++) {
    const k = `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const gs = byDay[k] || [];
    cells += `<button class="cal-cell sp-cell${k === today ? " today" : ""}${k === calDay ? " sel" : ""}" data-day="${k}" ${gs.length ? "" : "tabindex=-1"}>
      <span class="cal-num">${d}</span>
      <span class="cal-workouts">${gs.slice(0, 3).map((g) => { const L = label(g);
        return `<span class="sp-chip" style="--team:${H.esc(color[key(L.f)])}"><b>${H.esc(L.f.abbr || "")}</b> ${H.esc(L.text)}<span class="sp-chip-time"> ${H.esc(g.tbd ? "TBD" : fTime(g))}</span></span>`; }).join("")}
        ${gs.length > 3 ? `<span class="cal-more">+${gs.length - 3} more</span>` : ""}</span>
      <span class="sp-dots">${gs.map((g) => `<i style="background:${H.esc(color[key(label(g).f)])}"></i>`).join("")}</span>
    </button>`;
  }
  const monthKey = `${y}-${String(m + 1).padStart(2, "0")}`;
  const list = calDay ? (byDay[calDay] || []) : games.filter((g) => gDay(g).startsWith(monthKey));
  root.innerHTML = `<div class="sp-head"><div><p class="eyebrow">Schedule</p><h2 class="sp-h2">${calMonth.toLocaleDateString("en-US", { month: "long", year: "numeric" })}</h2></div>
      <div class="cal-nav"><button class="btn btn-secondary btn-sm" id="spPrev" aria-label="Previous month">‹</button>
        <button class="btn btn-secondary btn-sm" id="spToday">Today</button>
        <button class="btn btn-secondary btn-sm" id="spNext" aria-label="Next month">›</button></div></div>
    <div class="sp-filters">${favs.map((f) => `<button class="sp-filter${hidden.has(key(f)) ? "" : " on"}" data-team="${key(f)}" style="--team:${H.esc(f.color || "var(--accent)")}">
      <i></i>${H.esc(f.short || f.name)}</button>`).join("")}</div>
    <div class="calendar sp-calendar">${cells}</div>
    <div class="sp-list"><div class="sp-list-head"><h3>${calDay ? fDate({ date: calDay, tbd: true }, { weekday: "long" }) : "Games this month"}</h3>
      ${calDay ? `<button class="text-btn" id="spAll">Show the whole month</button>` : ""}</div>
      ${list.length ? list.map((g) => gameRow(g, favs, color)).join("") : `<p class="empty">${calDay ? "No games this day." : "No games this month."}</p>`}</div>
    ${calendarButtons(favs.filter((f) => !hidden.has(key(f))), "these teams")}
    <p class="sp-note">Times are in your time zone.</p>`;
  const go = (dm) => { calMonth = dm == null ? new Date(new Date().getFullYear(), new Date().getMonth(), 1) : new Date(y, m + dm, 1); calDay = null; renderCalendar(); };
  H.$("spPrev").onclick = () => go(-1); H.$("spNext").onclick = () => go(1); H.$("spToday").onclick = () => go(null);
  if (H.$("spAll")) H.$("spAll").onclick = () => { calDay = null; renderCalendar(); };
  root.querySelectorAll("[data-day]").forEach((c) => c.onclick = () => { calDay = calDay === c.dataset.day ? null : c.dataset.day; renderCalendar(); });
  root.querySelectorAll("[data-team]").forEach((b) => b.onclick = () => {
    hidden.has(b.dataset.team) ? hidden.delete(b.dataset.team) : hidden.add(b.dataset.team); renderCalendar(); });
  wireCopy(root);
}
function gameRow(g, favs, color) {
  const f = favs.find((x) => g.favorites.includes(key(x)));
  const s = side(g, f);
  const both = g.favorites.length > 1;
  return `<div class="sp-row" style="--team:${H.esc(color[key(f)])}">
    <div class="sp-row-when"><span>${fDate(g)}</span><span class="muted">${g.completed ? "Final" : H.esc(fTime(g))}</span></div>
    <div class="sp-row-main"><span class="sp-row-teams">${logo(g.away, "sp-logo-sm")}${H.esc(g.away.name)}
        ${g.completed && g.away.score != null ? `<b>${g.away.score}</b>` : ""} <span class="muted">${g.neutral ? "vs" : "at"}</span>
        ${logo(g.home, "sp-logo-sm")}${H.esc(g.home.name)} ${g.completed && g.home.score != null ? `<b>${g.home.score}</b>` : ""}</span>
      <span class="muted sp-where">${H.esc(where(g.venue)) || "Venue not listed"}${g.tv ? ` · ${H.esc(g.tv)}` : ""}${both ? " · two of your teams" : ""}</span></div>
    <div class="sp-row-res">${s.res ? `<span class="sp-res sp-res-${s.res}">${s.res}</span>` : g.state === "in" ? `<span class="sp-live">Live</span>` : ""}</div>
  </div>`;
}

// ---------- Standings
async function renderStandings() {
  const root = H.$("sportsArea");
  const favLeagues = [...new Set(home.favorites.map((f) => f.league))];
  standLeague ||= favLeagues[0] || "nfl";
  root.innerHTML = `<div class="sp-head"><div><p class="eyebrow">Standings</p><h2 class="sp-h2">${H.esc(leagueName(standLeague))}</h2></div>
    ${leagueSelect("spStandLeague", standLeague)}</div><p class="empty">Loading standings…</p>`;
  H.$("spStandLeague").onchange = (e) => { standLeague = e.target.value; renderStandings().catch(H.showError); };
  let r;
  try { r = await H.api(`/api/sports/standings/${standLeague}`); }
  catch (e) { root.querySelector(".empty").textContent = e.message; return; }
  const favIds = new Set(home.favorites.filter((f) => f.league === standLeague).map((f) => f.id));
  const html = r.groups.length ? r.groups.map((grp) => `<div class="card card-flush sp-table-card"><h3 class="sp-group">${H.esc(grp.name)}</h3>
    <div class="table-scroll"><table class="table sp-stand"><thead><tr><th>Team</th>${r.columns.map((c) => `<th class="num">${c}</th>`).join("")}</tr></thead>
    <tbody>${grp.teams.map((t, i) => `<tr class="${favIds.has(t.id) ? "sp-fav" : ""}"><td><a class="sp-opp" href="#/sports/teams/my?team=${standLeague}:${t.id}">
      <span class="sp-rank">${t.seed || i + 1}</span>${logo(t, "sp-logo-sm")}<span class="sp-stand-name">${H.esc(t.name)}</span><span class="sp-stand-abbr">${H.esc(t.abbr)}</span>
      ${favIds.has(t.id) ? `<span class="sp-star-inline" aria-label="One of your teams">${STAR(true)}</span>` : ""}</a></td>
      ${r.columns.map((c) => `<td class="num">${H.esc(t.stats[c] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div></div>`).join("")
    : `<p class="empty">ESPN has no standings for this league right now.</p>`;
  root.querySelector(".empty").outerHTML = html + `<p class="sp-note">${r.season ? `${H.esc(r.season)} season. ` : ""}Your teams are starred. Source: ESPN.</p>`;
}
function leagueSelect(id, val) {
  return `<select class="select" id="${id}" aria-label="League">${home.leagues.map((l) =>
    `<option value="${l.key}"${l.key === val ? " selected" : ""}>${H.esc(l.label)}</option>`).join("")}</select>`;
}

// ---------- Find teams
async function renderFind() {
  const root = H.$("sportsArea");
  findLeague ||= home.favorites[0]?.league || "nfl";
  root.innerHTML = `<div class="sp-head"><div><p class="eyebrow">Find teams</p><h2 class="sp-h2">Star the teams you follow</h2></div></div>
    <div class="sp-find-bar">${leagueSelect("spFindLeague", findLeague)}
      <input class="input" id="spFindQ" type="search" placeholder="Search teams" value="${H.esc(findQuery)}" autocomplete="off"></div>
    <div id="spFindList"><p class="empty">Loading teams…</p></div>`;
  H.$("spFindLeague").onchange = (e) => { findLeague = e.target.value; renderFind().catch(H.showError); };
  H.$("spFindQ").oninput = (e) => { findQuery = e.target.value; drawFind(); };
  try { teamLists[findLeague] ||= (await H.api(`/api/sports/teams?league=${findLeague}`)).teams; }
  catch (e) { H.$("spFindList").innerHTML = `<p class="empty">${H.esc(e.message)}</p>`; return; }
  drawFind();
}
function drawFind() {
  const q = findQuery.trim().toLowerCase();
  const all = teamLists[findLeague] || [];
  const list = all.filter((t) => !q || `${t.name} ${t.abbr}`.toLowerCase().includes(q));
  H.$("spFindList").innerHTML = list.length ? `<div class="sp-find">${list.slice(0, 200).map((t) => `
    <div class="sp-find-row"><a class="sp-opp" href="#/sports/teams/my?team=${key(t)}">${logo(t, "sp-logo-sm")}${H.esc(t.name)}</a>
      <button class="sp-star${isFav(t) ? " on" : ""}" data-k="${key(t)}" aria-pressed="${isFav(t)}" aria-label="${isFav(t) ? "Unstar" : "Star"} ${H.esc(t.name)}">${STAR(isFav(t))}</button></div>`).join("")}</div>
    ${list.length > 200 ? `<p class="sp-note">Showing 200 of ${list.length}. Search to narrow it down.</p>` : ""}`
    : `<p class="empty">No teams match.</p>`;
  H.$("spFindList").querySelectorAll("[data-k]").forEach((b) => b.onclick = async () => {
    const t = all.find((x) => key(x) === b.dataset.k);
    b.disabled = true;
    await toggleStar(t).catch(H.showError);
    drawFind();
  });
}

// Sports: a dashboard of three photo cards (Teams, Fantasy Football, Betting), like Health and Travel.
// Teams lives in teams.js, Fantasy Football in fantasy.js; Betting is in development (plan shown on its page).
// app.js calls renderSports(parts) for #/sports/<area>/<tab>.
import { initTeams, loadTeams, teamsData } from "./teams.js";
import { initFantasy, loadFantasy, fantasyCard } from "./fantasy.js";

let H;                        // { api, $, esc, toast, showError, API_BASE, routeQuery, isDemo }

const UNSPLASH = (id) => `https://images.unsplash.com/${id}?auto=format&fit=crop&w=1600&q=70`;
const AREAS = {
  teams:   { title: "Teams", tabs: [["my", "My Teams"], ["schedule", "Schedule"], ["standings", "Standings"], ["find", "Find Teams"]],
             img: "photo-1676746424139-77f8bd8922a8", credit: ["Chris Kursikowski", "a-soccer-field-at-night-with-the-lights-on-5IS7UghgoMA"],
             sub: "Your teams' schedules on one calendar, records and standings" },
  fantasy: { title: "Fantasy Football", tabs: [["matchup", "Matchup"], ["roster", "Roster"], ["standings", "Standings"], ["league", "League"]],
             img: "photo-1761258772385-66e51635cf84", credit: ["Tim Mossholder", "football-players-in-uniform-on-the-field-aZT2gACJuX0"],
             sub: "Your ESPN league: this week's matchup, your roster and the standings" },
  betting: { title: "Betting", tabs: [["overview", "Overview"]],
             img: "photo-1696542095242-53305aaa6d9c", credit: ["Myron Mott", "a-stadium-filled-with-lots-of-people-watching-a-football-game-lrTSZaprSZA"],
             sub: "Wins and losses across sportsbooks. In development." },
};
// Links from before the dashboard (#/sports/schedule, #/sports/find, ...) still work
const LEGACY = { schedule: ["teams", "schedule"], standings: ["teams", "standings"], find: ["teams", "find"] };

export function initSports(helpers) {
  H = helpers;
  initTeams(H);
  initFantasy(H);
}

function parse(parts) {
  let [area, tab] = parts;
  if (LEGACY[area]) [area, tab] = LEGACY[area];
  if (!AREAS[area]) return { area: "home" };
  const t = AREAS[area].tabs.find(([k]) => k === tab) || AREAS[area].tabs[0];
  return { area, tab: t[0] };
}

export async function renderSports(parts) {
  const r = parse(parts), A = AREAS[r.area];
  H.$("subnav").classList.toggle("hidden", !A);
  if (A) {
    H.$("subnavTitle").innerHTML = `<a class="subnav-crumb" href="#/sports">Sports</a>${H.esc(A.title)}`;
    H.$("subnavTabs").innerHTML = A.tabs.length > 1 ? A.tabs.map(([k, label]) =>
      `<a href="#/sports/${r.area}/${k}" class="${k === r.tab ? "active" : ""}">${label}</a>`).join("") : "";
    H.$("sectionControls").classList.add("hidden");
  }
  document.title = `${A ? A.title : "Sports"} · LifeTracker`;
  const root = H.$("sportsRoot");
  if (!A) return renderHome(root);
  if (root.dataset.area !== r.area) {
    root.innerHTML = `<div class="hero-wrap tr-hero-wrap"><div class="health-hero tr-hero">
        <div class="hero-media"><img src="${UNSPLASH(A.img)}" alt="" /></div>
        <div class="hero-text"><a class="hero-back" href="#/sports">Sports</a><h1>${H.esc(A.title)}</h1><p>${H.esc(A.sub)}</p></div>
        <a class="photo-credit" target="_blank" rel="noopener" href="https://unsplash.com/photos/${A.credit[1]}">Photo: ${H.esc(A.credit[0])} / Unsplash</a>
      </div></div><div id="sportsArea"></div>`;
    root.dataset.area = r.area;
  }
  if (r.area === "teams") return loadTeams(r.tab);
  if (r.area === "fantasy") return loadFantasy(r.tab, H.$("sportsArea"));
  return renderBetting(H.$("sportsArea"));
}

// ---------------------------------------------------------------- home: three photo cards
async function renderHome(root) {
  root.dataset.area = "home";
  const card = (k, stat) => {
    const A = AREAS[k];
    return `<a class="hd-card tr-card" data-area="${k}" href="#/sports/${k}">
      <span class="hd-media"><img src="${UNSPLASH(A.img)}" alt="" loading="eager" /></span><span class="hd-shade"></span>
      <span class="hd-body"><span class="hd-title">${A.title}</span>
        <span class="hd-stat">${stat ? `<b>${H.esc(stat.big)}</b>${stat.unit ? `<span>${H.esc(stat.unit)}</span>` : ""}` : `<b class="hd-skel"></b>`}</span>
        <span class="hd-line">${stat ? H.esc(stat.line) : "&nbsp;"}</span></span>
      <span class="hd-go" aria-hidden="true">›</span></a>`;
  };
  const draw = (S = {}) => {
    root.innerHTML = `<div class="hd-head"><div><p class="eyebrow">Sports</p><h1 class="title">Game day</h1></div></div>
      <div class="hd-grid tr-grid">${card("teams", S.teams)}${card("fantasy", S.fantasy)}${card("betting", S.betting)}</div>
      <p class="hint tr-credits">Photos: ${Object.values(AREAS).map((A) => `<a href="https://unsplash.com/photos/${A.credit[1]}" target="_blank" rel="noopener">${H.esc(A.credit[0])}</a>`).join(", ")} / Unsplash</p>`;
  };
  draw();
  const S = { betting: { big: "Soon", unit: "", line: "In development" } };
  await Promise.all([
    teamsData().then((T) => {
      const next = T.games.find((g) => !g.completed);
      if (!T.favorites.length) S.teams = { big: "—", line: "Star the teams you follow" };
      else if (!next) S.teams = { big: String(T.favorites.length), unit: T.favorites.length === 1 ? "team" : "teams", line: "No games scheduled" };
      else {
        const d = new Date(next.date), days = Math.round((new Date(d.toDateString()) - new Date(new Date().toDateString())) / 864e5);
        S.teams = { big: next.state === "in" ? "Live" : days <= 0 ? "Today" : String(days), unit: next.state === "in" || days <= 0 ? "" : days === 1 ? "day" : "days",
                    line: `${next.away.short || next.away.name} at ${next.home.short || next.home.name}` };
      }
    }).catch(() => { S.teams = { big: "—", line: "Open to see your teams" }; }),
    fantasyCard().then((F) => { S.fantasy = F; }).catch(() => { S.fantasy = { big: "—", line: "Connect your ESPN league" }; }),
  ]);
  if (H.$("sportsRoot").dataset.area === "home") draw(S);
}

// ---------------------------------------------------------------- Betting (in development)
function renderBetting(el) {
  el.innerHTML = `<div class="card bt-dev">
      <span class="pill pill-plain">In development</span>
      <h2 class="sp-h2">A betting tracker for every sportsbook</h2>
      <p>The goal: one running total of what you've put in and taken out across DraftKings, FanDuel and any other book,
        whether it's going up or down, and a financial audit that ties it to your bank account.</p>
    </div>
    <div class="grid-2 bt-plan">
      <div class="card"><p class="eyebrow">Step 1</p><h3>Money in and out, from your bank</h3>
        <p>Deposits to and withdrawals from sportsbooks show up in your bank transactions. Matching them would give a true net +/- by book and by month, with nothing to sign in to.</p></div>
      <div class="card"><p class="eyebrow">Step 2</p><h3>Every bet, from your bet history</h3>
        <p>Import each book's bet history (wager, odds, result) to see your record by sport and bet type, and how the balance moved over time.</p></div>
      <div class="card"><p class="eyebrow">Step 3</p><h3>Linked accounts</h3>
        <p>DraftKings and FanDuel have no public API, and LifeTracker shouldn't hold your sportsbook passwords. Live linking would go through a licensed aggregator that connects books the way Plaid connects banks.</p></div>
      <div class="card"><p class="eyebrow">Step 4</p><h3>Audit and limits</h3>
        <p>Year-to-date net for taxes (winnings are taxable income), a monthly limit you set, and a flag when deposits run ahead of it.</p></div>
    </div>
    <p class="sp-note">Nothing here is connected yet.</p>`;
}

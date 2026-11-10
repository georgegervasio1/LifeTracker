// Services each person uses: the intro page after sign-up, Settings → Your services, and the in-development
// add-on pages (Golf, Movies, Recipes, Chess). The list itself comes from the server (backend/services.py).
let H;                        // { api, $, esc, toast, onSaved }
export function initServices(helpers) { H = helpers; }

export const ADDONS = ["golf", "movies", "recipes", "chess"];

// The checkbox grid shared by the intro page and Settings. Returns () => picked ids.
function picker(root, catalog, picked) {
  const on = new Set(picked);
  const card = (s) => `<label class="svc-card">
      <input type="checkbox" value="${s.id}" ${on.has(s.id) ? "checked" : ""} />
      <span class="svc-text"><span class="svc-name">${H.esc(s.title)}${s.status === "in_development" ? '<span class="pill pill-plain">In development</span>' : ""}</span>
        <span class="svc-blurb">${H.esc(s.blurb)}</span></span>
      <span class="svc-check" aria-hidden="true"></span></label>`;
  const core = catalog.filter((s) => s.kind === "core"), addons = catalog.filter((s) => s.kind === "addon");
  root.innerHTML = `<div class="svc-group"><h2 class="svc-h">Services</h2><div class="svc-grid">${core.map(card).join("")}</div></div>
    <div class="svc-group"><h2 class="svc-h">Add-ons</h2>
      <p class="hint">These are being built. Pick one to add its tab now and see what's planned; it fills in as each one ships.</p>
      <div class="svc-grid">${addons.map(card).join("")}</div></div>`;
  return () => [...root.querySelectorAll("input:checked")].map((i) => i.value);
}

// First sign-in: a full page before the app, like the sign-in page.
export function showIntro(svc, done) {
  const { $ } = H;
  const read = picker($("introPicker"), svc.catalog, svc.picked);
  const btn = $("introContinue"), sync = () => { const n = read().length; btn.disabled = !n; btn.textContent = n ? `Continue with ${n}` : "Pick at least one"; };
  $("introPicker").onchange = sync;
  sync();
  btn.onclick = async () => {
    btn.disabled = true;
    try { done(await H.api("/api/services", { method: "PUT", body: JSON.stringify({ picked: read() }) })); }
    catch (e) { H.toast(e.message); sync(); }
  };
}

// Settings (#/settings)
export function loadSettings(svc) {
  const { $ } = H, root = $("settingsRoot");
  root.innerHTML = `<div class="settings-head"><h1 class="title">Settings</h1></div>
    <div class="card svc-settings"><h2 class="svc-title">Your services</h2>
      <p class="hint">Choose the sections you see. Turning one off only hides it: nothing you've saved there is deleted.</p>
      <div id="setPicker"></div>
      <div class="settings-actions"><button class="btn btn-primary btn-sm" id="setSave">Save</button></div></div>`;
  const read = picker($("setPicker"), svc.catalog, svc.picked);
  $("setSave").onclick = async () => {
    const picked = read();
    if (!picked.length) return H.toast("Pick at least one service");
    try { H.onSaved(await H.api("/api/services", { method: "PUT", body: JSON.stringify({ picked }) })); H.toast("Saved"); }
    catch (e) { H.toast(e.message); }
  };
}

// ---------------------------------------------------------------- add-ons (in development)
// What each one will do and where its data can come from. Nothing here is fetched yet.
const PLANS = {
  golf: {
    title: "Golf", h: "Your handicap and every round, in one place",
    intro: "A golf log that keeps your Handicap Index current, with your rounds from 18Birdies and your official index from GHIN, the handicap service CDGA and the other US golf associations use.",
    steps: [
      ["Log a round, get an index", "Enter the course, tees, course rating, slope and score. The index is worked out the World Handicap System way: each round's score differential is 113 ÷ slope × (adjusted score − course rating), and the index is the average of your best 8 of the last 20. Fewer than 20 rounds use the WHS table."],
      ["Rounds from 18Birdies", "18Birdies has no public API, so rounds would come in from a scorecard you share or export from the app, rather than a live link."],
      ["Your official GHIN index", "GHIN's API is open only to approved partners, so the official index would be entered by hand (or read from GHIN if CDGA grants access) and shown next to the computed one."],
    ],
    data: [["Handicap math", "Ready", "The WHS formula only needs your scores and each course's rating and slope."],
           ["18Birdies", "Workaround", "No public API."], ["GHIN / CDGA", "Workaround", "Partner-only API."]],
  },
  movies: {
    title: "Movies", h: "Every film you've watched, from Letterboxd",
    intro: "Your Letterboxd diary in LifeTracker: what you watched and when, your ratings, and how your year in film is going.",
    steps: [
      ["Recent diary from your profile", "Every public Letterboxd profile has an RSS feed of recent diary entries (film, date watched, rating, review). Entering your username would be enough to keep it current."],
      ["Your full history", "Letterboxd's data export (Settings → Data) is a zip of CSV files: diary, ratings, reviews and watchlist. It would be read in your browser, the same way the Strava export is."],
      ["Stats and goals", "Films per month, your rating spread, rewatches, and a link to Goals (for example, a number of films this year)."],
    ],
    data: [["Letterboxd RSS", "Ready", "Public feed per profile, no key."], ["Letterboxd export", "Ready", "CSV files you download."],
           ["Letterboxd API", "Not used", "Open only to approved apps."]],
  },
  recipes: {
    title: "Recipes", h: "Recipes you cook, tied to your food diary",
    intro: "A cookbook for the recipes you actually make, that logs a serving to Health → Food in one tap.",
    steps: [
      ["Save a recipe from a link", "Most recipe sites publish the recipe as structured data in the page (ingredients, steps, times, servings, often nutrition), so pasting a link could fill it in."],
      ["Your own recipes", "Write your own, with tags (breakfast, meal prep, high protein) and notes from each time you make it."],
      ["Log it to Food", "Log a serving to the food diary with its nutrition, from the recipe or worked out from the ingredients with USDA FoodData Central, which Food already uses."],
    ],
    data: [["Recipe pages", "Ready", "Most sites include schema.org Recipe data."], ["USDA FoodData Central", "Ready", "Already used by Food."]],
  },
  chess: {
    title: "Chess", h: "Your Chess.com ratings over time",
    intro: "Your current and best ratings in every time control, how they've moved, and your record.",
    steps: [
      ["Ratings from your username", "Chess.com's public data API gives current, best and record for rapid, blitz, bullet and daily, plus puzzles, with no sign-in or key."],
      ["Rating history and record", "The same API lists your games month by month, so the history chart and your results by time control, color and opening can be built from it."],
      ["Goals", "Link a rating to Goals, for example a target rapid rating by a date you pick."],
    ],
    data: [["Chess.com public API", "Ready", "Public, no key; ratings, stats and monthly game archives."]],
  },
};

export function addonTitle(id) { return PLANS[id]?.title || ""; }

export function renderAddon(id) {
  const { $, esc } = H, P = PLANS[id];
  $("subnav").classList.add("hidden");
  document.title = `${P.title} · LifeTracker`;
  $("addonRoot").innerHTML = `<div class="addon-head"><p class="eyebrow">${esc(P.title)}</p><h1 class="title">${esc(P.h)}</h1></div>
    <div class="card bt-dev"><span class="pill pill-plain">In development</span><p>${esc(P.intro)}</p></div>
    <div class="grid-2 bt-plan addon-plan">${P.steps.map(([h, p], i) => `<div class="card"><p class="eyebrow">Step ${i + 1}</p><h3>${esc(h)}</h3><p>${esc(p)}</p></div>`).join("")}</div>
    <div class="card card-flush addon-data"><div class="card-head padded"><h3>Where the data comes from</h3></div>
      ${P.data.map(([n, s, note]) => `<div class="addon-row"><span>${esc(n)}</span><span class="pill ${s === "Ready" ? "pill-met" : "pill-plain"}">${esc(s)}</span><span class="muted">${esc(note)}</span></div>`).join("")}</div>
    <p class="hint">Turn this off any time in <a href="#/settings">Settings</a>.</p>`;
}

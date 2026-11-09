// Travel: a dashboard of three photo cards (Apartments, Commute, Trips), like Health. Each area has its own tabs.
// Commute (Capital Bikeshare + Metro) lives here; Apartments in apartments.js, Trips in trips.js.
// app.js calls renderTravel(parts) for #/travel/<area>/<tab>.
import { initApartments, loadApartments, apartmentsData } from "./apartments.js";
import { initTrips, loadTrips, tripsData } from "./trips.js";

let H;                        // { api, $, esc, toast, showError, usd, usd0, tileLayer, gmailToken, isDemo, authFetch, viewSwap }
let bike = null, rides = null, metro = null;
let refreshTimer = null, rideMap = null;

const UNSPLASH = (id) => `https://images.unsplash.com/${id}?auto=format&fit=crop&w=1600&q=70`;
const AREAS = {
  apartments: { title: "Apartments", tabs: [["listings", "Listings"], ["saved", "Saved"], ["map", "Map"], ["emails", "Weekly emails"]],
                img: "photo-1592252566776-c680fa586020", credit: ["Benjamin R.", "body-of-water-near-green-trees-and-high-rise-buildings-during-daytime-IVTis_xBsUQ"],
                sub: "Listings from your weekly search, saved spots and the map" },
  commute:    { title: "Commute", tabs: [["overview", "Overview"], ["bike", "Bikeshare"], ["metro", "Metro"]],
                img: "photo-1569328922596-4c04460c91a2", credit: ["Andy Feliciotti", "people-biking-on-road-and-different-vehicles-viewing-united-states-capitol-during-daytime-screenshot-isg8AL7-6uk"],
                sub: "Capital Bikeshare and Metro, and your transit benefit" },
  trips:      { title: "Trips", tabs: [["trips", "Trips"]],
                img: "photo-1527605158555-853f200063e9", credit: ["William Bayreuther", "plane-wing-through-glass-window-C0KdBbibUYo"],
                sub: "Vacations and travel: dates, places, flights and stays" },
};
const LEGACY = { bike: ["commute", "bike"], apartments: ["apartments", "listings"], metro: ["commute", "metro"] };

export function initTravel(helpers) {
  H = helpers;
  initApartments(H);
  initTrips(H);
}

export function parseTravel(parts) {
  let [area, tab] = parts;
  if (!AREAS[area] && LEGACY[area]) [area, tab] = LEGACY[area];
  if (!AREAS[area]) return { area: "home" };
  const t = AREAS[area].tabs.find(([k]) => k === tab) || AREAS[area].tabs[0];
  return { area, tab: t[0] };
}

export async function renderTravel(parts) {
  clearInterval(refreshTimer);
  const r = parseTravel(parts), A = AREAS[r.area];
  const sub = H.$("subnav");
  sub.classList.toggle("hidden", !A);
  if (A) {
    H.$("subnavTitle").innerHTML = `<a class="subnav-crumb" href="#/travel">Travel</a>${H.esc(A.title)}`;
    H.$("subnavTabs").innerHTML = A.tabs.length > 1 ? A.tabs.map(([k, label]) =>
      `<a href="#/travel/${r.area}/${k}" class="${k === r.tab ? "active" : ""}">${label}</a>`).join("") : "";
    H.$("sectionControls").classList.add("hidden");
  }
  document.title = `${A ? A.title : "Travel"} · LifeTracker`;
  const root = H.$("travelRoot");
  if (!A) return renderHome(root);
  if (root.dataset.area !== r.area) {
    root.innerHTML = `<div class="hero-wrap tr-hero-wrap"><div class="health-hero tr-hero">
        <div class="hero-media"><img src="${UNSPLASH(A.img)}" alt="" /></div>
        <div class="hero-text"><a class="hero-back" href="#/travel">Travel</a><h1>${H.esc(A.title)}</h1><p>${H.esc(A.sub)}</p></div>
        <a class="photo-credit" target="_blank" rel="noopener" href="https://unsplash.com/photos/${A.credit[1]}">Photo: ${H.esc(A.credit[0])} / Unsplash</a>
      </div></div><div id="travelArea"></div>`;
    root.dataset.area = r.area;
  }
  const area = H.$("travelArea");
  if (r.area === "apartments") return loadApartments(r.tab, area);
  if (r.area === "trips") return loadTrips(area);
  if (r.tab === "metro") return loadMetro(area);
  if (r.tab === "overview") return loadOverview(area);
  area.innerHTML = `<div id="travelBike"></div>`;
  await loadBike();
  refreshTimer = setInterval(() => {
    if (document.visibilityState === "visible" && /^#\/travel\/commute\/bike/.test(location.hash)) loadBike(true).catch(() => {});
    else if (!location.hash.startsWith("#/travel")) clearInterval(refreshTimer);
  }, 60000);
}

// ---------------------------------------------------------------- home: three photo cards
async function renderHome(root) {
  root.dataset.area = "home";
  const card = (k, stat) => {
    const A = AREAS[k];
    return `<a class="hd-card tr-card" data-area="${k}" href="#/travel/${k}">
      <span class="hd-media"><img src="${UNSPLASH(A.img)}" alt="" loading="eager" /></span><span class="hd-shade"></span>
      <span class="hd-body"><span class="hd-title">${A.title}</span>
        <span class="hd-stat">${stat ? `<b>${H.esc(stat.big)}</b>${stat.unit ? `<span>${H.esc(stat.unit)}</span>` : ""}` : `<b class="hd-skel"></b>`}</span>
        <span class="hd-line">${stat ? H.esc(stat.line) : "&nbsp;"}</span></span>
      <span class="hd-go" aria-hidden="true">›</span></a>`;
  };
  const draw = (S = {}) => {
    root.innerHTML = `<div class="wrap"><div class="hd-head"><div><p class="eyebrow">Travel</p><h1 class="title">Where you're going</h1></div></div>
      <div class="hd-grid tr-grid">${card("apartments", S.apartments)}${card("commute", S.commute)}${card("trips", S.trips)}</div>
      <p class="hint tr-credits">Photos: ${Object.values(AREAS).map((A) => `<a href="https://unsplash.com/photos/${A.credit[1]}" target="_blank" rel="noopener">${H.esc(A.credit[0])}</a>`).join(", ")} / Unsplash</p></div>`;
  };
  draw();
  const S = {};
  await Promise.all([
    apartmentsData().then((A) => { const d = A.digests[0];
      S.apartments = { big: String(A.wishlist.length), unit: "saved", line: d ? `Latest email ${fd(d.date)}: ${d.new} new` : `${A.listings.length} listings` }; }).catch(() => { S.apartments = { big: "—", line: "Open to set up" }; }),
    H.api("/api/travel/commute").then((C) => { const m = C.months[C.months.length - 1];
      S.commute = m ? { big: String(m.metro_rides + m.bike_rides), unit: "trips this month", line: `${m.bike_rides} by bike · ${m.metro_rides} by Metro${m.benefit != null ? ` · ${H.usd.format(Math.max(0, m.benefit - m.metro_fares))} benefit left` : ""}` }
        : { big: "—", line: "Bring in your rides and Metro trips" }; }).catch(() => { S.commute = { big: "—", line: "Open to set up" }; }),
    tripsData().then((T) => { const next = T.trips.find((t) => t.when === "upcoming" || t.when === "now");
      S.trips = next ? { big: next.when === "now" ? "Now" : String(next.days_until), unit: next.when === "now" ? "" : next.days_until === 1 ? "day" : "days", line: `${next.name}${next.places?.length ? ` · ${next.places[0]}` : ""}` }
        : { big: String(T.trips.length), unit: T.trips.length === 1 ? "trip" : "trips", line: "Add a trip" }; }).catch(() => { S.trips = { big: "—", line: "Add a trip" }; }),
  ]);
  if (H.$("travelRoot").dataset.area === "home") draw(S);
}

const yr = new Date().getFullYear();
function fd(d) {
  if (!d) return "";
  const x = new Date(d.slice(0, 10) + "T00:00");
  return x.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(x.getFullYear() !== yr ? { year: "numeric" } : {}) });
}
const num = (v) => (v == null ? "—" : Math.round(v).toLocaleString("en-US"));
const money = (v) => (v == null ? "—" : H.usd0.format(v));

// ---------------------------------------------------------------- Bikeshare
async function loadBike(quiet) {
  const root = H.$("travelBike");
  if (!bike && !quiet) root.innerHTML = `<p class="empty">Loading stations…</p>`;
  const [b, r] = await Promise.all([H.api("/api/travel/bike"), quiet && rides ? Promise.resolve(rides) : H.api("/api/travel/bike/rides")]);
  bike = b; rides = r;
  renderBike();
}

function count(v, label, warn) {
  return `<div class="bk-count ${warn ? "bk-low" : ""}"><span class="bk-num">${v == null ? "—" : v}</span><span class="bk-label">${label}</span></div>`;
}

function stationCard(s, i) {
  const off = s.live && (!s.renting || !s.returning)
    ? `<span class="pill pill-warn">${!s.renting && !s.returning ? "Closed" : !s.renting ? "Not renting" : "Not taking returns"}</span>` : "";
  const body = s.gone ? `<p class="hint">This station isn't in the feed anymore. Remove it and pick another.</p>`
    : !s.live ? `<p class="hint">No live numbers right now.</p>`
    : `<div class="bk-counts">${count(s.bikes, "Bikes", s.bikes === 0)}${count(s.ebikes, "E-bikes", s.ebikes === 0)}${count(s.docks, "Open docks", s.docks === 0)}</div>`;
  return `<div class="card bk-card" data-i="${i}">
    <div class="bk-top"><div>${s.label ? `<p class="eyebrow">${H.esc(s.label)}</p>` : ""}<h3 class="bk-name">${H.esc(s.name || "Station")}</h3></div>${off}</div>
    ${body}
    <div class="bk-foot"><span class="muted">${s.capacity ? `${s.capacity} docks` : ""}</span><button class="text-btn" data-rm="${i}">Remove</button></div>
  </div>`;
}

function spendCard(sp) {
  if (!sp || !sp.charges) {
    return `<div class="card"><h3 class="card-head">Bank and card charges</h3><p class="hint">No bikeshare charges in your bank or card data in the last 12 months. They show here once your accounts sync or you import a statement.</p></div>`;
  }
  const months = Object.entries(sp.by_month), max = Math.max(...months.map(([, v]) => Math.abs(v)), 1);
  return `<div class="card"><h3 class="card-head">Bank and card charges</h3>
    <div class="bk-spend">
      <div><span class="kpi-label">This year</span><span class="bk-big">${H.usd0.format(sp.year_total)}</span></div>
      <div><span class="kpi-label">Last 12 months</span><span class="bk-big">${H.usd0.format(sp.total)}</span><span class="kpi-sub">${sp.charges} charge${sp.charges === 1 ? "" : "s"}</span></div>
    </div>
    <div class="bk-months">${months.map(([m, v]) => `<div class="bk-month" title="${m}: ${H.usd0.format(v)}">
      <span class="bk-bar" style="height:${Math.max(4, Math.abs(v) / max * 100).toFixed(0)}%"></span>
      <span class="bk-mlabel">${new Date(m + "-01T00:00").toLocaleDateString("en-US", { month: "narrow" })}</span></div>`).join("")}</div>
    <p class="hint">Everything charged by the bikeshare in your bank and card data, membership included. It can differ from the receipts above when a charge posts on another day.</p>
  </div>`;
}

function renderBike() {
  const root = H.$("travelBike"), b = bike;
  const upd = b.updated ? new Date(b.updated * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }) : "";
  root.innerHTML = `
    <div class="tr-head">
      <div><p class="eyebrow">Capital Bikeshare</p><h1 class="title">Your stations</h1>
        <p class="lede-sm">${b.feed === "down" ? "The live feed isn't answering. Numbers return when it does." : upd ? `Live · updated ${upd} · refreshes every minute` : ""}</p></div>
      <div class="goals-actions"><button class="btn btn-secondary btn-sm" id="bkRefresh">Refresh</button><button class="btn btn-primary btn-sm" id="bkAdd">Add station</button></div>
    </div>
    ${b.stations.length ? `<div class="bk-grid">${b.stations.map(stationCard).join("")}</div>`
      : `<div class="card empty-state"><h3>Pick the stations you use</h3><p>Add the docks near home, class or the gym. Each one shows live bikes, e-bikes and open docks.</p>
         <div class="empty-actions"><button class="btn btn-primary" id="bkAdd2">Add a station</button></div></div>`}
    ${ridesSection(rides)}
    ${spendCard(b.spend)}`;
  H.$("bkRefresh").onclick = () => loadBike(true).catch(H.showError);
  H.$("bkAdd").onclick = openStationPicker;
  H.$("bkAdd2")?.addEventListener("click", openStationPicker);
  bindRides(root);
  root.querySelectorAll("[data-rm]").forEach((el) => (el.onclick = () => {
    const keep = b.stations.filter((_, i) => i !== +el.dataset.rm);
    saveStations(keep).catch(H.showError);
  }));
}

async function saveStations(list) {
  bike = await H.api("/api/travel/bike/saved", { method: "PUT",
    body: JSON.stringify({ stations: list.map((s) => ({ id: s.id, label: s.label || "", name: s.name || "" })) }) });
  renderBike();
}

function openStationPicker() {
  const dlg = H.$("bkDialog");
  const list = dlg.querySelector("#bkResults"), q = dlg.querySelector("#bkQuery"), label = dlg.querySelector("#bkLabel");
  q.value = ""; label.value = ""; list.innerHTML = `<p class="hint">Type part of a street or place name, or find the stations closest to you.</p>`;
  const show = (rows) => {
    const have = new Set(bike.stations.map((s) => String(s.id)));
    list.innerHTML = rows.length ? rows.map((s) => `<div class="bk-result">
      <div><div class="bk-rname">${H.esc(s.name)}</div><div class="muted">${s.live ? `${s.bikes ?? "—"} bikes · ${s.ebikes ?? "—"} e-bikes · ${s.docks ?? "—"} docks` : "No live numbers"}${s.mi != null ? ` · ${s.mi.toFixed(1)} mi` : ""}</div></div>
      ${have.has(String(s.id)) ? `<span class="muted">Added</span>` : `<button type="button" class="btn btn-secondary btn-sm" data-id="${H.esc(s.id)}" data-name="${H.esc(s.name)}">Add</button>`}
    </div>`).join("") : `<p class="hint">No stations match.</p>`;
    list.querySelectorAll("[data-id]").forEach((el) => (el.onclick = async () => {
      if (bike.stations.length >= 12) return H.toast("You can save up to 12 stations.");
      try {
        await saveStations([...bike.stations, { id: el.dataset.id, name: el.dataset.name, label: label.value.trim() }]);
        dlg.close(); H.toast("Station added");
      } catch (e) { H.showError(e); }
    }));
  };
  let t;
  q.oninput = () => { clearTimeout(t); t = setTimeout(async () => {
    if (q.value.trim().length < 2) return;
    try { show((await H.api(`/api/travel/bike/stations?q=${encodeURIComponent(q.value.trim())}`)).stations); } catch (e) { H.showError(e); }
  }, 250); };
  dlg.querySelector("#bkNear").onclick = () => {
    if (!navigator.geolocation) return H.toast("This browser can't share your location.");
    list.innerHTML = `<p class="hint">Finding stations near you…</p>`;
    navigator.geolocation.getCurrentPosition(async (p) => {
      try { show((await H.api(`/api/travel/bike/stations?lat=${p.coords.latitude}&lon=${p.coords.longitude}`)).stations); } catch (e) { H.showError(e); }
    }, () => { list.innerHTML = `<p class="hint">Location is off for this site. Search by name instead.</p>`; }, { timeout: 10000 });
  };
  dlg.querySelector("#bkClose").onclick = () => dlg.close();
  dlg.showModal();
  q.focus();
}

// ---------------------------------------------------------------- Rides (from ride receipt emails)
const hrs = (m) => (m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`);

function memberCard(M) {
  if (M.price == null) {
    return `<div class="card"><h3 class="card-head">Has your membership paid off?</h3>
      <p class="hint">Your receipts show ${H.usd.format(M.saved)} saved across ${M.rides} ride${M.rides === 1 ? "" : "s"}. Enter what you paid for the membership to compare.</p>
      <button class="btn btn-secondary btn-sm" data-mem>Enter membership</button></div>`;
  }
  const pct = M.price > 0 ? Math.min(100, (M.saved / M.price) * 100) : 100;
  const span = M.start || M.end ? `${M.start ? fd(M.start) : "…"} to ${M.end ? fd(M.end) : "today"}` : "All rides";
  return `<div class="card"><div class="bk-top"><h3 class="card-head">Membership</h3><button class="text-btn" data-mem>Edit</button></div>
    <div class="bk-verdict ${M.paid_off ? "pos" : ""}">${M.paid_off ? "Paid off" : `${H.usd.format(-M.net)} to go`}</div>
    <p class="muted">${M.paid_off ? `${H.usd.format(M.net)} ahead: ` : ""}${H.usd.format(M.saved)} saved of ${H.usd.format(M.price)} paid · ${H.esc(span)}</p>
    <div class="goal-bar bk-pbar"><div class="goal-fill pace-on_track" style="width:${pct.toFixed(1)}%"></div></div>
    ${!M.paid_off && M.rides_to_go ? `<p class="hint">About ${M.rides_to_go} more ride${M.rides_to_go === 1 ? "" : "s"} at your average saving per ride.</p>` : ""}
    <p class="hint">Savings are the "Saved this trip" amounts on your receipts${M.with_savings < M.rides ? ` (${M.rides - M.with_savings} receipts don't list one)` : ""}.</p></div>`;
}

function ridesSection(R) {
  if (!R) return "";
  const T = R.totals;
  const scan = H.isDemo() ? "" : `<button class="btn btn-primary btn-sm" id="rdScan">${T.rides ? "Check for new rides" : "Connect ride receipts"}</button>`;
  const head = `<div class="tr-sub"><div><h2 class="title-sm">Your rides</h2>
      <p class="muted">${T.rides ? `${fd(T.first)} to ${fd(T.last)}${R.gmail?.last_scan ? ` · checked ${fd(R.gmail.last_scan)}` : ""}` : ""}</p></div>
    <div class="goals-actions"><label class="btn btn-secondary btn-sm">Import file<input type="file" id="rdImport" accept=".json" hidden /></label>${scan}</div></div>`;
  if (!T.rides) {
    return head + `<div class="card empty-state"><h3>Bring in your rides</h3><p>Capital Bikeshare emails a receipt after every ride, with the stations, times, cost and what your membership saved. LifeTracker reads those receipts from your Gmail (read-only) and builds your stats. Nothing else in your inbox is kept.</p>${H.isDemo() ? `<p class="hint">Reading email is off in the demo.</p>` : ""}</div>`;
  }
  const months = Object.entries(R.by_month).slice(-12), max = Math.max(...months.map(([, v]) => v.rides), 1);
  return head + `
    <div class="kpis">
      <div class="kpi"><span class="kpi-label">Rides</span><span class="kpi-value">${T.rides}</span><span class="kpi-sub">${hrs(T.minutes)} riding</span></div>
      <div class="kpi"><span class="kpi-label">Miles, estimated</span><span class="kpi-value">${T.miles_rides ? T.miles.toFixed(1) : "—"}</span><span class="kpi-sub">Straight line, ${T.miles_rides} of ${T.rides} rides</span></div>
      <div class="kpi"><span class="kpi-label">Spent on rides</span><span class="kpi-value">${H.usd0.format(T.spent)}</span><span class="kpi-sub">From receipts</span></div>
      <div class="kpi"><span class="kpi-label">Member savings</span><span class="kpi-value pos">${H.usd0.format(T.saved)}</span><span class="kpi-sub">From receipts</span></div>
    </div>
    <div class="grid-2 bk-two">
      ${memberCard(R.membership)}
      <div class="card"><h3 class="card-head">Rides by month</h3>
        <div class="bk-months">${months.map(([m, v]) => `<div class="bk-month" title="${m}: ${v.rides} rides, ${v.miles.toFixed(1)} mi">
          <span class="bk-bar" style="height:${Math.max(4, (v.rides / max) * 100).toFixed(0)}%"></span>
          <span class="bk-mlabel">${new Date(m + "-01T00:00").toLocaleDateString("en-US", { month: "short" })}</span></div>`).join("")}</div></div>
    </div>
    ${R.rides.some((r) => r.miles != null) ? `<div class="card card-flush apt-map-card"><div id="rideMap" class="apt-map"></div></div>
      <p class="hint">Lines run straight from start to end station. Receipts don't include the route you rode.</p>` : ""}
    <div class="grid-2 bk-two">
      <div class="card"><h3 class="card-head">Most common trips</h3>${R.top_trips.map((t) =>
        `<div class="bk-li"><span>${H.esc(t.from)} <span class="muted">to</span> ${H.esc(t.to)}</span><span class="muted">${t.rides}</span></div>`).join("") || `<p class="hint">No trips with both stations yet.</p>`}</div>
      <div class="card"><h3 class="card-head">Top stations</h3>${R.top_stations.map((t) =>
        `<div class="bk-li"><span>${H.esc(t.name)}</span><span class="muted">${t.visits}</span></div>`).join("")}</div>
    </div>
    <div class="card"><div class="bk-top"><h3 class="card-head">Recent rides</h3><button class="text-btn danger" id="rdForget">Forget all rides</button></div>
      ${R.rides.slice(0, 12).map((r) => `<div class="bk-ride">
        <div><div>${H.esc(r.start_station || "Unknown")} <span class="muted">to</span> ${H.esc(r.end_station || "Unknown")}</div>
          <div class="muted">${fd(r.date)}${r.start_time ? ` · ${H.esc(r.start_time)}` : ""}${r.minutes != null ? ` · ${r.minutes} min` : ""}${r.miles != null ? ` · ${r.miles.toFixed(1)} mi est.` : ""}${r.bike ? ` · ${r.bike === "ebike" ? "E-bike" : "Classic"}` : ""}</div></div>
        <div class="bk-ride-amt">${r.total != null ? H.usd.format(r.total) : "—"}${r.saved ? `<div class="pos">saved ${H.usd.format(r.saved)}</div>` : ""}</div>
      </div>`).join("")}</div>`;
}

function drawRideMap() {
  rideMap?.remove(); rideMap = null;
  const el = H.$("rideMap");
  if (!el || !window.L) return;
  const counts = {};
  for (const r of rides.rides) if (r.miles != null) {
    const k = [r.start_lat, r.start_lon, r.end_lat, r.end_lon].join(",");
    counts[k] = (counts[k] || 0) + 1;
  }
  rideMap = L.map(el, { scrollWheelZoom: false });
  H.tileLayer().addTo(rideMap);
  const color = getComputedStyle(document.body).getPropertyValue("--accent").trim(), pts = [];
  for (const [k, n] of Object.entries(counts)) {
    const [a, b, c, d] = k.split(",").map(Number);
    L.polyline([[a, b], [c, d]], { color, weight: Math.min(2 + n, 8), opacity: 0.55 }).addTo(rideMap).bindTooltip(`${n} ride${n === 1 ? "" : "s"}`);
    pts.push([a, b], [c, d]);
  }
  if (pts.length) rideMap.fitBounds(pts, { padding: [24, 24], maxZoom: 15 });
}

function bindRides(root) {
  root.querySelectorAll("[data-mem]").forEach((b) => (b.onclick = openMembership));
  H.$("rdScan")?.addEventListener("click", scanRides);
  H.$("rdImport")?.addEventListener("change", async (ev) => {
    const f = ev.target.files[0]; ev.target.value = "";
    if (!f) return;
    try {
      let body = JSON.parse(await f.text()); if (Array.isArray(body)) body = { rides: body };
      rides = await H.api("/api/travel/bike/rides/import", { method: "POST", body: JSON.stringify(body) });
      renderBike(); H.toast(`Imported ${rides.added} ride${rides.added === 1 ? "" : "s"}`);
    } catch (e) { H.showError(e instanceof SyntaxError ? new Error("That file isn't valid JSON.") : e); }
  });
  H.$("rdForget")?.addEventListener("click", async () => {
    if (!confirm("Remove every imported ride from LifeTracker? Your emails aren't touched.")) return;
    try { rides = await H.api("/api/travel/bike/rides", { method: "DELETE" }); renderBike(); } catch (e) { H.showError(e); }
  });
  drawRideMap();
}

async function scanRides() {
  const b = H.$("rdScan"), label = b.textContent;
  b.disabled = true; b.textContent = "Reading receipts…";
  const run = async (force) => H.api("/api/travel/bike/rides/scan", { method: "POST", body: JSON.stringify({ access_token: await H.gmailToken(force) }) });
  try {
    let r;
    try { r = await run(false); } catch (e) { if (!/expired|denied|Gmail access/i.test(e.message)) throw e; r = await run(true); }
    rides = r; renderBike();
    H.toast(r.added ? `Added ${r.added} ride${r.added === 1 ? "" : "s"}` : r.read ? "No new rides in those receipts" : "No new ride receipts");
  } catch (e) {
    H.toast(/popup-closed|cancelled/.test(e.code || e.message) ? "Gmail connection cancelled" : e.message);
    b.disabled = false; b.textContent = label;
  }
}

function openMembership() {
  const dlg = H.$("memDialog"), f = dlg.querySelector("form"), M = rides.membership;
  f.elements.price.value = M.price ?? ""; f.elements.start.value = M.start || ""; f.elements.end.value = M.end || "";
  f.onsubmit = async (e) => {
    e.preventDefault();
    if (e.submitter?.value === "cancel") return dlg.close();
    try {
      rides = await H.api("/api/travel/bike/membership", { method: "PUT", body: JSON.stringify(Object.fromEntries(new FormData(f))) });
      dlg.close(); renderBike(); H.toast("Membership saved");
    } catch (err) { H.showError(err); }
  };
  dlg.showModal();
}


// ---------------------------------------------------------------- Commute overview: both modes, month by month
async function loadOverview(area) {
  if (!area.innerHTML.includes("tr-overview")) area.innerHTML = `<p class="empty">Loading…</p>`;
  const C = await H.api("/api/travel/commute");
  const M = C.months, last = M[M.length - 1], B = C.metro.benefit || {};
  const max = Math.max(1, ...M.map((m) => m.metro_rides + m.bike_rides));
  const benefitNow = last && last.benefit != null ? last.benefit - last.metro_fares : null;
  area.innerHTML = `<div class="tr-overview">
    <div class="kpis kpis-3">
      <div class="kpi"><span class="kpi-label">Trips this month</span><span class="kpi-value">${last ? last.metro_rides + last.bike_rides : "—"}</span>
        <span class="kpi-sub">${last ? `${last.bike_rides} bikeshare · ${last.metro_rides} Metro` : "No trips yet"}</span></div>
      <div class="kpi"><span class="kpi-label">${H.esc(B.label || "Transit benefit")} left</span><span class="kpi-value">${benefitNow != null ? H.usd.format(Math.max(0, benefitNow)) : "—"}</span>
        <span class="kpi-sub">${benefitNow != null ? `of ${H.usd.format(last.benefit)} this month, after ${H.usd.format(last.metro_fares)} in fares` : `<a class="link-sm" href="#/travel/commute/metro">Set up your benefit</a>`}</span></div>
      <div class="kpi"><span class="kpi-label">Bikeshare membership</span><span class="kpi-value ${C.bike.membership.paid_off ? "pos" : ""}">${C.bike.membership.price == null ? "—" : C.bike.membership.paid_off ? "Paid off" : H.usd.format(-C.bike.membership.net)}</span>
        <span class="kpi-sub">${C.bike.membership.price == null ? `<a class="link-sm" href="#/travel/commute/bike">Enter what you paid</a>` : C.bike.membership.paid_off ? `${H.usd.format(C.bike.membership.net)} ahead` : "to go"}</span></div>
    </div>
    <div class="card"><h3 class="card-head">Trips by month</h3>
      ${M.length ? `<div class="cm-chart">${M.map((m) => `<div class="cm-col" title="${m.month}: ${m.bike_rides} bikeshare, ${m.metro_rides} Metro">
        <div class="cm-stack"><span class="cm-metro" style="height:${(m.metro_rides / max * 100).toFixed(1)}%"></span><span class="cm-bike" style="height:${(m.bike_rides / max * 100).toFixed(1)}%"></span></div>
        <span class="bk-mlabel">${new Date(m.month + "-01T00:00").toLocaleDateString("en-US", { month: "short" })}</span></div>`).join("")}</div>
        <div class="cm-legend"><span><i class="cm-bike"></i>Bikeshare</span><span><i class="cm-metro"></i>Metro</span></div>`
        : `<p class="hint">Bring in your bikeshare ride receipts and your SmarTrip history to see both here.</p>`}
    </div>
    <div class="card"><h3 class="card-head">Money by month</h3>
      ${M.length ? `<div class="h-table"><div class="tr-mrow tr-mhead"><span>Month</span><span>Metro fares</span><span>Benefit</span><span>Bikeshare</span><span>Bike miles</span></div>
        ${[...M].reverse().map((m) => `<div class="tr-mrow"><span>${new Date(m.month + "-01T00:00").toLocaleDateString("en-US", { month: "short", year: "numeric" })}</span>
          <span>${H.usd.format(m.metro_fares)}</span><span>${m.benefit != null ? H.usd.format(m.benefit) : "—"}</span><span>${H.usd.format(m.bike_spent)}</span><span>${m.bike_miles ? m.bike_miles.toFixed(1) : "—"}</span></div>`).join("")}</div>
        <p class="hint">Bike miles are straight-line estimates between stations. Metro fares come from your SmarTrip history.</p>` : `<p class="hint">Nothing yet.</p>`}
    </div></div>`;
}

// ---------------------------------------------------------------- Metro: SmarTrip history and the transit benefit
async function loadMetro(area) {
  if (!metro) area.innerHTML = `<p class="empty">Loading Metro…</p>`;
  metro = await H.api("/api/travel/metro");
  renderMetro(area);
}

function renderMetro(area) {
  const M = metro, B = M.benefit || {}, T = M.totals;
  const months = Object.entries(M.by_month).slice(-12);
  const cur = months.length ? months[months.length - 1][1] : null;
  const benefitCard = B.monthly == null
    ? `<div class="card"><h3 class="card-head">Transit benefit</h3><p class="hint">If school or work loads money onto your SmarTrip card each month (SmartBenefits), enter the amount to see how much is left each month.</p>
        <button class="btn btn-secondary btn-sm" data-ben>Set up benefit</button></div>`
    : `<div class="card"><div class="bk-top"><h3 class="card-head">${H.esc(B.label || "Transit benefit")}</h3><button class="text-btn" data-ben>Edit</button></div>
        ${cur && cur.benefit != null ? `<div class="bk-verdict ${cur.benefit_left >= 0 ? "" : "neg"}">${H.usd.format(Math.max(0, cur.benefit_left))} left</div>
          <p class="muted">${H.usd.format(cur.fares)} in fares of ${H.usd.format(cur.benefit)} this month${cur.benefit_left < 0 ? `, ${H.usd.format(-cur.benefit_left)} over` : ""}</p>
          <div class="goal-bar bk-pbar"><div class="goal-fill pace-on_track" style="width:${Math.min(100, (cur.fares / cur.benefit) * 100).toFixed(1)}%"></div></div>` : `<p class="hint">Not active this month.</p>`}
        <p class="hint">${H.usd.format(B.monthly)} a month${B.start ? ` from ${fd(B.start)}` : ""}${B.end ? ` to ${fd(B.end)}` : ""}. Fares come from your SmarTrip history; import it again to bring this up to date.</p></div>`;
  area.innerHTML = `
    <div class="tr-head"><div><p class="eyebrow">Metro</p><h1 class="title">${T.rides ? `${T.rides} ride${T.rides === 1 ? "" : "s"}` : "SmarTrip"}</h1>
      <p class="lede-sm">${T.rides ? `${fd(T.first)} to ${fd(T.last)}${M.balance != null ? ` · card balance ${H.usd.format(M.balance)} on ${fd(M.balance_date)}` : ""}` : "Import your card's usage history to see trips and fares."}</p></div>
      <div class="goals-actions"><label class="btn btn-primary btn-sm">Import SmarTrip CSV<input type="file" id="mtImport" accept=".csv,text/csv" hidden /></label></div></div>
    <div class="grid-2 bk-two">${benefitCard}
      <div class="card"><h3 class="card-head">Rides by month</h3>${months.length ? `<div class="bk-months">${months.map(([m, v]) => `<div class="bk-month" title="${m}: ${v.rail} rail, ${v.bus} bus, ${H.usd.format(v.fares)}">
        <span class="bk-bar" style="height:${Math.max(4, (v.rides / Math.max(1, ...months.map(([, x]) => x.rides))) * 100).toFixed(0)}%"></span>
        <span class="bk-mlabel">${new Date(m + "-01T00:00").toLocaleDateString("en-US", { month: "short" })}</span></div>`).join("")}</div>` : `<p class="hint">No trips yet.</p>`}</div></div>
    ${T.rides ? "" : `<div class="card"><h3 class="card-head">How to get your SmarTrip history</h3>
      <p class="hint">WMATA doesn't share trips with other apps, so this uses the file you can download. Sign in at smartrip.wmata.com, open your card, choose Card usage (or Usage history), pick the dates, and export or download it as CSV. Then press Import SmarTrip CSV here. Importing again later only adds new trips.</p></div>`}
    ${M.top_stations.length ? `<div class="card"><h3 class="card-head">Top stations</h3>${M.top_stations.map((s) => `<div class="bk-li"><span>${H.esc(s.name)}</span><span class="muted">${s.visits}</span></div>`).join("")}</div>` : ""}
    ${M.trips.length ? `<div class="card"><div class="bk-top"><h3 class="card-head">Recent activity</h3><button class="text-btn danger" id="mtForget">Forget Metro trips</button></div>
      ${M.trips.slice(0, 15).map((t) => `<div class="bk-ride"><div><div>${t.kind === "rail" ? `${H.esc(t.entry || "?")} <span class="muted">to</span> ${H.esc(t.exit || "?")}` : t.kind === "bus" ? `Bus ${H.esc(t.entry)}` : H.esc(t.desc || t.kind)}</div>
        <div class="muted">${fd(t.date)}${t.time ? ` · ${t.time}` : ""} · ${{ rail: "Metrorail", bus: "Metrobus", entry: "Entry", load: "Added value", benefit: "Benefit", other: "Other" }[t.kind]}</div></div>
        <div class="bk-ride-amt ${t.change > 0 ? "pos" : ""}">${t.change != null && t.change !== 0 ? (t.change > 0 ? "+" : "−") + H.usd.format(Math.abs(t.change)) : ""}</div></div>`).join("")}</div>` : ""}
    ${M.bank_total ? `<p class="hint">Your bank and card data also show ${H.usd.format(M.bank_total)} paid to WMATA or SmarTrip in the last 12 months.</p>` : ""}`;
  area.querySelectorAll("[data-ben]").forEach((b) => (b.onclick = () => openBenefit(area)));
  H.$("mtImport").onchange = async (ev) => {
    const f = ev.target.files[0]; ev.target.value = "";
    if (!f) return;
    try { metro = await H.api("/api/travel/metro/import", { method: "POST", body: JSON.stringify({ csv: await f.text() }) }); renderMetro(area);
      H.toast(`Read ${metro.read} rows, ${metro.added} new`); } catch (e) { H.showError(e); }
  };
  H.$("mtForget")?.addEventListener("click", async () => {
    if (!confirm("Remove every imported Metro trip from LifeTracker?")) return;
    try { metro = await H.api("/api/travel/metro", { method: "DELETE" }); renderMetro(area); } catch (e) { H.showError(e); }
  });
}

function openBenefit(area) {
  let d = document.getElementById("benDialog");
  if (!d) {
    d = Object.assign(document.createElement("dialog"), { id: "benDialog", className: "dialog" });
    d.innerHTML = `<form class="dialog-form" method="dialog"><h3>Transit benefit</h3>
      <p class="hint span-2">The amount loaded onto your SmarTrip card each month, for example through your school's SmartBenefits program.</p>
      <label class="span-2">Name<input class="input" name="label" placeholder="School SmartBenefits" maxlength="60" /></label>
      <label class="span-2">Amount per month<input class="input" name="monthly" inputmode="decimal" placeholder="Blank until you enter it" /></label>
      <label>Starts<input class="input" type="date" name="start" /></label><label>Ends (optional)<input class="input" type="date" name="end" /></label>
      <div class="span-2 dialog-actions"><button class="btn btn-primary" value="save">Save</button><button class="btn btn-secondary" value="cancel" formnovalidate>Cancel</button></div></form>`;
    document.body.append(d);
  }
  const f = d.querySelector("form"), B = metro.benefit || {};
  f.elements.label.value = B.label || ""; f.elements.monthly.value = B.monthly ?? ""; f.elements.start.value = B.start || ""; f.elements.end.value = B.end || "";
  f.onsubmit = async (e) => {
    e.preventDefault();
    if (e.submitter?.value === "cancel") return d.close();
    try { metro = await H.api("/api/travel/metro/benefit", { method: "PUT", body: JSON.stringify(Object.fromEntries(new FormData(f))) }); d.close(); renderMetro(area); H.toast("Benefit saved"); }
    catch (err) { H.showError(err); }
  };
  d.showModal();
}

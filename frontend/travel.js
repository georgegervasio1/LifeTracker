// Travel: Capital Bikeshare at your saved stations (live from the public feed) and an apartment search with a
// ranked wishlist (listings arrive from the weekly digest or an imported file).
// app.js passes its helpers in with initTravel() and calls loadTravel(tab) on #/travel/<tab>.

let H;                        // { api, $, esc, toast, showError, usd0, isDemo }
let bike = null, apts = null, rides = null;
let aptFilter = "new", aptSort = "sqft";
let refreshTimer = null, aptMap = null, wishSort = null, rideMap = null;

export function initTravel(helpers) { H = helpers; }

export async function loadTravel(tab) {
  clearInterval(refreshTimer);
  if (tab === "apartments") return loadApartments();
  await loadBike();
  refreshTimer = setInterval(() => {
    if (document.visibilityState === "visible" && location.hash.startsWith("#/travel/bike")) loadBike(true).catch(() => {});
    else if (!location.hash.startsWith("#/travel")) clearInterval(refreshTimer);
  }, 60000);
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

// ---------------------------------------------------------------- Apartments
const STATUS = { new: "New", saved: "Wishlist", touring: "Touring", applied: "Applied", passed: "Passed" };

async function loadApartments() {
  const root = H.$("travelApts");
  if (!apts) root.innerHTML = `<p class="empty">Loading listings…</p>`;
  apts = await H.api("/api/apartments");
  renderApts();
}

function setApts(r) { apts = r; renderApts(); }

function facts(a) {
  return [a.rent != null ? `${money(a.rent)}/mo` : "", a.sqft ? `${num(a.sqft)} sq ft` : "",
    a.beds != null ? `${a.beds} bd` : "", a.baths != null ? `${a.baths} ba` : ""].filter(Boolean).join(" · ");
}

function pills(a) {
  return [a.is_new && a.status === "new" ? `<span class="pill pill-on_track">New</span>` : "",
    a.fit === "stretch" ? `<span class="pill pill-warn">Stretch</span>` : a.fit === "over" ? `<span class="pill pill-missing">Over budget</span>` : "",
    a.big ? `<span class="pill pill-met">Large</span>` : "",
    a.status !== "new" ? `<span class="pill pill-plain">${STATUS[a.status]}</span>` : ""].join("");
}

function where(a) {
  return [a.neighborhood, a.nearest_stop ? `${a.nearest_stop}${a.stop_mi != null ? ` (${a.stop_mi} mi)` : ""}` : "",
    a.available ? `Available ${/^\d{4}-\d{2}-\d{2}$/.test(a.available) ? fd(a.available) : a.available}` : ""].filter(Boolean).join(" · ");
}

function listingRow(a) {
  return `<div class="apt-row" data-id="${a.id}">
    <div class="apt-main">
      <div class="apt-title"><a href="${H.esc(a.url)}" target="_blank" rel="noopener">${H.esc(a.address || a.title)}</a>${pills(a)}</div>
      <div class="apt-facts">${H.esc(facts(a)) || `<span class="muted">No rent or size listed</span>`}</div>
      ${where(a) ? `<div class="muted apt-where">${H.esc(where(a))}</div>` : ""}
      ${a.amenities?.length ? `<div class="apt-chips">${a.amenities.map((x) => `<span class="apt-chip">${H.esc(x)}</span>`).join("")}</div>` : ""}
    </div>
    <div class="apt-acts">
      ${["saved", "touring", "applied"].includes(a.status) ? `<span class="muted">On wishlist</span>`
        : `<button class="btn btn-secondary btn-sm" data-st="saved">Save</button>`}
      ${a.status === "passed" ? `<button class="text-btn" data-st="new">Undo pass</button>` : `<button class="text-btn" data-st="passed">Pass</button>`}
    </div>
  </div>`;
}

function wishCard(a, i) {
  return `<div class="card apt-wish" data-id="${a.id}">
    <span class="apt-rank drag" title="Drag to reorder">${i + 1}</span>
    <div class="apt-wmain">
      <div class="apt-title"><a href="${H.esc(a.url)}" target="_blank" rel="noopener">${H.esc(a.address || a.title)}</a>${pills({ ...a, status: "new", is_new: false })}</div>
      <div class="apt-facts">${H.esc(facts(a))}</div>
      ${where(a) ? `<div class="muted apt-where">${H.esc(where(a))}</div>` : ""}
      <textarea class="input apt-notes" rows="1" placeholder="Notes: questions, tour time, what you liked" data-notes>${H.esc(a.notes || "")}</textarea>
    </div>
    <div class="apt-wside">
      <select class="select apt-status" data-status aria-label="Status">${["saved", "touring", "applied", "passed"].map((s) =>
        `<option value="${s}" ${a.status === s ? "selected" : ""}>${STATUS[s]}</option>`).join("")}</select>
    </div>
  </div>`;
}

function renderApts() {
  const root = H.$("travelApts"), A = apts, C = A.criteria || {};
  const byId = Object.fromEntries(A.listings.map((a) => [a.id, a]));
  const wish = A.wishlist.map((id) => byId[id]).filter(Boolean);
  const newCount = A.listings.filter((a) => a.is_new && a.status === "new").length;
  const sorters = { sqft: (a, b) => (b.sqft || 0) - (a.sqft || 0), rent: (a, b) => (a.rent ?? 1e9) - (b.rent ?? 1e9),
                    newest: (a, b) => (b.first_seen || "").localeCompare(a.first_seen || "") };
  const shown = A.listings.filter((a) => aptFilter === "new" ? a.is_new && a.status === "new"
    : aptFilter === "passed" ? a.status === "passed" : a.status !== "passed").sort(sorters[aptSort]);
  const last = A.latest_digest ? `Latest digest ${fd(A.latest_digest)} · ${newCount} new` : "No digest received yet";
  const crit = [C.max_rent != null ? `Up to ${money(C.max_rent)}${C.stretch_rent != null ? ` (stretch ${money(C.stretch_rent)})` : ""}` : "",
    C.min_beds != null ? `${C.min_beds}+ beds` : "", C.move_in ? `Move in ${fd(C.move_in)}` : ""].filter(Boolean).join(" · ");
  const seg = (v, label) => `<button class="seg ${aptFilter === v ? "seg-on" : ""}" data-af="${v}">${label}</button>`;
  root.innerHTML = `
    <div class="tr-head">
      <div><p class="eyebrow">Apartment search${C.city ? ` · ${H.esc(C.city)}` : ""}</p><h1 class="title">${wish.length ? `${wish.length} on your wishlist` : "Apartments"}</h1>
        <p class="lede-sm">${H.esc(last)}${crit ? ` · ${H.esc(crit)}` : ""}</p></div>
      <div class="goals-actions"><label class="btn btn-secondary btn-sm">Import file<input type="file" id="aptImport" accept=".json" hidden /></label>
        <button class="btn btn-secondary btn-sm" id="aptCrit">Criteria</button></div>
    </div>
    ${A.listings.length ? "" : `<div class="card empty-state"><h3>No listings yet</h3><p>Listings from your weekly apartment digest land here, so you can save the best ones to a ranked wishlist with notes. You can also import a listings file.</p>
      <div class="empty-actions"><button class="btn btn-secondary" id="aptCrit2">Set your criteria</button></div></div>`}
    ${wish.length ? `<h2 class="section-label">Wishlist</h2><div class="apt-wishlist" id="aptWish">${wish.map(wishCard).join("")}</div>
      <p class="hint">Drag the numbers to rank them.</p>` : A.listings.length ? `<p class="hint">Save listings to build a ranked wishlist.</p>` : ""}
    ${A.listings.some((a) => a.lat != null) ? `<div class="card card-flush apt-map-card"><div id="aptMap" class="apt-map"></div></div>` : ""}
    ${A.listings.length ? `<div class="apt-bar"><div class="seg-row">${seg("new", `New${newCount ? ` ${newCount}` : ""}`)}${seg("all", "All")}${seg("passed", "Passed")}</div>
      <select class="select" id="aptSort" aria-label="Sort"><option value="sqft">Largest first</option><option value="rent">Lowest rent</option><option value="newest">Newest</option></select></div>
      <div class="card apt-list">${shown.length ? shown.map(listingRow).join("") : `<p class="empty">${aptFilter === "new" ? "Nothing new in the latest digest." : "Nothing here."}</p>`}</div>` : ""}`;

  root.querySelectorAll("[data-af]").forEach((b) => (b.onclick = () => { aptFilter = b.dataset.af; renderApts(); }));
  const so = H.$("aptSort"); if (so) { so.value = aptSort; so.onchange = () => { aptSort = so.value; renderApts(); }; }
  H.$("aptImport").onchange = importApts;
  H.$("aptCrit").onclick = openCriteria;
  H.$("aptCrit2")?.addEventListener("click", openCriteria);
  root.querySelectorAll(".apt-row [data-st]").forEach((b) => (b.onclick = () =>
    patch(b.closest("[data-id]").dataset.id, { status: b.dataset.st, ...(b.dataset.st === "saved" ? { rank: wish.length } : {}) })));
  root.querySelectorAll(".apt-wish").forEach((card) => {
    const id = card.dataset.id;
    card.querySelector("[data-status]").onchange = (e) => patch(id, { status: e.target.value });
    const ta = card.querySelector("[data-notes]");
    const grow = () => { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; };
    grow(); ta.oninput = grow;
    ta.onchange = () => H.api(`/api/apartments/${id}`, { method: "PATCH", body: JSON.stringify({ notes: ta.value }) })
      .then((r) => { apts = r; H.toast("Notes saved"); }).catch(H.showError);
  });
  wishSort?.destroy(); wishSort = null;
  if (H.$("aptWish") && window.Sortable) {
    wishSort = Sortable.create(H.$("aptWish"), { handle: ".apt-rank", animation: 150, onEnd: () => {
      const ids = [...H.$("aptWish").children].map((el) => el.dataset.id);
      H.api("/api/apartments/order", { method: "PUT", body: JSON.stringify({ ids }) }).then(setApts).catch(H.showError);
    } });
  }
  drawMap(A.listings.filter((a) => a.lat != null && a.lon != null && a.status !== "passed"), new Set(A.wishlist));
}

function drawMap(rows, wish) {
  aptMap?.remove(); aptMap = null;
  if (!rows.length || !window.L || !H.$("aptMap")) return;
  aptMap = L.map("aptMap", { scrollWheelZoom: false, attributionControl: true });
  H.tileLayer().addTo(aptMap);
  const pts = rows.map((a) => {
    const on = wish.has(a.id);
    L.circleMarker([a.lat, a.lon], { radius: on ? 8 : 6, weight: 2, color: getComputedStyle(document.body).getPropertyValue(on ? "--accent" : "--text-3").trim(),
      fillOpacity: on ? 0.9 : 0.35 }).addTo(aptMap)
      .bindPopup(`<strong>${H.esc(a.address || a.title)}</strong><br>${H.esc(facts(a))}`);
    return [a.lat, a.lon];
  });
  aptMap.fitBounds(pts, { padding: [28, 28], maxZoom: 15 });
}

async function patch(id, body) {
  try { setApts(await H.api(`/api/apartments/${id}`, { method: "PATCH", body: JSON.stringify(body) })); } catch (e) { H.showError(e); }
}

async function importApts(ev) {
  const file = ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  try {
    let body = JSON.parse(await file.text());
    if (Array.isArray(body)) body = { listings: body };
    const r = await H.api("/api/apartments/import", { method: "POST", body: JSON.stringify(body) });
    setApts(r);
    H.toast(`Imported: ${r.added} new, ${r.updated} updated${r.skipped.length ? `, ${r.skipped.length} skipped` : ""}`);
  } catch (e) { H.showError(e instanceof SyntaxError ? new Error("That file isn't valid JSON.") : e); }
}

function openCriteria() {
  const dlg = H.$("aptDialog"), f = dlg.querySelector("form"), C = apts.criteria || {};
  for (const k of ["city", "max_rent", "stretch_rent", "min_beds", "big_sqft", "move_in"]) f.elements[k].value = C[k] ?? "";
  f.onsubmit = async (e) => {
    e.preventDefault();
    if (e.submitter?.value === "cancel") return dlg.close();
    try {
      setApts(await H.api("/api/apartments/criteria", { method: "PUT", body: JSON.stringify(Object.fromEntries(new FormData(f))) }));
      dlg.close(); H.toast("Criteria saved");
    } catch (err) { H.showError(err); }
  };
  dlg.showModal();
}

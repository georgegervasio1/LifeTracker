// Travel → Apartments: Zillow-style listing cards with photos, saved spots (ranked, rated, with notes), one map of
// every spot with a Google My Maps export, and the weekly digest emails. travel.js routes here.

let H;            // helpers from travel.js: { api, $, esc, toast, showError, usd0, tileLayer, authFetch }
let A = null;     // /api/apartments
let filter = { show: "active", zone: true, sort: "pick" };
let map = null, wishSort = null;
const blobs = {};                     // saved-photo id -> object URL

export function initApartments(h) { H = h; }

const yr = new Date().getFullYear();
function fd(d, long) {
  if (!d) return "";
  const x = new Date(d.slice(0, 10) + "T00:00");
  return x.toLocaleDateString("en-US", { month: long ? "long" : "short", day: "numeric", ...(x.getFullYear() !== yr ? { year: "numeric" } : {}) });
}
const money = (v) => (v == null ? "—" : H.usd0.format(v));
const STATUS = { new: "Not saved", saved: "Saved", touring: "Touring", applied: "Applied", passed: "Passed" };
const ON_LIST = ["saved", "touring", "applied"];

export async function loadApartments(tab, root) {
  if (!A) root.innerHTML = `<p class="empty">Loading listings…</p>`;
  A = await H.api("/api/apartments");
  render(tab, root);
}

function set(r, tab, root) { A = r; render(tab, root); }
let cur = { tab: "listings", root: null };
const rerender = () => render(cur.tab, cur.root);

// ---------------------------------------------------------------- pieces
function facts(a) {
  return [a.beds != null ? `${+a.beds} bd` : "", a.baths != null ? `${+a.baths} ba` : "", a.sqft ? `${a.sqft.toLocaleString("en-US")} sq ft` : ""]
    .filter(Boolean).join(" · ");
}
function stars(n, id, size = "") {
  return `<span class="stars ${size}" ${id ? `data-rate="${id}"` : ""} role="${id ? "radiogroup" : "img"}" aria-label="${n ? `${n} of 5 stars` : "Not rated"}">${
    [1, 2, 3, 4, 5].map((i) => `<span class="star ${i <= (n || 0) ? "on" : ""}" ${id ? `data-n="${i}" role="radio" aria-checked="${i === n}" tabindex="0"` : ""}>★</span>`).join("")}</span>`;
}
function badges(a) {
  return [a.private ? `<span class="pill pill-plain">Private</span>` : "",
    a.is_new && a.status === "new" ? `<span class="pill pill-on_track">New</span>` : "",
    a.price_change < 0 ? `<span class="pill pill-met">Price cut ${money(-a.price_change)}</span>` : a.price_change > 0 ? `<span class="pill pill-warn">Up ${money(a.price_change)}</span>` : "",
    a.fit === "stretch" ? `<span class="pill pill-warn">Stretch</span>` : a.fit === "over" ? `<span class="pill pill-missing">Over budget</span>` : "",
    a.big ? `<span class="pill pill-plain">Large</span>` : ""].join("");
}
function where(a) {
  const stop = a.nearest_stop ? `${a.nearest_stop}${a.stop_mi != null ? ` ${a.stop_mi} mi` : ""}` : "";
  const av = a.available ? `Available ${/^\d{4}-\d{2}-\d{2}$/.test(a.available) ? fd(a.available) : a.available}` : "";
  return [a.neighborhood, stop, av].filter(Boolean).join(" · ");
}
function photo(a, n = 0, cls = "") {
  const u = (a.photos || [])[n];
  if (!u) return `<div class="apt-nophoto ${cls}"><span>No photos yet</span></div>`;
  const copy = (a.photo_copies || [])[n] || "";
  return `<img class="${cls}" src="${H.esc(u)}" alt="" loading="lazy" referrerpolicy="no-referrer" data-copy="${copy}" />`;
}
function gmaps(a) {
  const q = a.lat != null ? `${a.lat},${a.lon}` : `${a.address || a.title || ""}${A.criteria?.city ? `, ${A.criteria.city}` : ""}`;
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(q)}`;
}

// A photo that fails to load (listing taken down, host blocks it) falls back to the copy saved in LifeTracker.
function wirePhotos(root) {
  root.querySelectorAll("img[data-copy]").forEach((img) => {
    img.onerror = async () => {
      const pid = img.dataset.copy;
      img.onerror = null;
      if (!pid) return img.replaceWith(Object.assign(document.createElement("div"), { className: `apt-nophoto ${img.className}`, innerHTML: "<span>Photo unavailable</span>" }));
      try {
        if (!blobs[pid]) {
          const res = await H.authFetch(`/api/apartments/photo/${pid}`);
          if (!res.ok) throw new Error();
          blobs[pid] = URL.createObjectURL(await res.blob());
        }
        img.src = blobs[pid];
      } catch { img.replaceWith(Object.assign(document.createElement("div"), { className: `apt-nophoto ${img.className}`, innerHTML: "<span>Photo unavailable</span>" })); }
    };
  });
}

function card(a) {
  const saved = ON_LIST.includes(a.status);
  return `<article class="zcard ${a.status === "passed" ? "zcard-passed" : ""}" data-id="${a.id}">
    <div class="zcard-media">${photo(a, 0, "zcard-img")}
      <button class="zheart ${saved ? "on" : ""}" data-heart="${a.id}" aria-label="${saved ? "Remove from saved" : "Save"}" aria-pressed="${saved}">
        <svg viewBox="0 0 24 24" width="20" height="20"><path d="M12 20.5s-7.5-4.6-9.3-9.2C1.4 7.9 3.6 4.5 7 4.5c2 0 3.6 1.1 5 3 1.4-1.9 3-3 5-3 3.4 0 5.6 3.4 4.3 6.8C19.5 15.9 12 20.5 12 20.5z"/></svg>
      </button>
      ${(a.photos || []).length > 1 ? `<span class="zcount">${a.photos.length} photos</span>` : ""}
    </div>
    <div class="zcard-body">
      <div class="zprice">${a.rent != null ? `${money(a.rent)}<small>/mo</small>` : `<span class="muted">Rent not listed</span>`}</div>
      <div class="zfacts">${H.esc(facts(a)) || "&nbsp;"}</div>
      <div class="zaddr">${H.esc(a.address || a.title)}</div>
      <div class="zwhere muted">${H.esc(where(a)) || "&nbsp;"}</div>
      <div class="zfoot"><span class="zbadges">${badges(a)}</span>${a.rating ? stars(a.rating, null, "sm") : ""}</div>
    </div>
  </article>`;
}

// ---------------------------------------------------------------- page
function head() {
  const C = A.criteria || {}, d = A.digests[0];
  const crit = [C.max_rent != null ? `Up to ${money(C.max_rent)}${C.stretch_rent != null ? `, stretch ${money(C.stretch_rent)}` : ""}` : "",
    C.min_beds != null ? `${C.min_beds}+ beds` : "", C.move_in ? `Move in ${fd(C.move_in)}` : ""].filter(Boolean).join(" · ");
  return `<div class="tr-head"><div><p class="eyebrow">${C.city ? H.esc(C.city) : "Apartment search"}</p>
      <p class="lede-sm">${d ? `Latest email ${fd(d.date)}: ${d.new} new${d.price_drops ? `, ${d.price_drops} price cut${d.price_drops === 1 ? "" : "s"}` : ""}` : "No digest yet"}${crit ? ` · ${H.esc(crit)}` : ""}</p></div>
    <div class="goals-actions"><label class="btn btn-secondary btn-sm">Import file<input type="file" id="aptImport" accept=".json" hidden /></label>
      <button class="btn btn-secondary btn-sm" id="aptCrit">Criteria</button></div></div>`;
}

function render(tab, root) {
  cur = { tab, root };
  if (!A.listings.length) {
    root.innerHTML = head() + `<div class="card empty-state"><h3>No listings yet</h3><p>Listings from your weekly apartment emails land here with photos, so you can save, rate and map the best ones. You can also import a listings file.</p>
      <div class="empty-actions"><button class="btn btn-secondary" id="aptCrit2">Set your criteria</button></div></div>`;
  } else if (tab === "saved") root.innerHTML = head() + savedView();
  else if (tab === "map") root.innerHTML = head() + mapView();
  else if (tab === "emails") root.innerHTML = head() + emailsView();
  else root.innerHTML = head() + listingsView();
  bind(root);
}

function shownListings() {
  const S = { pick: (a, b) => (b.pick === true) - (a.pick === true) || (b.sqft || 0) - (a.sqft || 0),
              sqft: (a, b) => (b.sqft || 0) - (a.sqft || 0), rent: (a, b) => (a.rent ?? 1e9) - (b.rent ?? 1e9),
              newest: (a, b) => (b.first_seen || "").localeCompare(a.first_seen || ""), rating: (a, b) => (b.rating || 0) - (a.rating || 0) };
  return A.listings.filter((a) => (filter.show === "passed" ? a.status === "passed" : filter.show === "new" ? a.is_new && a.status === "new" : a.status !== "passed")
    && (!filter.zone || a.in_zone !== false)).sort(S[filter.sort] || S.pick);
}

function listingsView() {
  const rows = shownListings(), out = A.listings.filter((a) => a.in_zone === false).length;
  const seg = (v, label) => `<button class="seg ${filter.show === v ? "seg-on" : ""}" data-show="${v}">${label}</button>`;
  return `<div class="apt-bar"><div class="seg-row">${seg("active", "All")}${seg("new", "New this week")}${seg("passed", "Passed")}
      ${out ? `<label class="apt-zone"><input type="checkbox" id="aptZone" ${filter.zone ? "checked" : ""}/> In my area only</label>` : ""}</div>
    <select class="select" id="aptSort" aria-label="Sort">${[["pick", "Suggested first"], ["sqft", "Largest first"], ["rent", "Lowest rent"], ["newest", "Newest"], ["rating", "Your rating"]]
      .map(([v, l]) => `<option value="${v}" ${filter.sort === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    <p class="muted apt-count">${rows.length} listing${rows.length === 1 ? "" : "s"}</p>
    ${rows.length ? `<div class="zgrid">${rows.map(card).join("")}</div>` : `<p class="empty">Nothing here.</p>`}`;
}

function savedView() {
  const byId = Object.fromEntries(A.listings.map((a) => [a.id, a]));
  const wish = A.wishlist.map((id) => byId[id]).filter(Boolean);
  if (!wish.length) return `<div class="card empty-state"><h3>Nothing saved yet</h3><p>Tap the heart on a listing to save it. Saved spots get a rank, stars and notes, and show on the map.</p></div>`;
  return `<div class="apt-bar"><p class="muted">Drag the numbers to rank. Ratings and notes are yours only.</p>
      <button class="btn btn-secondary btn-sm" data-kml="saved">Export to Google My Maps</button></div>
    <div class="apt-wishlist" id="aptWish">${wish.map((a, i) => `<div class="card swish" data-id="${a.id}">
      <span class="apt-rank drag" title="Drag to reorder">${i + 1}</span>
      <div class="swish-img" data-open="${a.id}">${photo(a, 0, "swish-photo")}</div>
      <div class="swish-main">
        <div class="apt-title"><button class="linklike" data-open="${a.id}">${H.esc(a.address || a.title)}</button>${badges({ ...a, is_new: false })}</div>
        <div class="apt-facts">${a.rent != null ? `${money(a.rent)}/mo · ` : ""}${H.esc(facts(a))}</div>
        <div class="muted apt-where">${H.esc(where(a))}</div>
        <div class="swish-row">${stars(a.rating, a.id)}
          <select class="select apt-status" data-status="${a.id}" aria-label="Status">${["saved", "touring", "applied", "passed"].map((s) =>
            `<option value="${s}" ${a.status === s ? "selected" : ""}>${STATUS[s]}</option>`).join("")}</select>
          <a class="link-sm" href="${gmaps(a)}" target="_blank" rel="noopener">Google Maps</a>
          ${a.url ? `<a class="link-sm" href="${H.esc(a.url)}" target="_blank" rel="noopener">Listing</a>` : ""}</div>
        <textarea class="input apt-notes" rows="1" placeholder="Notes: questions, tour time, what you liked" data-notes="${a.id}">${H.esc(a.notes || "")}</textarea>
      </div></div>`).join("")}</div>`;
}

function mapView() {
  const pts = A.listings.filter((a) => a.lat != null && a.status !== "passed" && (!filter.zone || a.in_zone !== false));
  const saved = pts.filter((a) => ON_LIST.includes(a.status)), missing = A.listings.filter((a) => a.lat == null && a.status !== "passed").length;
  return `<div class="apt-bar"><p class="muted">${saved.length} saved and ${pts.length - saved.length} other spot${pts.length - saved.length === 1 ? "" : "s"}${missing ? ` · ${missing} without a map location` : ""}</p>
      <div class="goals-actions"><button class="btn btn-secondary btn-sm" data-kml="all">Export all</button><button class="btn btn-primary btn-sm" data-kml="saved">Export saved to Google My Maps</button></div></div>
    <div class="card card-flush apt-map-card"><div id="aptMap" class="apt-map apt-map-lg"></div></div>
    <p class="hint">Saved spots are filled in, with their stars. In Google My Maps (mymaps.google.com) choose Create a new map, then Import, and pick the file; the map then shows in the Google Maps app under Saved, Maps. Private spots are never exported.</p>
    <div class="map-list">${saved.map((a) => `<button class="map-li" data-focus="${a.id}"><span>${H.esc(a.address || a.title)}</span>${stars(a.rating, null, "sm")}</button>`).join("")}</div>`;
}

function emailsView() {
  const byId = Object.fromEntries(A.listings.map((a) => [a.id, a]));
  return A.digests.map((d, i) => `<details class="card digest" ${i === 0 ? "open" : ""}>
    <summary><div><div class="digest-subj">${H.esc(d.subject || `Listings received ${fd(d.date)}`)}</div>
      <div class="muted">${fd(d.date, true)} · ${d.new || 0} new${d.updated ? `, ${d.updated} seen before` : ""}${d.price_drops ? ` · ${d.price_drops} price cut${d.price_drops === 1 ? "" : "s"}` : ""}</div></div></summary>
    ${d.body ? `<div class="digest-body">${H.esc(d.body)}</div>` : ""}
    ${(d.listing_ids || []).length ? `<div class="zgrid zgrid-sm">${d.listing_ids.map((id) => byId[id]).filter(Boolean).map(card).join("")}</div>` : ""}
  </details>`).join("") || `<p class="empty">No emails yet.</p>`;
}

// ---------------------------------------------------------------- behaviour
async function patch(id, body, quiet) {
  try { const r = await H.api(`/api/apartments/${id}`, { method: "PATCH", body: JSON.stringify(body) }); A = r; if (!quiet) rerender(); return r; }
  catch (e) { H.showError(e); }
}

function bind(root) {
  wirePhotos(root);
  H.$("aptImport").onchange = importFile;
  H.$("aptCrit").onclick = openCriteria;
  H.$("aptCrit2")?.addEventListener("click", openCriteria);
  root.querySelectorAll("[data-show]").forEach((b) => (b.onclick = () => { filter.show = b.dataset.show; rerender(); }));
  H.$("aptZone")?.addEventListener("change", (e) => { filter.zone = e.target.checked; rerender(); });
  H.$("aptSort")?.addEventListener("change", (e) => { filter.sort = e.target.value; rerender(); });
  root.querySelectorAll(".zcard").forEach((el) => (el.onclick = (e) => { if (!e.target.closest("[data-heart]")) openDetail(el.dataset.id); }));
  root.querySelectorAll("[data-heart]").forEach((b) => (b.onclick = (e) => {
    e.stopPropagation();
    const a = A.listings.find((x) => x.id === b.dataset.heart), on = ON_LIST.includes(a.status);
    patch(a.id, on ? { status: "new" } : { status: "saved", rank: A.wishlist.length }).then(() => H.toast(on ? "Removed from saved" : "Saved"));
  }));
  root.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => openDetail(b.dataset.open)));
  root.querySelectorAll("[data-rate]").forEach(bindStars);
  root.querySelectorAll("[data-status]").forEach((s) => (s.onchange = () => patch(s.dataset.status, { status: s.value })));
  root.querySelectorAll("[data-notes]").forEach((ta) => {
    const grow = () => { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; };
    grow(); ta.oninput = grow;
    ta.onchange = () => patch(ta.dataset.notes, { notes: ta.value }, true).then((r) => r && H.toast("Notes saved"));
  });
  root.querySelectorAll("[data-kml]").forEach((b) => (b.onclick = () => exportKml(b.dataset.kml)));
  wishSort?.destroy(); wishSort = null;
  if (H.$("aptWish") && window.Sortable) {
    wishSort = Sortable.create(H.$("aptWish"), { handle: ".apt-rank", animation: 150, onEnd: () => {
      const ids = [...H.$("aptWish").children].map((el) => el.dataset.id);
      H.api("/api/apartments/order", { method: "PUT", body: JSON.stringify({ ids }) }).then((r) => { A = r; rerender(); }).catch(H.showError);
    } });
  }
  drawMap(root);
}

function bindStars(el) {
  const id = el.dataset.rate;
  el.querySelectorAll("[data-n]").forEach((s) => {
    const go = () => { const a = A.listings.find((x) => x.id === id), n = +s.dataset.n; patch(id, { rating: a.rating === n ? null : n }); };
    s.onclick = go;
    s.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } };
  });
}

function drawMap(root) {
  map?.remove(); map = null;
  const el = root.querySelector("#aptMap");
  if (!el || !window.L) return;
  const pts = A.listings.filter((a) => a.lat != null && a.status !== "passed" && (!filter.zone || a.in_zone !== false));
  if (!pts.length) return;
  map = L.map(el, { scrollWheelZoom: false });
  H.tileLayer().addTo(map);
  const accent = getComputedStyle(document.body).getPropertyValue("--accent").trim(), grey = getComputedStyle(document.body).getPropertyValue("--text-3").trim();
  const marks = {};
  for (const a of pts) {
    const on = ON_LIST.includes(a.status);
    const icon = L.divIcon({ className: "", iconSize: null, html: `<div class="zpin ${on ? "on" : ""}">${a.rent != null ? `$${(a.rent / 1000).toFixed(1)}k` : "•"}${on && a.rating ? ` ${"★".repeat(a.rating)}` : ""}</div>` });
    marks[a.id] = L.marker([a.lat, a.lon], { icon, zIndexOffset: on ? 1000 : 0 }).addTo(map)
      .bindPopup(`<strong>${H.esc(a.address || a.title)}</strong><br>${a.rent != null ? `${money(a.rent)}/mo · ` : ""}${H.esc(facts(a))}<br><a href="${gmaps(a)}" target="_blank" rel="noopener">Open in Google Maps</a>`);
  }
  map.fitBounds(pts.map((a) => [a.lat, a.lon]), { padding: [30, 30], maxZoom: 15 });
  root.querySelectorAll("[data-focus]").forEach((b) => (b.onclick = () => { const m = marks[b.dataset.focus]; if (m) { map.setView(m.getLatLng(), 16); m.openPopup(); } }));
  void accent; void grey;
}

async function exportKml(scope) {
  try {
    const res = await H.authFetch(`/api/apartments/export.kml?scope=${scope}`);
    if (!res.ok) throw new Error("Export failed");
    const url = URL.createObjectURL(await res.blob());
    Object.assign(document.createElement("a"), { href: url, download: `apartments-${scope}.kml` }).click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    H.toast("Map file downloaded");
  } catch (e) { H.showError(e); }
}

function dialog() {
  let d = document.getElementById("aptDetail");
  if (!d) { d = Object.assign(document.createElement("dialog"), { id: "aptDetail", className: "dialog dialog-wide" }); document.body.append(d); }
  return d;
}

function openDetail(id) {
  const a = A.listings.find((x) => x.id === id);
  if (!a) return;
  const d = dialog(), ph = a.photos || [];
  const hist = [...(a.price_history || [])];
  const seenIn = A.digests.filter((g) => (g.listing_ids || []).includes(a.id));
  d.innerHTML = `<div class="zdetail">
    <div class="zgallery">${ph.length ? `<div class="zmain">${photo(a, 0, "zmain-img")}</div>
      ${ph.length > 1 ? `<div class="zthumbs">${ph.map((_, n) => `<button class="zthumb ${n ? "" : "on"}" data-n="${n}">${photo(a, n, "zthumb-img")}</button>`).join("")}</div>` : ""}`
      : `<div class="apt-nophoto zmain-img"><span>No photos yet</span></div>`}</div>
    <div class="zinfo">
      <div class="zprice zl">${a.rent != null ? `${money(a.rent)}<small>/mo</small>` : "Rent not listed"}</div>
      <div class="zfacts zl">${H.esc(facts(a))}</div>
      <div class="zaddr zl">${H.esc(a.address || a.title)}</div>
      <div class="muted">${H.esc(where(a))}</div>
      <div class="zbadges">${badges(a)}</div>
      <div class="zactions">
        <select class="select" id="zStatus" aria-label="Status">${Object.keys(STATUS).map((s) => `<option value="${s}" ${a.status === s ? "selected" : ""}>${STATUS[s]}</option>`).join("")}</select>
        ${stars(a.rating, a.id, "big")}
      </div>
      <div class="zlinks">${a.url ? `<a class="btn btn-primary btn-sm" href="${H.esc(a.url)}" target="_blank" rel="noopener">Open listing</a>` : ""}
        <a class="btn btn-secondary btn-sm" href="${gmaps(a)}" target="_blank" rel="noopener">Google Maps</a></div>
      ${a.description ? `<p class="zdesc">${H.esc(a.description)}</p>` : ""}
      ${a.context ? `<p class="hint">${H.esc(a.context)}</p>` : ""}
      ${a.amenities?.length ? `<div class="apt-chips">${a.amenities.map((x) => `<span class="apt-chip">${H.esc(x)}</span>`).join("")}</div>` : ""}
      ${a.fees ? `<p class="muted zsmall">Fees: ${H.esc(a.fees)}</p>` : ""}
      ${hist.length ? `<div class="zsection"><h4>Price history</h4>${hist.map((h) => `<div class="bk-li"><span>${fd(h.date, true)}</span><span>${money(h.rent)}</span></div>`).join("")}
        <div class="bk-li"><span>Now</span><span>${money(a.rent)}</span></div></div>` : ""}
      <div class="zsection"><h4>Notes</h4><textarea class="input apt-notes" rows="3" id="zNotes" placeholder="Questions, tour time, what you liked">${H.esc(a.notes || "")}</textarea></div>
      <p class="muted zsmall">First seen ${fd(a.first_seen, true)}${seenIn.length ? ` · in ${seenIn.length} email${seenIn.length === 1 ? "" : "s"}` : ""}${a.source ? ` · ${H.esc(a.source)}` : ""}</p>
      <div class="dialog-actions"><button class="btn btn-secondary" id="zClose">Close</button>
        ${a.photos?.length && (a.photo_copies || []).filter(Boolean).length < a.photos.length ? `<button class="text-btn" id="zSave">Save photo copies</button>` : ""}</div>
    </div></div>`;
  wirePhotos(d);
  d.querySelectorAll(".zthumb").forEach((b) => (b.onclick = () => {
    d.querySelectorAll(".zthumb").forEach((x) => x.classList.toggle("on", x === b));
    d.querySelector(".zmain").innerHTML = photo(a, +b.dataset.n, "zmain-img"); wirePhotos(d.querySelector(".zmain"));
  }));
  d.querySelector("#zStatus").onchange = (e) => patch(a.id, { status: e.target.value }).then(() => openDetail(a.id));
  d.querySelectorAll("[data-rate]").forEach((el) => el.querySelectorAll("[data-n]").forEach((s) => (s.onclick = () =>
    patch(a.id, { rating: a.rating === +s.dataset.n ? null : +s.dataset.n }).then(() => openDetail(a.id)))));
  d.querySelector("#zNotes").onchange = (e) => patch(a.id, { notes: e.target.value }, true).then((r) => r && H.toast("Notes saved"));
  d.querySelector("#zClose").onclick = () => { d.close(); rerender(); };
  d.querySelector("#zSave")?.addEventListener("click", async () => {
    try { A = await H.api(`/api/apartments/${a.id}/photos/save`, { method: "POST" }); H.toast("Photo copies saved"); openDetail(a.id); } catch (e) { H.showError(e); }
  });
  if (!d.open) d.showModal();
}

async function importFile(ev) {
  const file = ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  try {
    let body = JSON.parse(await file.text());
    if (Array.isArray(body)) body = { listings: body };
    const batches = Array.isArray(body.batches) ? body.batches : [body];
    let added = 0, updated = 0;
    H.toast("Importing and saving photos…");
    for (const b of batches) {
      const r = await H.api("/api/apartments/import", { method: "POST", body: JSON.stringify(b) });
      added += r.added; updated += r.updated; A = r;
    }
    if (body.criteria) A = await H.api("/api/apartments/criteria", { method: "PUT", body: JSON.stringify(body.criteria) });
    if (Array.isArray(body.user)) for (const u of body.user) {
      const a = A.listings.find((x) => (x.address || "").toLowerCase() === (u.address || "").toLowerCase());
      if (a && a.status === "new") A = await H.api(`/api/apartments/${a.id}`, { method: "PATCH", body: JSON.stringify(u.set || {}) });
    }
    rerender();
    H.toast(`Imported: ${added} new, ${updated} updated`);
  } catch (e) { H.showError(e instanceof SyntaxError ? new Error("That file isn't valid JSON.") : e); }
}

function openCriteria() {
  const dlg = H.$("aptDialog"), f = dlg.querySelector("form"), C = A.criteria || {};
  for (const k of ["city", "max_rent", "stretch_rent", "min_beds", "big_sqft", "move_in"]) f.elements[k].value = C[k] ?? "";
  f.onsubmit = async (e) => {
    e.preventDefault();
    if (e.submitter?.value === "cancel") return dlg.close();
    try {
      A = await H.api("/api/apartments/criteria", { method: "PUT", body: JSON.stringify(Object.fromEntries(new FormData(f))) });
      dlg.close(); rerender(); H.toast("Criteria saved");
    } catch (err) { H.showError(err); }
  };
  dlg.showModal();
}

export function apartmentsStat() {
  if (!A) return null;
  const saved = A.wishlist.length, d = A.digests[0];
  return { big: String(saved), unit: saved === 1 ? "saved" : "saved", line: d ? `Latest email ${fd(d.date)}: ${d.new} new` : `${A.listings.length} listings` };
}
export async function apartmentsData() { A = await H.api("/api/apartments"); return A; }

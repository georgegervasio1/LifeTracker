// Travel → Trips: vacations with dates and places, bookings you add, and searches that open Google Flights,
// Airbnb, Google Hotels and your airlines already filled in. A foundation; nothing here books or pays.
let H, data = null, root = null;

const KIND = { flight: "Flight", stay: "Stay", car: "Car", other: "Other" };
const fd = (s, y) => (s ? new Date(s + "T00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", ...(y ? { year: "numeric" } : {}) }) : "");

export function initTrips(helpers) { H = helpers; }

export async function tripsData() {
  if (!data) data = await H.api("/api/trips");
  return data;
}

export async function loadTrips(area) {
  root = area;
  if (!data) area.innerHTML = `<p class="empty">Loading trips…</p>`;
  data = await H.api("/api/trips");
  render();
}

function span(t) {
  if (!t.start) return "Dates not set";
  const sameYear = t.end && t.start.slice(0, 4) === t.end.slice(0, 4);
  return t.end && t.end !== t.start ? `${fd(t.start, !sameYear)} to ${fd(t.end, true)}` : fd(t.start, true);
}

function when(t) {
  if (t.when === "now") return `<span class="tp-when now">Happening now</span>`;
  if (t.when === "upcoming") return `<span class="tp-when">${t.days_until === 0 ? "Today" : t.days_until === 1 ? "Tomorrow" : `In ${t.days_until} days`}</span>`;
  if (t.when === "someday") return `<span class="tp-when muted">Someday</span>`;
  return "";
}

function booking(b) {
  const line = b.kind === "flight"
    ? [b.airline, b.flight_no, b.from && b.to ? `${b.from} to ${b.to}` : b.from || b.to].filter(Boolean).join(" · ")
    : b.title || KIND[b.kind];
  const sub = [b.date ? fd(b.date) + (b.end_date ? ` to ${fd(b.end_date)}` : "") : "", b.time, b.confirmation ? `Conf. ${b.confirmation}` : ""].filter(Boolean).join(" · ");
  const links = [b.link ? `<a class="link-sm" href="${H.esc(b.link)}" target="_blank" rel="noopener">Open</a>` : "",
    b.manage ? `<a class="link-sm" href="${H.esc(b.manage)}" target="_blank" rel="noopener">Manage on ${H.esc(b.airline)}</a>` : ""].join("");
  return `<div class="tp-bk"><span class="tp-kind">${KIND[b.kind]}</span>
    <div class="tp-bk-main"><div>${H.esc(b.kind === "flight" && b.title ? b.title : line)}</div>
      <div class="muted">${H.esc(b.kind === "flight" && b.title ? [line, sub].filter(Boolean).join(" · ") : sub)}</div></div>
    <div class="tp-bk-side">${b.cost != null ? `<span>${H.usd.format(b.cost)}</span>` : ""}${links}</div></div>`;
}

function card(t) {
  const facts = [span(t), t.nights != null ? `${t.nights} night${t.nights === 1 ? "" : "s"}` : "", t.travelers > 1 ? `${t.travelers} travelers` : ""].filter(Boolean).join(" · ");
  const search = t.links.filter((l) => l.kind !== "airline"), air = t.links.filter((l) => l.kind === "airline");
  return `<div class="card tp-card ${t.when === "past" ? "tp-past" : ""}">
    <div class="bk-top"><div><p class="eyebrow">${H.esc((t.places || []).join(" · ") || "No place yet")}</p>
      <h3 class="bk-name">${H.esc(t.name)}</h3><p class="muted">${H.esc(facts)}</p></div>
      <div class="tp-right">${when(t)}<button class="text-btn" data-edit="${H.esc(t.id)}">Edit</button></div></div>
    ${t.when !== "past" ? (search.length || air.length ? `<div class="tp-links">${search.map((l) => `<a class="btn btn-secondary btn-sm" href="${H.esc(l.url)}" target="_blank" rel="noopener">${H.esc(l.label)}</a>`).join("")}
      ${air.map((l) => `<a class="btn btn-secondary btn-sm" href="${H.esc(l.url)}" target="_blank" rel="noopener">${H.esc(l.label)}</a>`).join("")}</div>`
      : `<p class="hint">Add a place to search flights and stays.</p>`) : ""}
    ${(t.bookings || []).length ? `<div class="tp-bks">${t.bookings.map(booking).join("")}</div>` : ""}
    ${t.cost ? `<p class="tp-cost"><span class="muted">Booked so far</span> ${H.usd.format(t.cost)}</p>` : ""}
    ${t.notes ? `<p class="tp-notes">${H.esc(t.notes)}</p>` : ""}
  </div>`;
}

function render() {
  const T = data, live = T.trips.filter((t) => t.when !== "past"), past = T.trips.filter((t) => t.when === "past");
  const S = T.settings || {};
  root.innerHTML = `
    <div class="tr-head"><div><p class="eyebrow">Trips</p><h1 class="title">${live.length ? `${live.length} planned` : "Plan a trip"}</h1>
      <p class="lede-sm">${S.home_airport ? `Flying from ${H.esc(S.home_airport)}` : "Set your home airport so flight searches start there"}${(S.airlines || []).length ? ` · ${H.esc(S.airlines.join(", "))}` : ""}</p></div>
      <div class="goals-actions"><button class="btn btn-secondary btn-sm" id="tpSettings">Airport and airlines</button><button class="btn btn-primary btn-sm" id="tpAdd">Add trip</button></div></div>
    ${live.length ? `<div class="tp-list">${live.map(card).join("")}</div>`
      : `<div class="card empty-state"><h3>No trips yet</h3><p>Add a trip with dates and places. LifeTracker builds Google Flights, Airbnb and Google Hotels searches for it, links your airlines, and keeps the flights and stays you book in one place.</p></div>`}
    ${past.length ? `<h3 class="tp-sub">Past trips</h3><div class="tp-list">${past.map(card).join("")}</div>` : ""}
    <p class="hint">Google Flights, Airbnb and the airlines don't offer booking access to personal apps, so these links open their sites with your trip filled in where the site allows. Nothing here books or pays for anything.</p>`;
  H.$("tpAdd").onclick = () => openEditor(null);
  H.$("tpSettings").onclick = openSettings;
  root.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => openEditor(T.trips.find((t) => t.id === b.dataset.edit))));
}

// ---------------------------------------------------------------- add / edit
function bkRow(b = {}) {
  const v = (k) => H.esc(b[k] ?? "");
  return `<div class="tp-bkrow" data-id="${v("id")}">
    <select class="input" name="kind">${Object.entries(KIND).map(([k, l]) => `<option value="${k}" ${b.kind === k ? "selected" : ""}>${l}</option>`).join("")}</select>
    <input class="input" name="title" placeholder="What (hotel, flight, car)" value="${v("title")}" maxlength="120" />
    <input class="input tp-fl" name="airline" placeholder="Airline" value="${v("airline")}" list="tpAirlines" maxlength="40" />
    <input class="input tp-fl" name="flight_no" placeholder="Flight no." value="${v("flight_no")}" maxlength="12" />
    <input class="input tp-fl" name="from" placeholder="From" value="${v("from")}" maxlength="40" />
    <input class="input tp-fl" name="to" placeholder="To" value="${v("to")}" maxlength="40" />
    <input class="input" type="date" name="date" value="${v("date")}" title="Date" />
    <input class="input tp-st" type="date" name="end_date" value="${v("end_date")}" title="Until" />
    <input class="input" name="time" placeholder="Time" value="${v("time")}" maxlength="8" />
    <input class="input" name="confirmation" placeholder="Confirmation" value="${v("confirmation")}" maxlength="20" />
    <input class="input" name="cost" placeholder="Cost" inputmode="decimal" value="${v("cost")}" />
    <input class="input tp-wide" name="link" placeholder="Link (https://…)" value="${v("link")}" />
    <button type="button" class="text-btn danger" data-rm>Remove</button></div>`;
}

function syncKinds(box) {
  box.querySelectorAll(".tp-bkrow").forEach((r) => {
    const k = r.querySelector("[name=kind]").value;
    r.classList.toggle("is-flight", k === "flight");
    r.classList.toggle("is-stay", k === "stay" || k === "car");
  });
}

function editor() {
  let d = document.getElementById("tripDialog");
  if (d) return d;
  d = Object.assign(document.createElement("dialog"), { id: "tripDialog", className: "dialog dialog-wide" });
  d.innerHTML = `<form class="dialog-form" method="dialog" autocomplete="off"><h3 id="tpTitle">Trip</h3>
    <label class="span-2">Name<input class="input" name="name" required maxlength="120" placeholder="Spring break" /></label>
    <label>Starts<input class="input" type="date" name="start" /></label><label>Ends<input class="input" type="date" name="end" /></label>
    <label>Places<input class="input" name="places" placeholder="Lisbon; Porto" /><span class="hint">Separate with semicolons. The first one is used for searches.</span></label>
    <label>Travelers<input class="input" type="number" name="travelers" min="1" max="16" value="1" /></label>
    <label class="span-2">Notes<textarea class="input" name="notes" rows="3" maxlength="4000"></textarea></label>
    <div class="span-2"><div class="bk-top"><h4 class="tp-sub">Bookings</h4><button type="button" class="btn btn-secondary btn-sm" id="tpAddBk">Add booking</button></div>
      <div id="tpBks"></div><datalist id="tpAirlines"></datalist></div>
    <div class="span-2 dialog-actions"><button type="button" class="text-btn danger" id="tpDelete">Delete trip</button><span style="flex:1"></span>
      <button class="btn btn-secondary" value="cancel" formnovalidate>Cancel</button><button class="btn btn-primary" value="save">Save</button></div></form>`;
  document.body.append(d);
  const box = d.querySelector("#tpBks");
  box.addEventListener("click", (e) => { if (e.target.closest("[data-rm]")) e.target.closest(".tp-bkrow").remove(); });
  box.addEventListener("change", (e) => { if (e.target.name === "kind") syncKinds(box); });
  d.querySelector("#tpAddBk").onclick = () => { box.insertAdjacentHTML("beforeend", bkRow({ kind: "flight" })); syncKinds(box); };
  return d;
}

function openEditor(t) {
  const d = editor(), f = d.querySelector("form"), box = d.querySelector("#tpBks");
  d.querySelector("#tpTitle").textContent = t ? "Edit trip" : "New trip";
  d.querySelector("#tpAirlines").innerHTML = data.airlines.map((a) => `<option value="${H.esc(a)}">`).join("");
  f.elements.name.value = t?.name || ""; f.elements.start.value = t?.start || ""; f.elements.end.value = t?.end || "";
  f.elements.places.value = (t?.places || []).join("; "); f.elements.travelers.value = t?.travelers || 1; f.elements.notes.value = t?.notes || "";
  box.innerHTML = (t?.bookings || []).map(bkRow).join(""); syncKinds(box);
  const del = d.querySelector("#tpDelete");
  del.hidden = !t;
  del.onclick = async () => {
    if (!confirm(`Delete "${t.name}"?`)) return;
    try { data = await H.api(`/api/trips/${t.id}`, { method: "DELETE" }); d.close(); render(); H.toast("Trip deleted"); } catch (e) { H.showError(e); }
  };
  f.onsubmit = async (e) => {
    e.preventDefault();
    if (e.submitter?.value === "cancel") return d.close();
    const fv = Object.fromEntries(new FormData(f));
    const bookings = [...box.querySelectorAll(".tp-bkrow")].map((r) => {
      const o = { id: r.dataset.id || undefined };
      r.querySelectorAll("[name]").forEach((i) => { if (i.value.trim()) o[i.name] = i.value.trim(); });
      if (o.kind !== "flight") ["airline", "flight_no", "from", "to"].forEach((k) => delete o[k]);
      return o;
    });
    const body = { name: fv.name, start: fv.start, end: fv.end, places: fv.places, travelers: fv.travelers, notes: fv.notes, bookings };
    try {
      data = await H.api(t ? `/api/trips/${t.id}` : "/api/trips", { method: t ? "PUT" : "POST", body: JSON.stringify(body) });
      d.close(); render(); H.toast(t ? "Trip saved" : "Trip added");
    } catch (err) { H.showError(err); }
  };
  d.showModal();
}

// ---------------------------------------------------------------- settings: home airport + airlines
function openSettings() {
  let d = document.getElementById("tripSettings");
  if (!d) {
    d = Object.assign(document.createElement("dialog"), { id: "tripSettings", className: "dialog" });
    document.body.append(d);
  }
  const S = data.settings || {}, mine = new Set(S.airlines || []);
  d.innerHTML = `<form class="dialog-form" method="dialog"><h3>Airport and airlines</h3>
    <label class="span-2">Home airport<input class="input" name="home_airport" maxlength="40" placeholder="Airport code, like BOS" value="${H.esc(S.home_airport || "")}" />
      <span class="hint">Flight searches start here.</span></label>
    <fieldset class="span-2 tp-airlines"><legend>Your airlines</legend>${data.airlines.map((a) =>
      `<label class="check"><input type="checkbox" name="airlines" value="${H.esc(a)}" ${mine.has(a) ? "checked" : ""} /> ${H.esc(a)}</label>`).join("")}
      <span class="hint">Each trip gets a link to these airlines, and flights you add on them get a Manage link.</span></fieldset>
    <div class="span-2 dialog-actions"><span style="flex:1"></span><button class="btn btn-secondary" value="cancel" formnovalidate>Cancel</button><button class="btn btn-primary" value="save">Save</button></div></form>`;
  const f = d.querySelector("form");
  f.onsubmit = async (e) => {
    e.preventDefault();
    if (e.submitter?.value === "cancel") return d.close();
    const body = { home_airport: f.elements.home_airport.value, airlines: [...f.querySelectorAll("[name=airlines]:checked")].map((i) => i.value) };
    try { data = await H.api("/api/trips/settings", { method: "PUT", body: JSON.stringify(body) }); d.close(); render(); H.toast("Saved"); }
    catch (err) { H.showError(err); }
  };
  d.showModal();
}

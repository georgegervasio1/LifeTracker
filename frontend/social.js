// Social → Events: Partiful events read from the user's private Partiful calendar link (backend/social.py).
// Partiful has no public API; its calendar sync link is what Google/Apple Calendar subscribe to, and we read the same link.
// Tabs: Upcoming, Past, Connect. An event with a place gets Directions and "Plan a trip" (Travel → Trips).
let H, data = null, root = null;

const TABS = [["upcoming", "Upcoming"], ["past", "Past"], ["connect", "Connect"]];
const RSVP = { going: "Going", maybe: "Maybe", "can't go": "Can't go", invited: "Invited" };
const RSVP_CLS = { going: "pill-met", maybe: "pill-warn", "can't go": "pill-plain", invited: "pill-on_track" };

export function initSocial(helpers) { H = helpers; }

// ---------------------------------------------------------------- time helpers (feed times carry their zone)
const when = (e) => (e.all_day ? new Date(e.start + "T00:00") : new Date(e.start));
const dayKey = (d) => d.toDateString();
function timeText(e) {
  if (e.all_day) return "All day";
  const s = when(e), opt = { hour: "numeric", minute: "2-digit" };
  const end = e.end ? new Date(e.end) : null;
  const a = s.toLocaleTimeString("en-US", opt);
  if (!end) return a;
  return dayKey(end) === dayKey(s) || end - s < 12 * 3600e3 ? `${a} to ${end.toLocaleTimeString("en-US", opt)}` : `${a} to ${end.toLocaleDateString("en-US", { month: "short", day: "numeric" })}`;
}
function relDay(e) {
  const d = Math.round((new Date(dayKey(when(e))) - new Date(dayKey(new Date()))) / 864e5);
  if (d === 0) return "Today";
  if (d === 1) return "Tomorrow";
  if (d > 1 && d < 7) return when(e).toLocaleDateString("en-US", { weekday: "long" });
  return d > 0 ? `In ${d} days` : "";
}
const longDate = (e) => when(e).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: when(e).getFullYear() !== new Date().getFullYear() ? "numeric" : undefined });
const directions = (e) => "https://www.google.com/maps/dir/?" + new URLSearchParams({ api: "1", destination: e.lat != null ? `${e.lat},${e.lon}` : e.location, travelmode: "transit" });
const pill = (e) => (e.rsvp ? `<span class="pill ${RSVP_CLS[e.rsvp] || "pill-plain"}">${RSVP[e.rsvp] || H.esc(e.rsvp)}</span>` : "");

// ---------------------------------------------------------------- entry
export async function renderSocial(parts) {
  const tab = TABS.some(([k]) => k === parts[0]) ? parts[0] : "upcoming";
  H.$("subnav").classList.remove("hidden");
  H.$("subnavTitle").textContent = "Social";
  H.$("subnavTabs").innerHTML = TABS.map(([k, l]) => `<a href="#/social/${k}" class="${k === tab ? "active" : ""}">${l}</a>`).join("");
  H.$("sectionControls").classList.add("hidden");
  document.title = "Social · LifeTracker";
  root = H.$("socialRoot");
  root.dataset.tab = tab;
  if (!data) root.innerHTML = `<p class="empty">Loading events…</p>`;
  data = await H.api("/api/social/events");
  if (root.dataset.tab === tab) draw(tab);
}

function draw(tab) {
  if (tab === "connect") return drawConnect();
  const D = data, list = (tab === "past" ? D.past : D.upcoming);
  const showHidden = H.routeQuery().get("hidden") === "1";
  const rows = list.filter((e) => showHidden || !e.hidden);
  if (!D.connected && !D.upcoming.length && !D.past.length) return drawEmpty();
  const head = tab === "past"
    ? `<div class="tr-head"><div><p class="eyebrow">Past events</p><h1 class="title">${rows.length ? `${rows.length} event${rows.length === 1 ? "" : "s"}` : "None yet"}</h1>
        <p class="lede-sm">Kept here after they leave your Partiful calendar</p></div></div>`
    : `<div class="tr-head"><div><p class="eyebrow">Events</p><h1 class="title">${rows.length ? `${rows.length} coming up` : "Nothing coming up"}</h1>
        <p class="lede-sm">${syncLine(D)}</p></div>
        <div class="goals-actions">${D.connected ? `<button class="btn btn-secondary btn-sm" id="soSync">Sync now</button>` : `<a class="btn btn-primary btn-sm" href="#/social/connect">Connect Partiful</a>`}</div></div>`;
  const feature = tab === "upcoming" && rows.length ? featureCard(rows[0]) : "";
  const rest = tab === "upcoming" ? rows.slice(1) : rows;
  root.innerHTML = `${head}${D.error ? `<p class="hint flag">Last sync failed: ${H.esc(D.error)}</p>` : ""}
    ${feature}
    ${rest.length ? groups(rest, tab) : !rows.length ? `<div class="card empty-state"><h3>${tab === "past" ? "No past events" : "No upcoming events"}</h3>
      <p>${tab === "past" ? "Events you went to or were invited to show up here once they're over." : "When you're invited to or RSVP to a Partiful event, it shows up here after the next sync."}</p></div>` : ""}
    ${D.hidden ? `<p class="hint">${D.hidden} hidden · <a class="link-sm" href="#/social/${tab}${showHidden ? "" : "?hidden=1"}">${showHidden ? "Hide them" : "Show them"}</a></p>` : ""}`;
  const s = H.$("soSync");
  if (s) s.onclick = () => syncNow(s);
  root.querySelectorAll("[data-ev]").forEach((el) => (el.onclick = () => openEvent(el.dataset.ev)));
}

function syncLine(D) {
  if (!D.connected) return "From events you add with an .ics file";
  if (!D.last_sync) return "From your Partiful calendar";
  const t = new Date(D.last_sync);
  return `From your Partiful calendar · synced ${t.toLocaleDateString("en-US", { month: "short", day: "numeric" })} at ${t.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}`;
}

function featureCard(e) {
  const d = when(e);
  return `<button class="card so-feature" data-ev="${H.esc(e.id)}">
      <div class="so-date big"><span>${d.toLocaleDateString("en-US", { month: "short" })}</span><b>${d.getDate()}</b></div>
      <div class="so-main"><p class="eyebrow">${H.esc([relDay(e), timeText(e)].filter(Boolean).join(" · "))}</p>
        <h2 class="title-sm">${H.esc(e.title)}</h2>
        ${e.location ? `<p class="muted">${H.esc(e.location)}</p>` : ""}
        <div class="so-meta">${pill(e)}${e.host ? `<span class="muted">Hosted by ${H.esc(e.host)}</span>` : ""}${e.status === "cancelled" ? `<span class="pill pill-missing">Cancelled</span>` : ""}</div></div>
      <span class="so-go" aria-hidden="true">›</span></button>`;
}

function row(e) {
  const d = when(e);
  const sub = [timeText(e), e.location].filter(Boolean).join(" · ");
  return `<button class="so-row ${e.hidden ? "so-hidden" : ""}" data-ev="${H.esc(e.id)}">
      <span class="so-date"><span>${d.toLocaleDateString("en-US", { weekday: "short" })}</span><b>${d.getDate()}</b></span>
      <span class="so-main"><span class="so-title">${H.esc(e.title)}</span><span class="muted">${H.esc(sub)}</span></span>
      <span class="so-side">${e.status === "cancelled" ? `<span class="pill pill-missing">Cancelled</span>` : pill(e)}${e.in_feed === false && when(e) > new Date() ? `<span class="pill pill-plain">Left your feed</span>` : ""}</span></button>`;
}

function groups(rows, tab) {
  const by = new Map();
  for (const e of rows) {
    const d = when(e), k = d.toLocaleDateString("en-US", { month: "long", year: "numeric" });
    if (!by.has(k)) by.set(k, []);
    by.get(k).push(e);
  }
  return [...by].map(([k, es]) => `<h3 class="tp-sub">${H.esc(k)}</h3><div class="card card-flush so-list">${es.map(row).join("")}</div>`).join("");
}

function drawEmpty() {
  root.innerHTML = `<div class="tr-head"><div><p class="eyebrow">Events</p><h1 class="title">Your Partiful events</h1>
      <p class="lede-sm">Invites, RSVPs and parties you're hosting, next to the rest of your life</p></div></div>
    <div class="card empty-state"><h3>Connect Partiful</h3>
      <p>Partiful doesn't have an API, but it gives you a private calendar link for calendar apps. Paste that link here and LifeTracker reads your events from it.</p>
      <div class="empty-actions"><a class="btn btn-primary" href="#/social/connect">Connect Partiful</a></div></div>`;
}

// ---------------------------------------------------------------- Connect tab
function drawConnect() {
  const D = data, demo = H.isDemo();
  root.innerHTML = `<div class="tr-head"><div><p class="eyebrow">Connect</p><h1 class="title">${D.connected ? "Partiful is connected" : "Connect Partiful"}</h1>
      <p class="lede-sm">${D.connected ? H.esc(syncLine(D)) + (D.last_count != null ? ` · ${D.last_count} event${D.last_count === 1 ? "" : "s"} in the feed` : "") : "Uses the private calendar link Partiful makes for Google, Apple and Outlook Calendar"}</p></div></div>
    ${D.error ? `<p class="hint flag">Last sync failed: ${H.esc(D.error)}</p>` : ""}
    <div class="grid-2">
      <div class="card">
        <h3>${D.connected ? "Replace the calendar link" : "Paste your calendar link"}</h3>
        <ol class="so-steps">
          <li>Open any event on partiful.com and click the calendar icon under the event details.</li>
          <li>Click <b>Google Calendar</b>, then <b>Copy Link</b>. Or right-click <b>Apple Calendar</b> and copy the link address.</li>
          <li>Paste it below. It starts with https:// or webcal://.</li>
        </ol>
        <p class="hint">Partiful's profile → Settings → Calendar sync preferences decides which events are in the link (hosting, going, invited).</p>
        <form id="soForm" class="so-form">
          <input class="input" id="soUrl" type="url" placeholder="webcal://…" autocomplete="off" ${demo ? "disabled" : ""} />
          <button class="btn btn-primary btn-sm" ${demo ? "disabled" : ""}>${D.connected ? "Replace" : "Connect"}</button>
        </form>
        <p class="hint">${demo ? "Connecting is turned off in the demo." : "Treat this link like a password: anyone with it can see your events. LifeTracker keeps it on the server and never shows it again."}</p>
        ${D.connected ? `<div class="dialog-actions"><button class="btn btn-secondary btn-sm" id="soSync">Sync now</button><span style="flex:1"></span>
          <button class="text-btn danger" id="soOff">Disconnect</button></div>` : ""}
      </div>
      <div class="card">
        <h3>Add one event</h3>
        <p>On an event's calendar icon, choose <b>Add this event only</b> and save the .ics file, then add it here. Good for an event that isn't in your calendar link.</p>
        <label class="btn btn-secondary btn-sm so-file">Choose .ics file<input type="file" id="soFile" accept=".ics,text/calendar" multiple hidden /></label>
        <p class="hint">What LifeTracker shows: title, date and time, place, host, the Partiful link, and your RSVP when the calendar includes it. Partiful updates the link about once a day, so a brand-new invite can take a while to appear.</p>
      </div>
    </div>`;
  H.$("soForm").onsubmit = async (ev) => {
    ev.preventDefault();
    const url = H.$("soUrl").value.trim();
    if (!url) return;
    const b = ev.submitter || H.$("soForm").querySelector("button");
    b.disabled = true;
    try {
      data = await H.api("/api/social/partiful", { method: "POST", body: JSON.stringify({ url }) });
      H.toast(`Connected · ${data.sync?.events ?? 0} events`);
      location.hash = "#/social/upcoming";
    } catch (e) { H.showError(e); b.disabled = false; }
  };
  const s = H.$("soSync");
  if (s) s.onclick = () => syncNow(s, "connect");
  const off = H.$("soOff");
  if (off) off.onclick = () => disconnect();
  H.$("soFile").onchange = async (ev) => {
    let added = 0;
    for (const f of ev.target.files) {
      try { data = await H.api("/api/social/import", { method: "POST", body: JSON.stringify({ ics: await f.text() }) }); added += data.added || 0; }
      catch (e) { H.showError(e); }
    }
    ev.target.value = "";
    if (added) H.toast(`Added ${added} event${added === 1 ? "" : "s"}`);
    drawConnect();
  };
}

async function syncNow(btn, tab = "upcoming") {
  btn.disabled = true;
  try { data = await H.api("/api/social/sync", { method: "POST" }); H.toast(`Synced · ${data.sync.events} events`); }
  catch (e) { H.showError(e); data = await H.api("/api/social/events?refresh=0"); }
  draw(tab);
}

function disconnect() {
  const d = document.createElement("dialog");
  d.className = "dialog";
  d.innerHTML = `<div class="dialog-form"><h3>Disconnect Partiful?</h3>
    <p class="span-2 muted">LifeTracker forgets the calendar link. Events it already brought in can stay, or be removed too.</p>
    <div class="span-2 dialog-actions"><button class="btn btn-secondary btn-sm" data-x>Cancel</button><span style="flex:1"></span>
      <button class="btn btn-secondary btn-sm" data-keep>Disconnect, keep events</button><button class="btn btn-primary btn-sm" data-rm>Disconnect and remove</button></div></div>`;
  document.body.append(d);
  const go = async (rm) => {
    try { data = await H.api(`/api/social/partiful${rm ? "?events=1" : ""}`, { method: "DELETE" }); d.close(); drawConnect(); H.toast("Disconnected"); }
    catch (e) { H.showError(e); }
  };
  d.querySelector("[data-x]").onclick = () => d.close();
  d.querySelector("[data-keep]").onclick = () => go(false);
  d.querySelector("[data-rm]").onclick = () => go(true);
  d.addEventListener("close", () => d.remove());
  d.showModal();
}

// ---------------------------------------------------------------- event detail
function openEvent(id) {
  const e = [...data.upcoming, ...data.past].find((x) => x.id === id);
  if (!e) return;
  const future = when(e) > new Date(Date.now() - 6 * 3600e3);
  const d = document.createElement("dialog");
  d.className = "dialog";
  const desc = (e.description || "").replace(/https:\/\/(?:www\.)?partiful\.com\/e\/\S+/g, "").trim();
  d.innerHTML = `<div class="dialog-form so-detail">
    <div class="span-2"><p class="eyebrow">${H.esc(longDate(e))} · ${H.esc(timeText(e))}</p><h3>${H.esc(e.title)}</h3>
      <div class="so-meta">${pill(e)}${e.host ? `<span class="muted">Hosted by ${H.esc(e.host)}</span>` : ""}${e.status === "cancelled" ? `<span class="pill pill-missing">Cancelled</span>` : ""}</div></div>
    ${e.location ? `<div class="span-2"><p class="so-k">Where</p><p>${H.esc(e.location)}</p></div>` : ""}
    ${desc ? `<div class="span-2"><p class="so-k">Details</p><p class="so-desc">${H.esc(desc.slice(0, 1200))}${desc.length > 1200 ? "…" : ""}</p></div>` : ""}
    ${!e.rsvp ? `<p class="span-2 hint">Your RSVP isn't in the calendar link. Open the event on Partiful to see or change it.</p>` : ""}
    <label class="span-2">Notes<textarea class="input" id="soNotes" rows="2" maxlength="2000" placeholder="Bring something, who's going, gift ideas">${H.esc(e.notes || "")}</textarea></label>
    <div class="span-2 so-actions">
      ${e.url ? `<a class="btn btn-primary btn-sm" href="${H.esc(e.url)}" target="_blank" rel="noopener">Open in Partiful</a>` : ""}
      ${e.location && future ? `<a class="btn btn-secondary btn-sm" href="${H.esc(directions(e))}" target="_blank" rel="noopener">Directions</a>` : ""}
      ${e.location && future ? `<button class="btn btn-secondary btn-sm" data-trip>Plan a trip</button>` : ""}
    </div>
    <div class="span-2 dialog-actions"><button class="text-btn" data-hide>${e.hidden ? "Show in lists" : "Hide from lists"}</button>
      ${e.source === "ics_import" ? `<button class="text-btn danger" data-del>Remove</button>` : ""}<span style="flex:1"></span>
      <button class="btn btn-secondary btn-sm" data-close>Done</button></div></div>`;
  document.body.append(d);
  const save = async (patch) => {
    const x = await H.api(`/api/social/events/${e.id}`, { method: "PATCH", body: JSON.stringify(patch) });
    Object.assign(e, x);
  };
  d.querySelector("[data-close]").onclick = async () => {
    const n = d.querySelector("#soNotes").value;
    if (n !== (e.notes || "")) { try { await save({ notes: n }); } catch (err) { H.showError(err); } }
    d.close();
  };
  d.querySelector("[data-hide]").onclick = async () => {
    try { await save({ hidden: !e.hidden }); d.close(); draw(root.dataset.tab); } catch (err) { H.showError(err); }
  };
  const del = d.querySelector("[data-del]");
  if (del) del.onclick = async () => {
    try { data = await H.api(`/api/social/events/${e.id}`, { method: "DELETE" }); d.close(); draw(root.dataset.tab); } catch (err) { H.showError(err); }
  };
  const trip = d.querySelector("[data-trip]");
  if (trip) trip.onclick = async () => {
    // Travel → Trips: the event's city (last parts of the address), dates, and the Partiful link in the notes
    const parts = e.location.split(",").map((s) => s.trim()).filter(Boolean);
    const place = parts.length >= 2 ? parts.slice(-2).join(", ").replace(/\s+\d{5}(-\d{4})?$/, "") : parts[0];
    const iso = (x) => { const y = new Date(x); return `${y.getFullYear()}-${String(y.getMonth() + 1).padStart(2, "0")}-${String(y.getDate()).padStart(2, "0")}`; };
    const start = e.all_day ? e.start : iso(e.start), end = e.end ? (e.all_day ? e.start : iso(e.end)) : start;
    try {
      await H.api("/api/trips", { method: "POST", body: JSON.stringify({ name: e.title, start, end: end < start ? start : end, places: [place],
        notes: [`Partiful event: ${e.title}`, e.location, e.url].filter(Boolean).join("\n") }) });
      d.close(); H.toast("Trip added"); location.hash = "#/travel/trips";
    } catch (err) { H.showError(err); }
  };
  d.addEventListener("close", () => d.remove());
  d.showModal();
}

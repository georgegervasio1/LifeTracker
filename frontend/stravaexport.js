// Strava account export (the zip from Settings → My Account → Download your data) → run records.
// Parsed in the browser because the archive can be hundreds of MB. Reads activities.csv for the
// summary and each run's GPX/TCX file (optionally gzipped) for the route, mile splits and best efforts.
// FIT files (Garmin and similar) aren't parsed: those runs keep their summary without a map.

const MI = 1609.344;
const RUN_TYPES = { "Run": "Run", "Trail Run": "TrailRun", "Virtual Run": "VirtualRun", "TrailRun": "TrailRun", "VirtualRun": "VirtualRun" };
const EFFORTS = [["400m", 400], ["1/2 mile", MI / 2], ["1K", 1000], ["1 mile", MI], ["2 mile", 2 * MI], ["5K", 5000],
  ["10K", 10000], ["15K", 15000], ["10 mile", 10 * MI], ["20K", 20000], ["Half-Marathon", 21097.5], ["30K", 30000], ["Marathon", 42195]];

function loadJSZip() {
  if (window.JSZip) return Promise.resolve(window.JSZip);
  return new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js";
    s.onload = () => ok(window.JSZip);
    s.onerror = () => fail(new Error("Couldn't load the zip reader. Check your connection and try again."));
    document.head.appendChild(s);
  });
}

export function parseCSV(text) {
  const rows = []; let row = [], field = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else q = false; }
      else field += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.length > 1 || row[0] !== "") rows.push(row);
      row = [];
    } else field += c;
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  return rows;
}

// "Oct 20, 2026, 11:00:00 AM" (UTC in Strava's export) → Date
export function parseStravaDate(s) {
  const m = /([A-Za-z]{3})\w* (\d{1,2}), (\d{4}),? (\d{1,2}):(\d{2}):(\d{2})\s*(AM|PM)?/.exec(s || "");
  if (!m) { const d = new Date(s); return isNaN(d) ? null : d; }
  const mon = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].indexOf(m[1].toLowerCase());
  let h = +m[4] % 12; if (m[7] === "PM") h += 12; if (!m[7]) h = +m[4];
  return new Date(Date.UTC(+m[3], mon, +m[2], h, +m[5], +m[6]));
}

const pad = (n) => String(n).padStart(2, "0");
const localIso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;

async function gunzip(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export function parseTrack(xml) {
  const doc = new DOMParser().parseFromString(xml, "application/xml");
  const num = (el, tag) => { const x = el.getElementsByTagNameNS("*", tag)[0] || el.getElementsByTagName(tag)[0]; return x ? parseFloat(x.textContent) : NaN; };
  const txt = (el, tag) => (el.getElementsByTagNameNS("*", tag)[0] || el.getElementsByTagName(tag)[0])?.textContent;
  const pts = [];
  for (const p of doc.getElementsByTagNameNS("*", "trkpt")) {          // GPX
    pts.push({ lat: +p.getAttribute("lat"), lon: +p.getAttribute("lon"), t: Date.parse(txt(p, "time")), ele: num(p, "ele"), hr: num(p, "hr") });
  }
  if (!pts.length) for (const p of doc.getElementsByTagNameNS("*", "Trackpoint")) {   // TCX
    const pos = p.getElementsByTagNameNS("*", "Position")[0];
    const hr = p.getElementsByTagNameNS("*", "HeartRateBpm")[0];
    pts.push({ lat: pos ? num(pos, "LatitudeDegrees") : NaN, lon: pos ? num(pos, "LongitudeDegrees") : NaN, t: Date.parse(txt(p, "Time")),
               ele: num(p, "AltitudeMeters"), hr: hr ? num(hr, "Value") : NaN });
  }
  return pts.filter((p) => isFinite(p.lat) && isFinite(p.lon) && isFinite(p.t));
}

function hav(a, b) {
  const R = 6371008.8, r = Math.PI / 180;
  const dLat = (b.lat - a.lat) * r, dLon = (b.lon - a.lon) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

// Douglas–Peucker on lat/lon, then Google polyline encoding
function simplify(pts, tol) {
  if (pts.length < 3) return pts;
  const keep = new Uint8Array(pts.length); keep[0] = keep[pts.length - 1] = 1;
  const stack = [[0, pts.length - 1]];
  while (stack.length) {
    const [a, b] = stack.pop(); let idx = -1, max = 0;
    const A = pts[a], B = pts[b], dx = B.lon - A.lon, dy = B.lat - A.lat, L = dx * dx + dy * dy;
    for (let i = a + 1; i < b; i++) {
      const P = pts[i];
      const t = L ? Math.max(0, Math.min(1, ((P.lon - A.lon) * dx + (P.lat - A.lat) * dy) / L)) : 0;
      const d = (P.lon - A.lon - t * dx) ** 2 + (P.lat - A.lat - t * dy) ** 2;
      if (d > max) { max = d; idx = i; }
    }
    if (idx > 0 && max > tol * tol) { keep[idx] = 1; stack.push([a, idx], [idx, b]); }
  }
  return pts.filter((_, i) => keep[i]);
}
export function encodePolyline(pts) {
  let out = "", pl = 0, pg = 0;
  const enc = (v) => { v = v < 0 ? ~(v << 1) : v << 1; let s = ""; while (v >= 0x20) { s += String.fromCharCode((0x20 | (v & 0x1f)) + 63); v >>= 5; } return s + String.fromCharCode(v + 63); };
  for (const p of pts) {
    const la = Math.round(p.lat * 1e5), lo = Math.round(p.lon * 1e5);
    out += enc(la - pl) + enc(lo - pg); pl = la; pg = lo;
  }
  return out;
}

// Route, mile splits (moving time), best efforts (elapsed time) from track points.
export function analyzeTrack(pts, csvDist) {
  const cum = [0]; let moving = 0; const mov = [0];
  for (let i = 1; i < pts.length; i++) {
    const d = hav(pts[i - 1], pts[i]), dt = (pts[i].t - pts[i - 1].t) / 1000;
    cum.push(cum[i - 1] + d);
    if (dt > 0 && dt <= 60 && d / dt > 0.5) moving += dt;        // stopped or paused segments don't count
    mov.push(moving);
  }
  const total = cum[cum.length - 1];
  const k = csvDist && total ? csvDist / total : 1;                // line splits up with Strava's distance
  for (let i = 0; i < cum.length; i++) cum[i] *= k;
  const splits = []; let start = 0;
  const flush = (end, n) => {
    const seg = pts.slice(start, end + 1), hrs = seg.map((p) => p.hr).filter(isFinite);
    const e0 = pts[start].ele, e1 = pts[end].ele;
    splits.push({ n, distance_m: +(cum[end] - cum[start]).toFixed(1), moving_s: Math.round(mov[end] - mov[start]),
      elev_m: isFinite(e0) && isFinite(e1) ? +(e1 - e0).toFixed(1) : null,
      avg_hr: hrs.length ? +(hrs.reduce((a, b) => a + b, 0) / hrs.length).toFixed(1) : null });
    start = end;
  };
  for (let i = 1, mile = 1; i < pts.length; i++) if (cum[i] >= mile * MI) { flush(i, mile); mile++; }
  if (cum[cum.length - 1] - cum[start] > 50) flush(pts.length - 1, splits.length + 1);
  const efforts = [];
  for (const [name, D] of EFFORTS) {
    if (cum[cum.length - 1] < D) break;
    let best = Infinity;
    for (let i = 0, j = 0; i < pts.length; i++) {
      while (j < pts.length && cum[j] - cum[i] < D) j++;
      if (j >= pts.length) break;
      best = Math.min(best, (pts[j].t - pts[i].t) / 1000);
    }
    if (isFinite(best)) efforts.push({ name, elapsed_s: Math.round(best), distance_m: D });
  }
  let line = simplify(pts, 0.00004);
  for (let tol = 0.00008; line.length > 1500; tol *= 2) line = simplify(pts, tol);
  return { polyline: encodePolyline(line), splits, best_efforts: efforts, track_m: total, moving_s: Math.round(moving),
           hr_max: Math.max(0, ...pts.map((p) => p.hr).filter(isFinite)) || null, start: new Date(pts[0].t) };
}

export async function parseStravaExport(file, onProgress = () => {}) {
  const JSZip = await loadJSZip();
  const zip = await JSZip.loadAsync(file);
  const csvFile = zip.file(/(^|\/)activities\.csv$/i)[0];
  if (!csvFile) throw new Error("This zip has no activities.csv. Use the archive Strava emails you from Download your data.");
  const rows = parseCSV(await csvFile.async("string"));
  const head = rows.shift().map((h) => h.trim());
  const at = (name, which = "first") => (which === "last" ? head.lastIndexOf(name) : head.indexOf(name));
  const C = { id: at("Activity ID"), date: at("Activity Date"), name: at("Activity Name"), type: at("Activity Type"),
    desc: at("Activity Description"), elapsed: at("Elapsed Time", "last"), dist: at("Distance", "last"), moving: at("Moving Time"),
    elev: at("Elevation Gain"), hr: at("Average Heart Rate"), maxhr: at("Max Heart Rate"), cal: at("Calories"), file: at("Filename") };
  if (C.id < 0 || C.date < 0 || C.type < 0) throw new Error("activities.csv doesn't look like a Strava export.");
  const num = (r, c) => { const v = c >= 0 ? parseFloat(String(r[c] ?? "").replace(/,/g, "")) : NaN; return isFinite(v) ? v : null; };
  const runs = rows.filter((r) => RUN_TYPES[(r[C.type] || "").trim()]);
  const out = []; let noMap = 0;
  for (let n = 0; n < runs.length; n++) {
    const r = runs[n];
    onProgress(n, runs.length);
    let dist = num(r, C.dist);
    const firstDist = num(r, at("Distance"));
    if ((dist == null || dist === firstDist) && firstDist != null && firstDist < 500) dist = firstDist * 1000;   // km column only
    const when = parseStravaDate(r[C.date]);
    const a = { id: String(r[C.id]).trim(), sport: RUN_TYPES[r[C.type].trim()], name: r[C.name] || "Run",
      description: C.desc >= 0 ? r[C.desc] : "", distance_m: dist, moving_s: num(r, C.moving), elapsed_s: num(r, C.elapsed),
      elev_m: num(r, C.elev), avg_hr: num(r, C.hr), max_hr: num(r, C.maxhr), calories: num(r, C.cal) };
    const fname = C.file >= 0 ? (r[C.file] || "").trim() : "";
    const f = fname && zip.file(fname);
    let track = null;
    if (f && /\.(gpx|tcx)(\.gz)?$/i.test(fname)) {
      try {
        let bytes = await f.async("uint8array");
        if (/\.gz$/i.test(fname)) bytes = await gunzip(bytes);
        const pts = parseTrack(new TextDecoder().decode(bytes));
        if (pts.length > 1) track = analyzeTrack(pts, dist);
      } catch { /* keep the summary */ }
    }
    if (track) {
      Object.assign(a, { polyline: track.polyline, splits: track.splits, best_efforts: track.best_efforts });
      a.distance_m ??= +track.track_m.toFixed(1);
      a.moving_s ??= track.moving_s;
      a.max_hr ??= track.hr_max;
    } else noMap++;
    const start = track?.start || when;
    if (!start) continue;
    a.start_local = localIso(start);
    a.date = a.start_local.slice(0, 10);
    out.push(a);
  }
  onProgress(runs.length, runs.length);
  return { runs: out, noMap };
}

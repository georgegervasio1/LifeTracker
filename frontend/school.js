// Education: choose a school and major, and (Ohio State) add classes from the live class search.
// The school decides grading, requirements, calendar and finals (backend/schools.py).
let ctx = null;                 // { api, $, esc, toast, getEdu, setEdu, rerender }

export function initSchool(c) { ctx = c; }

// ---------- School and major ----------
export function renderSchoolSetup(el, { first = false } = {}) {
  const { esc } = ctx, edu = ctx.getEdu(), C = edu.computed, S = C.school;
  const cur = edu.needs_school ? null : S.id;
  const schoolsHtml = C.schools.map((s) => `<label class="school-opt ${s.id === cur ? "on" : ""}">
      <input type="radio" name="school_id" value="${s.id}" ${s.id === cur ? "checked" : ""} />
      <span><strong>${esc(s.name)}</strong><small>${esc(s.program)}</small></span></label>`).join("");
  el.innerHTML = `<div class="card school-setup">
    <h3>${first ? "Set up Education for your school" : "School and major"}</h3>
    <p class="hint">Your school sets the grading scale, degree requirements, academic calendar and finals. You can change it later.</p>
    <form id="schoolForm" class="school-form" autocomplete="off">
      <div class="school-opts">${schoolsHtml}</div>
      <div id="majorWrap"></div>
      <label class="school-target"><span>Target GPA <span class="hint">(optional)</span></span><input class="input mini" name="target_gpa" type="number" step="0.01" min="0" /></label>
      <div class="dialog-actions">${first ? "" : `<button type="button" class="btn btn-secondary" id="schoolCancel">Cancel</button>`}
        <button class="btn btn-primary" id="schoolSave">Save</button></div>
    </form></div>`;
  const f = el.querySelector("#schoolForm");
  if (!first && C.profile.target_gpa != null) f.elements.target_gpa.value = C.profile.target_gpa.toFixed(2);
  const drawMajor = () => {
    const id = f.querySelector("input[name=school_id]:checked")?.value;
    el.querySelectorAll(".school-opt").forEach((o) => o.classList.toggle("on", o.querySelector("input").checked));
    const majors = id === "osu" ? (id === S.id ? S.majors : null) || OSU_MAJORS_FALLBACK : null;
    f.elements.target_gpa.max = id === "gulc" ? "4.33" : "4";
    el.querySelector("#majorWrap").innerHTML = majors ? `<label class="school-major">Major<select class="select" name="major_id">
      ${majors.map((m) => `<option value="${m.id}" ${m.id === (S.major?.id) ? "selected" : ""}>${esc(m.label)}</option>`).join("")}</select></label>
      ${S.id === "osu" && S.major?.note ? `<p class="hint">${esc(S.major.note)}</p>` : ""}` : "";
  };
  f.querySelectorAll("input[name=school_id]").forEach((r) => (r.onchange = drawMajor));
  drawMajor();
  el.querySelector("#schoolCancel")?.addEventListener("click", () => { el.innerHTML = ""; el.classList.add("hidden"); });
  f.onsubmit = async (e) => {
    e.preventDefault();
    const school_id = f.querySelector("input[name=school_id]:checked")?.value;
    if (!school_id) return ctx.toast("Choose your school");
    const body = { school_id, target_gpa: f.elements.target_gpa.value || null };
    try {
      if (school_id !== cur) ctx.setEdu(await ctx.api("/api/edu/profile", { method: "PUT", body: JSON.stringify({ school_id }) }));
      if (f.elements.major_id) body.major_id = f.elements.major_id.value;
      delete body.school_id;
      ctx.setEdu(await ctx.api("/api/edu/profile", { method: "PUT", body: JSON.stringify(body) }));
      ctx.toast("Saved"); el.innerHTML = ""; el.classList.add("hidden"); ctx.rerender();
    } catch (err) { ctx.toast(err.message); }
  };
}
// Shown before the profile has switched to Ohio State (the server lists the majors once it has).
const OSU_MAJORS_FALLBACK = [
  { id: "fin", label: "Finance (BSBA, Fisher College of Business)" },
  { id: "econ_ba", label: "Economics (BA, Arts and Sciences)" },
  { id: "econ_bs", label: "Economics (BS, Arts and Sciences)" },
  { id: "other", label: "Another major (add its required courses yourself)" },
];

// ---------- Ohio State class search ----------
let found = [];
export function openClassSearch(term) {
  const { $, esc } = ctx, edu = ctx.getEdu();
  const d = $("classDialog");
  $("classTerm").innerHTML = edu.computed.profile.terms.map((t) => `<option ${t === term ? "selected" : ""}>${esc(t)}</option>`).join("");
  $("classResults").innerHTML = `<p class="hint">Search by course number (ECON 2001, BUSFIN 3220) or by title. Times, rooms and instructors come from Ohio State's class search.</p>`;
  d.showModal();
  $("classQ").focus();
}

function sectionRow(courseIdx, s) {
  const { esc } = ctx;
  const when = s.meetings.length ? s.meetings.map((m) => `${m.days} ${m.start}–${m.end}`).join(", ") : "No set time";
  const where = [...new Set(s.meetings.map((m) => m.location).filter(Boolean))].join(", ");
  return `<label class="sec-row">
    <input type="radio" name="sec-${courseIdx}-${esc(s.component)}" value="${esc(s.class_number)}" />
    <span class="sec-main"><strong>${esc(when)}</strong><small>${[s.component, `#${s.class_number}`, s.instructors.join(", "), where, s.mode && s.mode !== "P" ? s.mode : "", s.session && !/regular/i.test(s.session) ? s.session : ""].filter(Boolean).map(esc).join(" · ")}</small></span>
    <span class="sec-status ${/open/i.test(s.status) ? "pos" : "neg"}">${esc(s.status || "")}${s.waitlist ? ` · WL ${s.waitlist}` : ""}</span></label>`;
}

function drawResults() {
  const { $, esc } = ctx;
  if (!found.length) { $("classResults").innerHTML = `<p class="empty small">No classes found for that search this term.</p>`; return; }
  $("classResults").innerHTML = found.map((c, i) => {
    const comps = [...new Set(c.sections.map((s) => s.component))];
    return `<details class="class-hit" ${found.length === 1 ? "open" : ""}><summary><span><strong>${esc(c.code)}</strong> ${esc(c.title)}</span><span class="tag">${c.credits ?? c.credits_range.filter((x) => x != null).join("–")} cr</span></summary>
      ${c.ge_tags.length ? `<p class="hint">Counts for ${c.ge_tags.map((t) => esc(ctx.getEdu().computed.school.tag_short[t] || t)).join(", ")}</p>` : ""}
      ${comps.map((k) => `<div class="sec-group"><div class="sec-head">${esc(k || "Section")}${comps.length > 1 ? " · pick one" : ""}</div>
        ${c.sections.filter((s) => s.component === k).map((s) => sectionRow(i, s)).join("")}</div>`).join("")}
      <div class="class-add"><select class="select" data-status="${i}"><option value="enrolled">Enrolled</option><option value="planned">Planned</option><option value="waitlisted">Waitlisted</option><option value="considering">Considering</option></select>
        <button class="btn btn-primary btn-sm" data-addclass="${i}">Add to planner</button></div></details>`;
  }).join("");
  document.querySelectorAll("[data-addclass]").forEach((b) => (b.onclick = () => addClass(+b.dataset.addclass)));
}

async function addClass(i) {
  const { $ } = ctx, c = found[i];
  const comps = [...new Set(c.sections.map((s) => s.component))];
  const picked = comps.map((k) => {
    const v = document.querySelector(`input[name="sec-${i}-${CSS.escape(k)}"]:checked`)?.value;
    return c.sections.find((s) => s.component === k && s.class_number === v);
  }).filter(Boolean);
  if (!picked.length) return ctx.toast("Pick a section first");
  const lead = picked.find((s) => /lec/i.test(s.component)) || picked[0];   // the lecture sets the final exam slot
  const ordered = [lead, ...picked.filter((s) => s !== lead)];
  const body = {
    title: c.title, code: c.code, term: $("classTerm").value, credits: c.credits ?? c.credits_range[0] ?? null,
    status: document.querySelector(`[data-status="${i}"]`).value, grade_status: "projected",
    crn: ordered.map((s) => s.class_number).join(", "),
    meetings_text: ordered.map((s) => s.meetings_text).filter(Boolean).join(", "),
    professor: lead.instructors.join(", "), component: ordered.map((s) => s.component).join(" + "),
    location: [...new Set(ordered.flatMap((s) => s.meetings.map((m) => m.location)).filter(Boolean))].join(", "),
    tags: c.ge_tags, graded: true,
  };
  try {
    ctx.setEdu(await ctx.api("/api/edu/courses", { method: "POST", body: JSON.stringify(body) }));
    ctx.toast(`${c.code} added`); ctx.rerender();
  } catch (e) { ctx.toast(e.message); }
}

export function wireClassSearch() {
  const { $ } = ctx;
  $("classForm").onsubmit = async (e) => {
    e.preventDefault();
    const q = $("classQ").value.trim();
    if (!q) return;
    $("classResults").innerHTML = `<p class="hint">Searching…</p>`;
    try {
      const r = await ctx.api(`/api/edu/catalog/search?q=${encodeURIComponent(q)}&term=${encodeURIComponent($("classTerm").value)}`);
      found = r.courses; drawResults();
    } catch (err) { $("classResults").innerHTML = `<p class="empty small">${ctx.esc(err.message)}</p>`; }
  };
  $("classClose").onclick = () => $("classDialog").close();
}

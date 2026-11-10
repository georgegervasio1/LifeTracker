// Developer page (#/admin): who has signed up, and who may use LifeTracker. Account rows only, never anyone's data.
let ctx = null;
export function initAdmin(c) { ctx = c; }

const ROLE = { owner: "Owner", admin: "Developer", member: "Approved", pending: "Waiting" };
const when = (t) => (t ? new Date(t * 1000).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—");

export async function loadAdmin() {
  const { $, esc, api, toast } = ctx, root = $("adminRoot");
  root.innerHTML = `<p class="hint">Loading…</p>`;
  let d;
  try { d = await api("/api/admin/users"); }
  catch (e) { root.innerHTML = `<div class="card empty-state"><h3>Developer</h3><p>${esc(e.message)}</p></div>`; return; }
  const draw = () => {
    root.innerHTML = `<div class="admin-head"><h2 class="title">Developer</h2>
      <p class="lede-sm">Everyone who has signed in with Google, and who has access. ${d.signup_open ? "Sign-up is open, so new accounts get in right away" : "New accounts wait here until you approve them"}. Finances stay with the owner account.</p></div>
      <div class="kpis">${["member", "pending", "admin", "owner"].map((k) => `<div class="kpi"><span class="kpi-label">${ROLE[k]}</span><span class="kpi-value">${d.counts[k] || 0}</span></div>`).join("")}</div>
      <div class="card admin-open"><div><strong>Open sign-up</strong><p class="hint">${d.signup_open ? "Anyone who signs in with Google gets in right away, with their own empty data and no Finances." : "New accounts wait for your approval."}${d.signup_env ? " Set on the server (SIGNUP_OPEN), so this switch can't change it." : ""}</p></div>
        <label class="switch"><input type="checkbox" id="signupOpen" ${d.signup_open ? "checked" : ""} ${d.signup_env ? "disabled" : ""} /> ${d.signup_open ? "On" : "Off"}</label></div>
      <form class="card admin-add" id="adminAdd"><label>Approve an email before they sign in<input class="input" name="email" type="email" placeholder="name@example.com" required /></label><button class="btn btn-primary btn-sm">Approve</button></form>
      <div class="card card-flush"><table class="table admin-table"><thead><tr><th>Account</th><th>Access</th><th>First sign-in</th><th>Last seen</th><th></th></tr></thead><tbody>
      ${d.users.map((u) => `<tr><td><strong>${esc(u.name || u.email)}</strong>${u.name ? `<small>${esc(u.email)}</small>` : ""}</td>
        <td><span class="pill pill-${u.role === "pending" ? "missing" : u.role === "member" ? "on_track" : "met"}">${ROLE[u.role]}</span></td>
        <td>${when(u.first_seen)}</td><td>${when(u.last_seen)}</td>
        <td class="num">${u.role === "pending" ? `<button class="btn btn-primary btn-sm" data-approve="${esc(u.email)}">Approve</button>` : u.role === "member" && !d.signup_open ? `<button class="text-btn danger" data-remove="${esc(u.email)}">Remove access</button>` : ""}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">No one has signed in yet</td></tr>`}
      </tbody></table></div>`;
    const set = async (email, approve) => {
      try { d = await api("/api/admin/access", { method: "POST", body: JSON.stringify({ email, approve }) }); toast(approve ? `${email} approved` : `${email} removed`); draw(); }
      catch (e) { toast(e.message); }
    };
    root.querySelectorAll("[data-approve]").forEach((b) => (b.onclick = () => set(b.dataset.approve, true)));
    root.querySelectorAll("[data-remove]").forEach((b) => (b.onclick = () => confirm(`Remove ${b.dataset.remove}'s access? Their data stays, but they can't sign in.`) && set(b.dataset.remove, false)));
    $("signupOpen").onchange = async (e) => {
      try { d = await api("/api/admin/signup", { method: "POST", body: JSON.stringify({ open: e.target.checked }) }); toast(d.signup_open ? "Sign-up is open" : "New accounts need approval"); draw(); }
      catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
    };
    $("adminAdd").onsubmit = (e) => { e.preventDefault(); set(e.target.elements.email.value.trim(), true); };
  };
  draw();
}

// Shared motion helpers for section dashboards (Education, Health). Everything respects prefers-reduced-motion.
const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Run a DOM update inside a View Transition when the browser supports it. Always returns an object with
// `updateCallbackDone` and `finished` promises, so callers can clear view-transition-names afterwards.
export function viewSwap(fn) {
  if (document.startViewTransition && !reduced()) return document.startViewTransition(fn);
  const done = Promise.resolve().then(fn);
  return { updateCallbackDone: done, ready: done, finished: done, skipTransition() {} };
}

// Stagger a fade-and-rise over the [data-enter] children of a container (or the container's children).
export function enter(container, { step = 40, max = 12 } = {}) {
  if (!container || reduced()) return;
  const items = [...container.querySelectorAll("[data-enter]")];
  (items.length ? items : [...container.children]).forEach((el, i) => {
    el.classList.remove("enter"); void el.offsetWidth;          // restart the animation on re-render
    el.style.setProperty("--enter-delay", `${Math.min(i, max) * step}ms`);
    el.classList.add("enter");
  });
}

// Slide a newly shown panel in from the side the user is moving toward (dir: 1 forward, -1 back).
export function panelIn(panel, dir = 1) {
  if (!panel || reduced()) return;
  panel.style.setProperty("--panel-from", `${dir * 18}px`);
  panel.classList.remove("panel-in"); void panel.offsetWidth;
  panel.classList.add("panel-in");
}

// Collapse an element's height to zero, then resolve (for rows leaving a list).
export function collapse(el, ms = 260) {
  if (!el || reduced()) return Promise.resolve();
  el.style.height = `${el.offsetHeight}px`; el.style.overflow = "hidden"; void el.offsetWidth;
  el.classList.add("collapsing");
  el.style.height = "0px";
  return new Promise((r) => setTimeout(r, ms));
}

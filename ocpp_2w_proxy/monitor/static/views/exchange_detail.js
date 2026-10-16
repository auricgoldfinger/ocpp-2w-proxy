// The exchange detail drawer: a right drawer on desktop, a full-screen sheet on phones (CSS).
// It loads GET /api/exchanges/{id}, re-loads when the exchange changes live, and closes by
// navigating back to the list so the URL always says what is open.

import { wantsSession } from "../cards/index.js";
import { exchangeDetail } from "../components/exchange_detail.js";
import { copyText } from "../components/json_view.js";
import { lanesFor } from "../components/swimlane_model.js";
import { tabId, viewTabs } from "../components/view_tabs.js";
import { sessionHash } from "../filters.js";
import { html, setHtml } from "../html.js";
import { captureDrawerState, restoreDrawerState } from "./drawer_state.js";
import { createViewMode, modeForKey } from "./view_mode.js";

const LIVE_RELOAD_MS = 500;
const TARGET_HIGHLIGHT_MS = 1600;
const FOCUSABLE = "a[href], button:not([disabled]), input, select, [tabindex]:not([tabindex='-1'])";

function titleBar(title, subtitle) {
  return html`<div class="drawer__bar"><div class="drawer__titles"><h2 id="drawer-title" tabindex="-1" data-key="title">${title}</h2><p class="drawer__subtitle">${subtitle}</p></div><button type="button" class="icon-button" data-action="close" data-key="close" aria-label="Close detail" title="Close (Esc)">✕</button></div>`;
}

async function loadSession(api, detail) {
  if (!wantsSession(detail.action) || detail.transaction_id === null) return null;
  try {
    return await api.session(detail.transaction_id);
  } catch {
    return null;
  }
}

export function createDetailDrawer({ api, live, config, navigate }) {
  const drawer = document.getElementById("drawer");
  const backdrop = document.getElementById("drawer-backdrop");
  let openId = null;
  let filters = null;
  // What the loaded exchange was drawn with, so a tab switch redraws without fetching.
  let shown = null;
  const viewMode = createViewMode();
  let token = 0;
  let reloadTimer = null;
  let returnFocus = null;

  const close = () => navigate({ view: "exchanges", filters });

  function render(body, title, subtitle, tabs = "") {
    setHtml(drawer, html`${titleBar(title, subtitle)}${tabs}<div class="drawer__body" data-key="body">${body}</div>`);
  }

  function renderShown() {
    const { context, title, subtitle } = shown;
    const mode = viewMode.mode;
    render(exchangeDetail({ ...context, mode, now: Date.now() / 1000 }), title, subtitle, viewTabs(mode));
  }

  function selectView(mode) {
    if (!shown || !viewMode.select(mode)) return;
    renderShown();
    drawer.querySelector(`#${tabId(mode)}`)?.focus();
  }

  async function load({ quiet }) {
    const mine = ++token;
    if (!quiet) render(html`<p class="muted" role="status">Loading exchange…</p>`, "Exchange", openId);
    try {
      const loaded = await api.exchange(openId);
      const session = await loadSession(api, loaded);
      if (mine !== token) return;
      const lanes = lanesFor(config, [loaded]);
      const outcome = (loaded.outcome ?? "open").replaceAll("_", " ");
      const subtitle = `${loaded.headline ?? ""} · ${outcome}${loaded.problem ? " · problem" : ""}`;
      const txHref = loaded.transaction_id === null ? null : sessionHash(filters, loaded.transaction_id);
      shown = { context: { detail: loaded, lanes, session, txHref }, title: loaded.action, subtitle };
      // A live reload of the same exchange keeps what the reader had open, scrolled and focused.
      const kept = quiet ? captureDrawerState(drawer) : null;
      renderShown();
      if (kept) restoreDrawerState(drawer, kept);
    } catch (error) {
      if (mine !== token) return;
      const message = error.status === 404
        ? "This exchange is not stored (any more). It may have passed the retention period."
        : "Could not load this exchange.";
      render(html`<p class="error" role="alert">${message}</p>`, "Exchange", openId);
    }
    if (!quiet) drawer.querySelector("#drawer-title")?.focus();
  }

  // `target` is a frame index, "all" or "record" (see copyText); without clipboard access the
  // matching block is selected instead, for Ctrl+C.
  async function copy(target) {
    const status = drawer.querySelector('[data-part="copy-status"]');
    try {
      await navigator.clipboard.writeText(copyText(shown.context.detail, target));
      status.textContent = "Copied.";
    } catch {
      const source = drawer.querySelector(`[data-copy-source="${CSS.escape(target)}"]`);
      if (!source) return;
      const range = document.createRange();
      range.selectNodeContents(source);
      getSelection().removeAllRanges();
      getSelection().addRange(range);
      status.textContent = "Selected; press Ctrl+C to copy.";
    }
  }

  // A sequence arrow jumps to the Readable block of its frame and outlines it for a moment.
  function jumpToFrame(index) {
    const target = drawer.querySelector(`[data-frame-target="${CSS.escape(index)}"]`);
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.focus({ preventScroll: true });
    target.classList.add("is-frame-target");
    setTimeout(() => target.classList.remove("is-frame-target"), TARGET_HIGHLIGHT_MS);
  }

  function trapFocus(event) {
    const items = [...drawer.querySelectorAll(FOCUSABLE)];
    if (items.length === 0) return;
    const [first, last] = [items[0], items.at(-1)];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  drawer.addEventListener("click", (event) => {
    const control = event.target.closest("[data-action]");
    const action = control?.dataset.action;
    if (action === "close") close();
    if (action === "copy" && shown) copy(control.dataset.copy);
    const view = event.target.closest("[data-view]")?.dataset.view;
    if (view) selectView(view);
    const frame = event.target.closest("[data-frame]")?.dataset.frame;
    if (frame !== undefined) jumpToFrame(frame);
  });
  drawer.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
    if (event.key === "Tab") trapFocus(event);
    const frame = event.target.closest?.("[data-frame]")?.dataset.frame;
    if (event.key === "Enter" && frame !== undefined) jumpToFrame(frame);
    const next = event.target.closest?.('[role="tab"]') ? modeForKey(event.key, viewMode.mode) : null;
    if (next) {
      event.preventDefault();
      selectView(next);
    }
  });
  backdrop.addEventListener("click", close);
  live.subscribe({
    exchange: (summary) => {
      if (summary.id !== openId) return;
      clearTimeout(reloadTimer);
      reloadTimer = setTimeout(() => load({ quiet: true }), LIVE_RELOAD_MS);
    },
    resync: () => openId && load({ quiet: true }),
  });

  return {
    show(id, nextFilters) {
      filters = nextFilters;
      if (id === openId) return;
      if (openId === null) returnFocus = document.activeElement;
      openId = id;
      shown = null;
      viewMode.open(id);
      drawer.hidden = false;
      backdrop.hidden = false;
      document.body.classList.add("drawer-open");
      load({ quiet: false });
    },
    hide() {
      if (openId === null) return;
      const id = openId;
      openId = null;
      token += 1;
      clearTimeout(reloadTimer);
      drawer.hidden = true;
      backdrop.hidden = true;
      document.body.classList.remove("drawer-open");
      const row = document.querySelector(`.row[data-id="${CSS.escape(id)}"] .row__link`);
      (row ?? returnFocus)?.focus?.();
    },
  };
}

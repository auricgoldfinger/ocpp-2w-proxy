// The Exchanges view: topology header, filter bar and the live swimlane list.

import { filterBar } from "../components/filter_bar.js";
import { exchangeRow, gapRow, laneHeader } from "../components/swimlane.js";
import { lanesFor, rowModel } from "../components/swimlane_model.js";
import { topology } from "../components/topology.js";
import { ExchangeFeed } from "../exchange_feed.js";
import { buildHash, exchangesQuery, isHiddenHeartbeat, matchesFilters, sessionHash } from "../filters.js";
import { html, setHtml } from "../html.js";
import { bindFilterBar, syncFilterBar } from "./filter_controls.js";
import { createSessionPanel } from "./session_panel.js";

const TOP_SLACK_PX = 48;
const LINKS_REFRESH_MS = 60_000;
const LINKS_DEBOUNCE_MS = 1000;

const now = () => Date.now() / 1000;

const SKELETON = html`<section class="exchanges">
<div class="topology-wrap" data-part="topology" aria-label="Topology"></div>
<div data-part="filters"></div>
<div data-part="session"></div>
<div class="list-status" data-part="pill-wrap"><button type="button" class="pill" data-part="pill" hidden></button></div>
<div class="lanes" data-part="lanes">
<div class="lanes-head-wrap" data-part="lane-head"></div>
<ol class="rows" data-part="rows" aria-live="off" aria-label="Exchanges, newest first"></ol>
</div>
<div class="list-footer" data-part="footer"></div>
</section>`;

export function mountExchanges(container, { api, live, config, navigate }) {
  setHtml(container, SKELETON);
  const part = (name) => container.querySelector(`[data-part="${name}"]`);
  const feed = new ExchangeFeed();
  const sessionPanel = createSessionPanel(part("session"), { api });
  const actions = new Set(config.secondaries.flatMap((secondary) => secondary.forward_actions));
  let filters = null;
  let frozen = false;
  let status = "loading";
  let linksData = null;
  let renderQueued = false;
  let loadToken = 0;
  let linksTimer = null;

  setHtml(part("filters"), filterBar([config.primary, ...config.secondaries.map((secondary) => secondary.name)]));
  const form = part("filters").querySelector("form");
  bindFilterBar(form, {
    currentFilters: () => filters,
    onChange: (next) => navigate({ view: "exchanges", filters: next }),
    onFreeze: () => setFrozen(!frozen),
  });

  function following() {
    return !frozen && window.scrollY <= TOP_SLACK_PX;
  }

  function scheduleRender() {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(() => {
      renderQueued = false;
      render();
    });
  }

  function chargerLinks() {
    if (!linksData) return null;
    const id = filters.charger || config.chargers[0];
    return linksData.chargers.find((charger) => charger.id === id) ?? linksData.chargers[0] ?? null;
  }

  function renderTopology() {
    const hrefFor = (peer) => buildHash({ view: "exchanges", filters: { ...filters, peer } });
    setHtml(part("topology"), topology(config, chargerLinks(), { now: now(), hrefFor }));
  }

  function renderRows() {
    const lanes = lanesFor(config, feed.exchanges());
    part("lanes").style.setProperty("--lanes", String(lanes.length));
    setHtml(part("lane-head"), laneHeader(lanes));
    const at = now();
    const rows = feed.items().map((item) => {
      if (item.type === "gap") return gapRow(item.gap);
      const { id, transaction_id: tx } = item.summary;
      const href = buildHash({ view: "exchange", id, filters });
      return exchangeRow(rowModel(item.summary, lanes, at), href, tx === null ? null : sessionHash(filters, tx));
    });
    setHtml(part("rows"), html`${rows}`);
  }

  function footer() {
    if (status === "loading") return html`<p class="muted" role="status">Loading exchanges…</p>`;
    if (status === "error") {
      return html`<p class="error" role="alert">Could not load exchanges. <button type="button" class="link-button" data-action="retry">Retry</button></p>`;
    }
    const empty = feed.size === 0 ? html`<p class="empty" role="status">No exchanges match these filters.</p>` : "";
    if (feed.trimmed) {
      return html`${empty}<button type="button" class="button" data-action="reload">Older rows were trimmed · reload</button>`;
    }
    const older = feed.next ? html`<button type="button" class="button" data-action="older">Load older</button>` : "";
    return html`${empty}${older}`;
  }

  function render() {
    renderRows();
    setHtml(part("footer"), footer());
    const pill = part("pill");
    const count = feed.pendingNew;
    pill.hidden = count === 0;
    pill.textContent = `▲ ${count} new`;
    syncFilterBar(form, filters, { hidden: feed.hidden, frozen, actions });
  }

  async function loadFirstPage() {
    const token = ++loadToken;
    status = "loading";
    scheduleRender();
    try {
      const page = await api.exchanges(exchangesQuery(filters, { now: now() }));
      if (token !== loadToken) return;
      feed.reset(page);
      feed.setGaps(chargerGaps());
      page.exchanges.forEach((summary) => actions.add(summary.action));
      status = "ready";
    } catch {
      if (token === loadToken) status = "error";
    }
    scheduleRender();
  }

  async function loadOlder() {
    const token = loadToken;
    try {
      const page = await api.exchanges(exchangesQuery(filters, { now: now(), before: feed.next }));
      if (token !== loadToken) return;
      feed.appendOlder(page);
      page.exchanges.forEach((summary) => actions.add(summary.action));
    } catch {
      status = "error";
    }
    scheduleRender();
  }

  function chargerGaps() {
    const gaps = linksData?.gaps ?? [];
    return filters.charger ? gaps.filter((gap) => gap.charger_id === filters.charger) : gaps;
  }

  async function loadLinks() {
    try {
      linksData = await api.links();
      feed.setGaps(chargerGaps());
    } catch {
      linksData = null;
    }
    renderTopology();
    scheduleRender();
  }

  function scheduleLinks() {
    clearTimeout(linksTimer);
    linksTimer = setTimeout(loadLinks, LINKS_DEBOUNCE_MS);
  }

  function release() {
    feed.release();
    if (following()) feed.trim();
    scheduleRender();
  }

  function setFrozen(value) {
    frozen = value;
    if (!frozen && following()) release();
    scheduleRender();
  }

  function onExchange(summary) {
    actions.add(summary.action);
    sessionPanel.exchangeArrived(summary);
    if (!matchesFilters(summary, filters, now())) {
      const unhidden = { ...filters, heartbeats: true };
      if (isHiddenHeartbeat(summary, filters) && matchesFilters(summary, unhidden, now()) && !feed.has(summary.id)) {
        feed.countHiddenHeartbeat();
        scheduleRender();
      }
      return;
    }
    if (following()) {
      feed.upsert(summary);
      feed.trim();
    } else {
      feed.hold(summary);
    }
    scheduleRender();
  }

  function onGap(gap) {
    if (filters.charger && gap.charger_id !== filters.charger) return;
    feed.addGap(gap);
    scheduleRender();
  }

  // A click on a row outside its links opens it, unless the user is selecting text.
  function openRow(event) {
    const row = event.target.closest(".row--exchange");
    if (!row || event.target.closest("a, button") || getSelection().toString()) return;
    row.querySelector(".row__link")?.click();
  }

  function onClick(event) {
    openRow(event);
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "older") loadOlder();
    if (action === "retry" || action === "reload") loadFirstPage();
  }

  function onScroll() {
    if (following() && feed.pending.size > 0) release();
  }

  function onPill() {
    window.scrollTo({ top: 0, behavior: "auto" });
    frozen = false;
    release();
  }

  container.addEventListener("click", onClick);
  part("pill").addEventListener("click", onPill);
  window.addEventListener("scroll", onScroll, { passive: true });
  const unsubscribe = live.subscribe({
    exchange: onExchange,
    gap: onGap,
    link: scheduleLinks,
    resync: () => {
      loadLinks();
      loadFirstPage();
      sessionPanel.reload();
    },
  });
  const linksInterval = setInterval(loadLinks, LINKS_REFRESH_MS);

  return {
    update(state) {
      const changed = JSON.stringify(state.filters) !== JSON.stringify(filters);
      filters = state.filters;
      if (!changed) return;
      renderTopology();
      if (linksData === null) loadLinks();
      loadFirstPage();
      sessionPanel.show(filters);
    },
    unmount() {
      unsubscribe();
      clearInterval(linksInterval);
      clearTimeout(linksTimer);
      sessionPanel.dispose();
      window.removeEventListener("scroll", onScroll);
      container.removeEventListener("click", onClick);
      loadToken += 1;
    },
  };
}

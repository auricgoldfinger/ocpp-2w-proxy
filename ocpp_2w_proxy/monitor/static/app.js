// Entry point: loads the config, starts the live stream and routes the URL hash to a view.

import { api } from "./api.js";
import { buildHash, parseHash } from "./filters.js";
import { html, setHtml } from "./html.js";
import { LiveStream } from "./live.js";
import { initTheme } from "./theme.js";
import { createDetailDrawer } from "./views/exchange_detail.js";
import { mountExchanges } from "./views/exchanges.js";
import { mountLinks } from "./views/links.js";

// Both list states (#/exchanges and #/exchange/<id>) keep the list mounted.
const VIEW_OF_ROUTE = { exchanges: "exchanges", exchange: "exchanges" };
const MOUNTERS = { exchanges: mountExchanges, links: mountLinks };

const viewElement = document.getElementById("view");
const live = new LiveStream();
let current = { name: null, view: null };

function navigate(state) {
  location.hash = buildHash(state);
}

function showStatus(connected) {
  document.getElementById("banner").hidden = connected;
  const status = document.getElementById("live-status");
  status.textContent = connected ? "● live" : "○ offline";
  status.classList.toggle("live-status--down", !connected);
}

function markTab(name) {
  for (const tab of document.querySelectorAll("[data-tab]")) {
    if (tab.dataset.tab === name) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
}

function fillChargerPicker(config, filters) {
  const picker = document.getElementById("charger-select");
  const options = [["", "all chargers"], ...config.chargers.map((id) => [id, id])];
  setHtml(picker, html`${options.map(([value, label]) => html`<option value="${value}">${label}</option>`)}`);
  picker.value = filters.charger;
  picker.addEventListener("change", () => {
    const state = parseHash(location.hash);
    navigate({ view: "exchanges", filters: { ...state.filters, charger: picker.value } });
  });
}

function route(context) {
  const state = parseHash(location.hash);
  const name = VIEW_OF_ROUTE[state.view] ?? state.view;
  if (current.name !== name) {
    current.view?.unmount();
    current = { name, view: MOUNTERS[name](viewElement, context) };
    window.scrollTo(0, 0);
  }
  current.view.update(state);
  if (state.view === "exchange") context.drawer.show(state.id, state.filters);
  else context.drawer.hide();
  document.getElementById("charger-select").value = state.filters.charger;
  markTab(name);
}

async function start() {
  initTheme(document.getElementById("theme-toggle"));
  live.subscribe({ status: showStatus });
  let config;
  try {
    config = await api.config();
  } catch {
    setHtml(viewElement, html`<p class="error" role="alert">The monitor API is not reachable. Reload to try again.</p>`);
    return;
  }
  const context = { api, live, config, navigate };
  context.drawer = createDetailDrawer(context);
  fillChargerPicker(config, parseHash(location.hash).filters);
  window.addEventListener("hashchange", () => route(context));
  route(context);
  live.start();
}

start();

// The Links view: connection health per peer and the monitor's own health. Refreshes on live
// link and gap events, and once a minute for the message rates.

import { linksPanel } from "../components/links_panel.js";
import { html, setHtml } from "../html.js";

const REFRESH_MS = 60_000;
const DEBOUNCE_MS = 1000;

const SKELETON = html`<div class="links-view"><div data-part="error"></div><div data-part="body"><p class="muted" role="status">Loading links…</p></div></div>`;

export function mountLinks(container, { api, live }) {
  setHtml(container, SKELETON);
  const errorSlot = container.querySelector('[data-part="error"]');
  const body = container.querySelector('[data-part="body"]');
  let timer = null;
  let loaded = false;
  let token = 0;

  async function load() {
    const mine = ++token;
    try {
      const links = await api.links();
      if (mine !== token) return;
      loaded = true;
      setHtml(errorSlot, html``);
      setHtml(body, linksPanel(links, Date.now() / 1000));
    } catch {
      if (mine !== token) return;
      const note = loaded ? "Could not refresh; showing the last data." : "Could not load the links.";
      setHtml(errorSlot, html`<p class="error" role="alert">${note} <button type="button" class="link-button" data-action="retry">Retry</button></p>`);
    }
  }

  function scheduleLoad() {
    clearTimeout(timer);
    timer = setTimeout(load, DEBOUNCE_MS);
  }

  const onClick = (event) => {
    if (event.target.closest('[data-action="retry"]')) load();
  };
  container.addEventListener("click", onClick);
  const unsubscribe = live.subscribe({ link: scheduleLoad, gap: scheduleLoad, resync: load });
  const interval = setInterval(load, REFRESH_MS);
  load();

  return {
    update() {},
    unmount() {
      token += 1;
      unsubscribe();
      clearInterval(interval);
      clearTimeout(timer);
      container.removeEventListener("click", onClick);
    },
  };
}

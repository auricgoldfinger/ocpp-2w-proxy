// The session header above the Exchanges list: loads GET /api/sessions/{tx} while the ?tx=
// filter is set, and reloads it (debounced) when an exchange of that transaction arrives.

import { sessionHeader } from "../components/session_header.js";
import { sessionHash } from "../filters.js";
import { html, setHtml } from "../html.js";

const RELOAD_DEBOUNCE_MS = 2000;

const now = () => Date.now() / 1000;

export function createSessionPanel(element, { api }) {
  let filters = null;
  let state = { tx: "", status: "loading", session: null };
  let token = 0;
  let timer = null;

  function render() {
    const hadFocus = element.contains(document.activeElement);
    if (!filters?.tx) {
      setHtml(element, html``);
      return;
    }
    const { status, session } = state;
    setHtml(element, sessionHeader({ tx: filters.tx, status, session, clearHref: sessionHash(filters, ""), now: now() }));
    if (hadFocus) element.querySelector("a")?.focus();
  }

  // A reload of the same tx keeps the shown figures until the new ones arrive.
  async function load() {
    const mine = ++token;
    clearTimeout(timer);
    const tx = filters?.tx;
    if (tx && state.tx !== tx) state = { tx, status: "loading", session: null };
    render();
    if (!tx) return;
    try {
      const session = await api.session(tx);
      if (mine !== token) return;
      state = { tx, status: "ready", session };
    } catch (error) {
      if (mine !== token) return;
      state = { tx, status: error.status === 404 ? "missing" : "error", session: null };
    }
    render();
  }

  return {
    /** New filters: loads when the tx changed, else only re-renders (the clear link keeps them). */
    show(next) {
      const txChanged = next.tx !== filters?.tx;
      filters = next;
      if (txChanged) load();
      else render();
    },
    /** A live exchange arrived; reloads soon when it belongs to the shown session. */
    exchangeArrived(summary) {
      if (!filters?.tx || String(summary.transaction_id) !== filters.tx) return;
      clearTimeout(timer);
      timer = setTimeout(load, RELOAD_DEBOUNCE_MS);
    },
    reload: load,
    dispose() {
      clearTimeout(timer);
      token += 1;
    },
  };
}

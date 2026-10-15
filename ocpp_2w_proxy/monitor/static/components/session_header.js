// The session header shown above the Exchanges list when the ?tx= filter is set. Pure: the
// GET /api/sessions/{tx} aggregate (or why there is none) in, SafeHtml out.

import { formatCount, formatDateTime, formatDuration, formatKwh } from "../format.js";
import { html } from "../html.js";
import { badge } from "./swimlane.js";

const STATE_VIEWS = {
  ongoing: { label: "ongoing", icon: "●", tone: "pending" },
  stopped: { label: "stopped", icon: "■", tone: "muted" },
};

const MESSAGES = {
  loading: (tx) => html`<p class="muted" role="status">Loading session ${tx}…</p>`,
  missing: (tx) => html`<p role="status">No session is stored for transaction ${tx}. It may have passed the retention period.</p>`,
  error: () => html`<p class="error" role="alert">Could not load the session summary.</p>`,
};

function clearLink(href) {
  return html`<a class="session-header__clear" href="${href}">✕ <span>Show all exchanges</span></a>`;
}

function problemsText(problems) {
  if (!problems) return "none";
  return html`<span class="session-header__problems">⚠ ${formatCount(problems)}</span>`;
}

/** The facts of a session: [label, value] pairs, the stop time "ongoing" while it runs. */
export function sessionFacts(session, now) {
  const ongoing = session.stopped_at === null;
  const duration = session.started_at === null ? null : (session.stopped_at ?? now) - session.started_at;
  return [
    ["charger", session.charger_id],
    ["connector", session.connector_id ?? "–"],
    ["idTag", session.id_tag ?? "–"],
    ["started", formatDateTime(session.started_at)],
    ["stopped", ongoing ? "ongoing" : formatDateTime(session.stopped_at)],
    [ongoing ? "running for" : "duration", formatDuration(duration)],
    ["energy", formatKwh(session.energy_wh)],
    ["exchanges", formatCount(session.exchanges)],
    ["problems", problemsText(session.problems)],
  ];
}

/**
 * `status` is "loading", "ready", "missing" (404) or "error"; `session` is set when ready.
 * `clearHref` drops the tx filter; `now` is epoch seconds for a running session.
 */
export function sessionHeader({ tx, status, session = null, clearHref, now }) {
  const title = html`<h2 id="session-title" class="session-header__title">Session <span class="mono">tx ${tx}</span></h2>`;
  if (status !== "ready") {
    return html`<section class="session-header" aria-labelledby="session-title"><div class="session-header__bar">${title}${clearLink(clearHref)}</div>${MESSAGES[status](tx)}</section>`;
  }
  const state = STATE_VIEWS[session.stopped_at === null ? "ongoing" : "stopped"];
  const facts = sessionFacts(session, now).map(([label, value]) => html`<div><dt>${label}</dt><dd>${value}</dd></div>`);
  return html`<section class="session-header${session.problems ? " session-header--problem" : ""}" aria-labelledby="session-title"><div class="session-header__bar">${title}${badge(state)}${clearLink(clearHref)}</div><dl class="facts">${facts}</dl></section>`;
}

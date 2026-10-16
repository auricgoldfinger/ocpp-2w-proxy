// Markup of the exchange detail drawer. Pure: the GET /api/exchanges/{id} body in, SafeHtml out.

import { formatDateTime } from "../format.js";
import { html } from "../html.js";
import { jsonView } from "./json_view.js";
import { readableView } from "./readable_view.js";
import { txLink } from "./swimlane.js";
import { viewPanel } from "./view_tabs.js";

// Only the active view is built, so a live reload never renders the hidden one.
const PANELS = {
  readable: readableView,
  json: ({ detail }) => jsonView(detail),
};

function originText(detail) {
  if (detail.origin === "proxy") return `proxy (${detail.proxy_kind})`;
  return detail.origin;
}

function header(detail, txHref) {
  const transaction = detail.transaction_id !== null && txHref ? txLink(detail.transaction_id, txHref) : detail.transaction_id;
  const facts = [
    ["charger", detail.charger_id],
    ["origin", originText(detail)],
    ["started", formatDateTime(detail.started_at)],
    ["transaction", transaction],
    ["connector", detail.connector_id],
    ["idTag", detail.id_tag],
    ["exchange id", detail.id],
  ].filter(([, value]) => value !== null && value !== undefined);
  const incomplete = detail.incomplete
    ? html`<p class="notice" role="note">⚠ This exchange was open during a monitor gap; some of its events may be missing. Relaying was not affected.</p>`
    : "";
  return html`<dl class="facts">${facts.map(([label, value]) => html`<div><dt>${label}</dt><dd>${value}</dd></div>`)}</dl>${incomplete}`;
}

/**
 * The drawer body: the facts, then the `mode` view ("readable" or "json") in its tab panel.
 * `txHref` (optional) links to the session.
 */
export function exchangeDetail({ mode = "readable", ...context }) {
  return html`${header(context.detail, context.txHref)}${viewPanel(mode, PANELS[mode](context))}`;
}

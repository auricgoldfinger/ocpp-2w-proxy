// StatusNotification card: the connector status as a pill (icon and word, never colour alone),
// the error code only when it is not NoError, then the info and vendor fields.

import { badge } from "../components/swimlane.js";
import { html } from "../html.js";
import { statusView } from "./status_view.js";

const STATUS_VIEWS = {
  Available: { icon: "✓", tone: "up" },
  Preparing: { icon: "◔", tone: "pending" },
  Charging: { icon: "⚡", tone: "delivered" },
  SuspendedEV: { icon: "⏸", tone: "queued" },
  SuspendedEVSE: { icon: "⏸", tone: "queued" },
  Finishing: { icon: "◕", tone: "pending" },
  Reserved: { icon: "⧖", tone: "withheld" },
  Unavailable: { icon: "⊘", tone: "muted" },
  Faulted: { icon: "✕", tone: "failed" },
};

const DETAIL_FIELDS = [
  ["info", "info"],
  ["vendorId", "vendor"],
  ["vendorErrorCode", "vendor error"],
  ["timestamp", "timestamp"],
];

/** The pill view of a connector status: {label, icon, tone}. */
export function connectorStatusView(status) {
  return statusView(status, STATUS_VIEWS);
}

function connectorText(connectorId) {
  if (connectorId === 0) return "0 (whole charger)";
  return connectorId ?? "–";
}

function errorPart(errorCode) {
  if (!errorCode || errorCode === "NoError") return "";
  return html`<span class="status-card__error">${badge({ label: errorCode, icon: "✕", tone: "failed", title: `errorCode ${errorCode}` })}</span>`;
}

export function statusNotificationCard(payload) {
  const body = payload ?? {};
  const facts = [["connector", connectorText(body.connectorId)]];
  for (const [key, label] of DETAIL_FIELDS) if (body[key] !== undefined && body[key] !== "") facts.push([label, body[key]]);
  return html`<div class="card card--status"><p class="status-card__state"><span class="status-pill">${badge(connectorStatusView(body.status))}</span>${errorPart(body.errorCode)}</p><dl class="kv">${facts.map(([label, value]) => html`<dt>${label}</dt><dd>${value}</dd>`)}</dl></div>`;
}

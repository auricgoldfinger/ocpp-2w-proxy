// BootNotification card: who the charger says it is, and what it was told back. The clock skew
// compares the reply's currentTime with when the proxy handled that reply; it is left out when
// the detail has no reply with a currentTime.

import { badge } from "../components/swimlane.js";
import { formatDuration } from "../format.js";
import { html } from "../html.js";
import { figure, figures } from "./figure.js";
import { errorView, replyFrame } from "./reply.js";
import { statusView } from "./status_view.js";

const IDENTITY_FIELDS = [
  ["chargePointSerialNumber", "serial number"],
  ["chargeBoxSerialNumber", "charge box serial"],
  ["firmwareVersion", "firmware"],
  ["meterType", "meter type"],
  ["meterSerialNumber", "meter serial"],
  ["iccid", "ICCID"],
  ["imsi", "IMSI"],
];

const REGISTRATION_VIEWS = {
  Accepted: { icon: "✓", tone: "up" },
  Pending: { icon: "⧗", tone: "queued" },
  Rejected: { icon: "✕", tone: "failed" },
};

// Below this the two clocks agree as far as the frame timestamps can tell.
const IN_SYNC_SECONDS = 1;

/** Backend clock minus proxy clock in seconds, from the reply frame; null when unknown. */
export function clockSkew(frame) {
  const sent = typeof frame?.payload?.currentTime === "string" ? Date.parse(frame.payload.currentTime) : Number.NaN;
  if (Number.isNaN(sent) || typeof frame.at !== "number") return null;
  return sent / 1000 - frame.at;
}

/** "in sync", "4s ahead" or "2m10s behind". */
export function skewText(seconds) {
  if (Math.abs(seconds) < IN_SYNC_SECONDS) return "in sync";
  return `${formatDuration(Math.abs(seconds))} ${seconds > 0 ? "ahead" : "behind"}`;
}

function identityTitle(payload) {
  const name = [payload.chargePointVendor, payload.chargePointModel].filter(Boolean).join(" ");
  return html`<p class="boot-card__name">${name || "Unnamed charger"}</p>`;
}

function replyBody(frame) {
  if (!frame) return html`<p class="muted">No reply recorded yet.</p>`;
  if (frame.msg_type === "error") return html`<p>${badge(errorView(frame))}</p>`;
  const reply = frame.payload ?? {};
  const skew = clockSkew(frame);
  return figures([
    figure("registration", badge(statusView(reply.status, REGISTRATION_VIEWS))),
    typeof reply.interval === "number" && figure("heartbeat interval", formatDuration(reply.interval)),
    skew !== null && figure("backend clock vs proxy", skewText(skew)),
  ]);
}

/** The reply part; left out when the card is rendered without the exchange detail. */
function replySection(detail) {
  if (!detail) return "";
  return html`<h4 class="card__subhead">Reply</h4>${replyBody(replyFrame(detail))}`;
}

export function bootNotificationCard(payload, { detail = null } = {}) {
  const body = payload ?? {};
  const facts = IDENTITY_FIELDS.filter(([key]) => body[key] !== undefined && body[key] !== "");
  return html`<div class="card card--boot">${identityTitle(body)}<dl class="kv">${facts.map(([key, label]) => html`<dt>${label}</dt><dd>${body[key]}</dd>`)}</dl>${replySection(detail)}</div>`;
}

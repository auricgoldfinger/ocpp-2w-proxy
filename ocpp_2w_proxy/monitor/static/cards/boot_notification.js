// BootNotification card: who the charger says it is. What it was told back (registration,
// heartbeat interval, clock skew) is drawn by responses.js.

import { html } from "../html.js";
import { replySection } from "./reply_section.js";

const IDENTITY_FIELDS = [
  ["chargePointSerialNumber", "serial number"],
  ["chargeBoxSerialNumber", "charge box serial"],
  ["firmwareVersion", "firmware"],
  ["meterType", "meter type"],
  ["meterSerialNumber", "meter serial"],
  ["iccid", "ICCID"],
  ["imsi", "IMSI"],
];

function identityTitle(payload) {
  const name = [payload.chargePointVendor, payload.chargePointModel].filter(Boolean).join(" ");
  return html`<p class="boot-card__name">${name || "Unnamed charger"}</p>`;
}

export function bootNotificationCard(payload, { detail = null } = {}) {
  const body = payload ?? {};
  const facts = IDENTITY_FIELDS.filter(([key]) => body[key] !== undefined && body[key] !== "");
  return html`<div class="card card--boot">${identityTitle(body)}<dl class="kv">${facts.map(([key, label]) => html`<dt>${label}</dt><dd>${body[key]}</dd>`)}</dl>${replySection(detail)}</div>`;
}

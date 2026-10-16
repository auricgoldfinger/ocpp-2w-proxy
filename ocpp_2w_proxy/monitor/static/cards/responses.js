// What a reply frame said, for every OCPP 1.6 action. A CallError shows its code and
// description; a CallResult is drawn by the action's confirmation renderer in RESPONSES.

import { badge } from "../components/swimlane.js";
import { formatIsoDateTime } from "../format.js";
import { html } from "../html.js";
import { heartbeat, registration } from "./clock_responses.js";
import { keysTable } from "./configuration_responses.js";
import { payloadTree } from "./generic.js";
import { statusPill } from "./reply_status.js";

const isEmpty = (payload) => payload === null || payload === undefined || Object.keys(payload).length === 0;

/** A <dl> of the [label, value] pairs that have a value; "" when none has. */
function facts(pairs) {
  const shown = pairs.filter(([, value]) => value !== undefined && value !== null && value !== "");
  if (shown.length === 0) return "";
  return html`<dl class="kv">${shown.map(([label, value]) => html`<dt>${label}</dt><dd>${value}</dd>`)}</dl>`;
}

/** Confirmations without fields: the reply only says the request was received. */
function acknowledged(frame) {
  if (isEmpty(frame.payload)) {
    return html`<p class="reply-ack">${badge({ label: "Acknowledged", icon: "✓", tone: "up" })} <span class="muted">— no data in reply</span></p>`;
  }
  return html`<p class="reply-unexpected"><span aria-hidden="true">⚠</span> Unexpected fields in a reply that should be empty</p>${payloadTree(frame.payload)}`;
}

function status(frame) {
  return html`<p class="reply-status">${statusPill(frame.payload?.status)}</p>`;
}

/** Authorize, StartTransaction and StopTransaction: the idTag's status and what came with it. */
function idTagInfo(frame) {
  const info = frame.payload?.idTagInfo;
  if (!info) return acknowledged(frame);
  return html`<p class="reply-status">${statusPill(info.status)}</p>${facts([
    ["transaction", frame.payload.transactionId],
    ["expires", info.expiryDate && formatIsoDateTime(info.expiryDate)],
    ["parent idTag", info.parentIdTag],
  ])}`;
}

function dataTransfer(frame) {
  return html`${status(frame)}${facts([["data", frame.payload?.data]])}`;
}

// GetLocalListVersion's special versions: the charger has no local list support, or no list.
const LIST_VERSIONS = { "-1": "not supported", 0: "no local list" };

function listVersion(frame) {
  const version = frame.payload?.listVersion;
  return facts([["local list version", LIST_VERSIONS[version] ?? version ?? "–"]]);
}

function compositeSchedule(frame) {
  const reply = frame.payload ?? {};
  return html`${status(frame)}${facts([
    ["connector", reply.connectorId],
    ["schedule start", reply.scheduleStart && formatIsoDateTime(reply.scheduleStart)],
  ])}${reply.chargingSchedule ? payloadTree(reply.chargingSchedule) : ""}`;
}

function diagnostics(frame) {
  const fileName = frame.payload?.fileName;
  return fileName ? facts([["file", fileName]]) : html`<p class="muted">No diagnostics file available.</p>`;
}

/** The CallResult renderer of each OCPP 1.6 action. */
export const RESPONSES = {
  Authorize: idTagInfo,
  BootNotification: registration,
  CancelReservation: status,
  ChangeAvailability: status,
  ChangeConfiguration: status,
  ClearCache: status,
  ClearChargingProfile: status,
  DataTransfer: dataTransfer,
  DiagnosticsStatusNotification: acknowledged,
  FirmwareStatusNotification: acknowledged,
  GetCompositeSchedule: compositeSchedule,
  GetConfiguration: keysTable,
  GetDiagnostics: diagnostics,
  GetLocalListVersion: listVersion,
  Heartbeat: heartbeat,
  MeterValues: acknowledged,
  RemoteStartTransaction: status,
  RemoteStopTransaction: status,
  ReserveNow: status,
  Reset: status,
  SendLocalList: status,
  SetChargingProfile: status,
  StartTransaction: idTagInfo,
  StatusNotification: acknowledged,
  StopTransaction: idTagInfo,
  TriggerMessage: status,
  UnlockConnector: status,
  UpdateFirmware: acknowledged,
};

/** A badge view of a CallError reply: "ERR <code>" with its description as the title. */
export function errorView(frame) {
  const code = frame.error?.code ?? "";
  return { label: `ERR ${code}`.trim(), icon: "✕", tone: "failed", title: frame.error?.description || code || "CallError" };
}

function callError(frame) {
  const description = frame.error?.description;
  return html`<p class="reply-error">${badge(errorView(frame))}${description ? html` <span class="reply-error__text">${description}</span>` : ""}</p>${isEmpty(frame.payload) ? "" : payloadTree(frame.payload)}`;
}

/** What the reply `frame` to an `action` call said; unknown actions get the key/value tree. */
export function responseView(action, frame) {
  if (frame.msg_type === "error") return callError(frame);
  return (RESPONSES[action] ?? ((reply) => payloadTree(reply.payload)))(frame);
}

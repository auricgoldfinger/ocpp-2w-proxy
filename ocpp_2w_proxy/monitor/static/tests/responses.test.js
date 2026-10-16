import assert from "node:assert/strict";
import { test } from "node:test";

import { clockSkew, skewText } from "../cards/clock_responses.js";
import { RESPONSES, errorView, responseView } from "../cards/responses.js";
import { exchangeStory } from "../components/exchange_story.js";
import { loadFixture } from "./fixture_loader.js";

process.env.TZ = "UTC";

const result = (payload) => ({ at: 1760537100, peer: "charger", dir: "out", msg_type: "result", wire_id: "w1", payload, same_as_origin: false, error: null });
const view = (action, payload) => String(responseView(action, result(payload)));
// The reply the requester got, from a detail fixture.
const requesterReply = (name) => exchangeStory(loadFixture(name)).requesterReply.frame;

const OCPP_16_ACTIONS = [
  "Authorize", "BootNotification", "CancelReservation", "ChangeAvailability", "ChangeConfiguration", "ClearCache",
  "ClearChargingProfile", "DataTransfer", "DiagnosticsStatusNotification", "FirmwareStatusNotification",
  "GetCompositeSchedule", "GetConfiguration", "GetDiagnostics", "GetLocalListVersion", "Heartbeat", "MeterValues",
  "RemoteStartTransaction", "RemoteStopTransaction", "ReserveNow", "Reset", "SendLocalList", "SetChargingProfile",
  "StartTransaction", "StatusNotification", "StopTransaction", "TriggerMessage", "UnlockConnector", "UpdateFirmware",
];

test("every OCPP 1.6 action has a confirmation renderer", () => {
  assert.deepEqual(Object.keys(RESPONSES).sort(), [...OCPP_16_ACTIONS].sort());
  for (const action of OCPP_16_ACTIONS) assert.equal(typeof RESPONSES[action], "function", action);
});

test("an empty confirmation is acknowledged in words, not colour alone", () => {
  for (const action of ["MeterValues", "StatusNotification", "DiagnosticsStatusNotification", "FirmwareStatusNotification", "UpdateFirmware"]) {
    const markup = view(action, {});
    assert.match(markup, /badge__icon" aria-hidden="true">✓<\/span><span class="badge__label">Acknowledged</, action);
    assert.match(markup, /no data in reply/, action);
  }
});

test("fields in a reply that should be empty are flagged and shown", () => {
  const markup = view("MeterValues", { surprise: "yes" });
  assert.match(markup, /<span aria-hidden="true">⚠<\/span> Unexpected fields/);
  assert.match(markup, /<dt title="surprise">surprise<\/dt><dd><span class="kv-str">yes<\/span>/);
  assert.ok(!markup.includes("Acknowledged"));
});

test("idTagInfo confirmations show the status and what came with it", () => {
  const start = view("StartTransaction", { transactionId: 10219, idTagInfo: { status: "Accepted", expiryDate: "2026-10-15T00:00:00Z", parentIdTag: "***9A" } });
  assert.match(start, /tone-up.*badge__label">Accepted</);
  assert.match(start, /<dt>transaction<\/dt><dd>10219<\/dd>/);
  assert.match(start, /<dt>expires<\/dt><dd>2026-10-15 00:00:00<\/dd>/);
  assert.match(start, /<dt>parent idTag<\/dt><dd>\*\*\*9A<\/dd>/);
  assert.match(view("Authorize", { idTagInfo: { status: "Blocked" } }), /tone-blocked.*⛔.*Blocked/);
  assert.match(view("StopTransaction", {}), /Acknowledged/, "StopTransaction's idTagInfo is optional");
});

test("status-only confirmations show the status pill", () => {
  assert.match(view("Reset", { status: "Rejected" }), /tone-failed.*✕.*badge__label">Rejected</);
  assert.match(view("UnlockConnector", { status: "Unlocked" }), /tone-up.*badge__label">Unlocked</);
  assert.match(view("ChangeConfiguration", { status: "RebootRequired" }), /tone-queued.*↻.*RebootRequired/);
  assert.match(view("TriggerMessage", { status: "Sideways" }), /tone-unknown.*\?.*Sideways/, "a new status still reads as text");
  const transfer = view("DataTransfer", { status: "Accepted", data: "<ok>" });
  assert.match(transfer, /<dt>data<\/dt><dd>&lt;ok&gt;<\/dd>/);
});

test("specific confirmations show their own fields", () => {
  const boot = String(responseView("BootNotification", requesterReply("boot_notification_detail")));
  assert.match(boot, /badge__label">Accepted<\/span><\/span><\/span><span class="figure__label">registration/);
  assert.match(boot, />5m00s<\/span><span class="figure__label">heartbeat interval/);
  assert.match(view("Heartbeat", { currentTime: "2025-10-15T13:25:04Z" }), />2025-10-15 13:25:04<\/span><span class="figure__label">backend time/);
  assert.match(view("GetConfiguration", { configurationKey: [{ key: "HeartbeatInterval", readonly: false, value: "300" }] }), /HeartbeatInterval<\/th><td><span class="mono">300/);
  assert.match(view("GetLocalListVersion", { listVersion: -1 }), /<dd>not supported<\/dd>/);
  assert.match(view("GetLocalListVersion", { listVersion: 7 }), /<dd>7<\/dd>/);
  assert.match(view("GetDiagnostics", { fileName: "diag.zip" }), /<dt>file<\/dt><dd>diag.zip<\/dd>/);
  assert.match(view("GetDiagnostics", {}), /No diagnostics file/);
  assert.match(view("GetCompositeSchedule", { status: "Accepted", connectorId: 1 }), /Accepted.*<dt>connector<\/dt><dd>1<\/dd>/);
});

test("an unknown action falls back to the key/value tree", () => {
  assert.match(view("VendorThing", { a: 1 }), /<dt title="a">a<\/dt><dd><span class="kv-num">1<\/span>/);
});

test("a CallError shows its code and its description as text, with any details", () => {
  const frame = { ...result({ hint: "retry" }), msg_type: "error", error: { code: "InternalError", description: "backend <down>" } };
  const markup = String(responseView("MeterValues", frame));
  assert.match(markup, /ERR InternalError<\/span><\/span> <span class="reply-error__text">backend &lt;down&gt;<\/span>/);
  assert.match(markup, /<dt title="hint">hint<\/dt>/);
  assert.deepEqual(errorView({ error: { code: "NotSupported", description: "" } }), { label: "ERR NotSupported", icon: "✕", tone: "failed", title: "NotSupported" });
});

test("clock skew is the reply's currentTime against the proxy's clock", () => {
  assert.ok(Math.abs(clockSkew(requesterReply("boot_notification_detail")) - 3.6) < 1e-6);
  assert.equal(clockSkew({ at: 1, payload: {} }), null);
  assert.equal(clockSkew({ at: 1, payload: { currentTime: "soon" } }), null);
  assert.equal(skewText(3.6), "4s ahead");
  assert.equal(skewText(-130), "2m10s behind");
  assert.equal(skewText(0.4), "in sync");
});

test("each backend's empty MeterValues reply is acknowledged", () => {
  const detail = loadFixture("meter_values_fanout_detail");
  const backendReplies = detail.frames.filter((frame) => frame.dir === "in" && frame.msg_type === "result");
  assert.deepEqual(backendReplies.map((frame) => frame.peer), ["primary", "homeassistant"]);
  for (const frame of backendReplies) assert.match(String(responseView(detail.action, frame)), /Acknowledged/, frame.peer);
});

test("without a currentTime the skew is omitted", () => {
  const frame = requesterReply("boot_notification_detail");
  delete frame.payload.currentTime;
  const markup = String(responseView("BootNotification", frame));
  assert.ok(!markup.includes("clock"));
  assert.match(markup, /registration/);
});

test("the GetConfiguration reply is a key / value / access table", () => {
  const markup = String(responseView("GetConfiguration", requesterReply("get_configuration_detail")));
  assert.match(markup, /<tr><th scope="row" class="mono">HeartbeatInterval<\/th><td><span class="mono">300<\/span><\/td><td class="config-table__access"><span aria-hidden="true">✎<\/span> writable<\/td><\/tr>/);
  assert.match(markup, /NumberOfConnectors.*🔒<\/span> read-only/);
  assert.match(markup, /SupportedFeatureProfiles<\/th><td><span class="muted">not set<\/span>/);
  assert.match(markup, /AuthorizationKey<\/th><td><span class="masked" title="Masked by the proxy">\*\*\*<span class="sr-only"> \(masked\)<\/span>/, "masked values stay masked and say so");
});

test("keys unknown to the charger are listed", () => {
  const frame = requesterReply("get_configuration_detail");
  assert.match(String(responseView("GetConfiguration", frame)), /Unknown to the charger: <span class="mono">NoSuchKey<\/span>/);
  frame.payload.unknownKey = [];
  assert.ok(!String(responseView("GetConfiguration", frame)).includes("Unknown to the charger"));
});

test("a StartTransaction reply without idTagInfo still shows its transaction", () => {
  const markup = view("StartTransaction", { transactionId: 42 });
  assert.match(markup, /<dt>transaction<\/dt><dd>42<\/dd>/);
  assert.ok(!markup.includes("Unexpected"));
});

test("structured DataTransfer data is a tree, not [object Object]", () => {
  const markup = view("DataTransfer", { status: "Accepted", data: { level: 3 } });
  assert.match(markup, /<dt title="level">level<\/dt>/);
  assert.ok(!markup.includes("[object Object]"));
});

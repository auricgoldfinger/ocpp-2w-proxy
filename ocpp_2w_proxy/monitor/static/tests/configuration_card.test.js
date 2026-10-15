import assert from "node:assert/strict";
import { test } from "node:test";

import { renderCard } from "../cards/index.js";
import { loadFixture } from "./fixture_loader.js";

const cardFor = (name, change = (detail) => detail) => {
  const detail = change(loadFixture(name));
  return String(renderCard(detail.action, detail.payload, { detail }));
};

test("GetConfiguration lists the requested keys", () => {
  assert.match(cardFor("get_configuration_detail"), /Requested 3 keys: <span class="mono">HeartbeatInterval<\/span>, <span class="mono">AuthorizationKey<\/span>/);
  assert.match(String(renderCard("GetConfiguration", {})), /Requested all keys/);
});

test("the GetConfiguration reply is a key / value / access table", () => {
  const markup = cardFor("get_configuration_detail");
  assert.match(markup, /<tr><th scope="row" class="mono">HeartbeatInterval<\/th><td><span class="mono">300<\/span><\/td><td class="config-table__access"><span aria-hidden="true">✎<\/span> writable<\/td><\/tr>/);
  assert.match(markup, /NumberOfConnectors.*🔒<\/span> read-only/);
  assert.match(markup, /SupportedFeatureProfiles<\/th><td><span class="muted">not set<\/span>/);
});

test("masked values stay masked and say so", () => {
  const markup = cardFor("get_configuration_detail");
  assert.match(markup, /AuthorizationKey<\/th><td><span class="masked" title="Masked by the proxy">\*\*\*<span class="sr-only"> \(masked\)<\/span>/);
});

test("unknown keys are listed", () => {
  assert.match(cardFor("get_configuration_detail"), /Unknown to the charger: <span class="mono">NoSuchKey<\/span>/);
  assert.ok(!cardFor("get_configuration_detail", (detail) => {
    detail.frames[2].payload.unknownKey = [];
    return detail;
  }).includes("Unknown to the charger"));
});

test("ChangeConfiguration shows key → value and the reply status", () => {
  const markup = cardFor("change_configuration_detail");
  assert.match(markup, /<span class="mono">HeartbeatInterval<\/span> <span aria-hidden="true">→<\/span><span class="sr-only">set to<\/span> <span class="mono">600<\/span>/);
  assert.match(markup, /<span class="badge tone-queued" title="RebootRequired"><span class="badge__icon" aria-hidden="true">↻<\/span><span class="badge__label">RebootRequired/);
  assert.match(String(renderCard("ChangeConfiguration", { key: "AuthorizationKey", value: "***" })), /class="masked"/);
});

test("a refused change shows the CallError; no reply says so", () => {
  const refused = cardFor("change_configuration_detail", (detail) => {
    detail.frames = detail.frames.slice(0, 2);
    detail.frames.push({ at: 1, peer: "charger", dir: "in", msg_type: "error", wire_id: "b7-25", payload: {}, same_as_origin: false, error: { code: "NotSupported", description: "" } });
    return detail;
  });
  assert.match(refused, /ERR NotSupported/);
  assert.match(cardFor("change_configuration_detail", (detail) => ({ ...detail, frames: detail.frames.slice(0, 2) })), /No reply recorded yet/);
});

test("configuration values are escaped", () => {
  const markup = String(renderCard("ChangeConfiguration", { key: "<k>", value: "<v>" }));
  assert.ok(!/<[kv]>/.test(markup));
});

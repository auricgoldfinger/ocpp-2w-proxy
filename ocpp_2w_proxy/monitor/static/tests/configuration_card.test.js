import assert from "node:assert/strict";
import { test } from "node:test";

import { renderCard } from "../cards/index.js";
import { loadFixture } from "./fixture_loader.js";

const cardFor = (name) => {
  const detail = loadFixture(name);
  return String(renderCard(detail.action, detail.payload));
};

test("GetConfiguration lists the requested keys", () => {
  assert.match(cardFor("get_configuration_detail"), /Requested 3 keys: <span class="mono">HeartbeatInterval<\/span>, <span class="mono">AuthorizationKey<\/span>/);
  assert.match(String(renderCard("GetConfiguration", {})), /Requested all keys/);
});

test("ChangeConfiguration shows key → value", () => {
  assert.match(cardFor("change_configuration_detail"), /<span class="mono">HeartbeatInterval<\/span> <span aria-hidden="true">→<\/span><span class="sr-only">set to<\/span> <span class="mono">600<\/span>/);
  assert.match(String(renderCard("ChangeConfiguration", { key: "AuthorizationKey", value: "***" })), /class="masked"/);
});

test("configuration values are escaped", () => {
  const markup = String(renderCard("ChangeConfiguration", { key: "<k>", value: "<v>" }));
  assert.ok(!/<[kv]>/.test(markup));
});

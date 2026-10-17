// The fixtures follow docs/monitor_api.md exactly, so the UI is tested against the contract.

import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import { test } from "node:test";

import { assertDetail, assertSummary } from "./contract_shapes.js";
import { loadFixture } from "./fixture_loader.js";

test("exchanges fixture follows the list contract", () => {
  const page = loadFixture("exchanges");
  assert.deepEqual(Object.keys(page).sort(), ["exchanges", "hidden", "next"]);
  page.exchanges.forEach(assertSummary);
  const starts = page.exchanges.map((exchange) => exchange.started_at);
  assert.deepEqual(starts, [...starts].sort((a, b) => b - a), "newest first");
});

test("exchange detail fixture is its list summary plus payload, frames and decisions", () => {
  const detail = loadFixture("exchange_detail");
  assertDetail(detail);
  const { payload, frames, decisions, ...summary } = detail;
  assert.deepEqual(summary, loadFixture("exchanges").exchanges.find((exchange) => exchange.id === detail.id));
});

test("every detail fixture follows the detail contract", () => {
  const names = readdirSync(new URL("./fixtures/", import.meta.url)).filter((name) => name.endsWith("_detail.json"));
  assert.ok(names.length > 0);
  for (const name of names) assertDetail(loadFixture(name.replace(/\.json$/, "")));
});

test("session fixture follows the contract", () => {
  assert.deepEqual(Object.keys(loadFixture("session")).sort(), [
    "charger_id", "connector_id", "energy_wh", "exchanges", "id_tag", "problems", "started_at", "stopped_at", "transaction_id",
  ]);
});

test("config fixture follows the contract", () => {
  const config = loadFixture("config");
  assert.deepEqual(Object.keys(config).sort(), ["chargers", "primary", "retention_days", "secondaries"]);
  for (const secondary of config.secondaries) assert.deepEqual(Object.keys(secondary).sort(), ["forward_actions", "name"]);
});

test("links fixture follows the contract", () => {
  const links = loadFixture("links");
  assert.deepEqual(Object.keys(links).sort(), ["chargers", "gaps", "history", "monitor"]);
  assert.deepEqual(Object.keys(links.monitor).sort(), ["dropped_batches", "fanout_failures"]);
  for (const charger of links.chargers) {
    assert.deepEqual(Object.keys(charger).sort(), ["connected", "id", "monitor", "peers", "since"]);
    assert.deepEqual(Object.keys(charger.monitor).sort(), ["dropped", "last_drop_at"]);
    for (const peer of charger.peers) {
      assert.deepEqual(Object.keys(peer).sort(), ["messages_last_hour", "name", "queued", "role", "since", "up"]);
    }
  }
  for (const entry of links.history) assert.deepEqual(Object.keys(entry).sort(), ["at", "charger_id", "note", "peer", "up"]);
  for (const gap of links.gaps) assert.deepEqual(Object.keys(gap).sort(), ["charger_id", "dropped", "from_at", "to_at"]);
});

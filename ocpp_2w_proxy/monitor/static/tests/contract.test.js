// The fixtures follow docs/monitor_api.md exactly, so the UI is tested against the contract.

import assert from "node:assert/strict";
import { test } from "node:test";

import { assertSummary } from "./contract_shapes.js";
import { loadFixture } from "./fixture_loader.js";

test("exchanges fixture follows the list contract", () => {
  const page = loadFixture("exchanges");
  assert.deepEqual(Object.keys(page).sort(), ["exchanges", "hidden", "next"]);
  page.exchanges.forEach(assertSummary);
  const starts = page.exchanges.map((exchange) => exchange.started_at);
  assert.deepEqual(starts, [...starts].sort((a, b) => b - a), "newest first");
});

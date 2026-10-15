import assert from "node:assert/strict";
import { test } from "node:test";

import {
  activeFilterCount,
  buildHash,
  DEFAULT_FILTERS,
  exchangesQuery,
  isHiddenHeartbeat,
  matchesFilters,
  parseHash,
} from "../filters.js";
import { exchangeById } from "./fixture_loader.js";

const meterValues = () => exchangeById("4611686018427387905");

test("an empty hash opens the exchanges view with default filters", () => {
  assert.deepEqual(parseHash(""), { view: "exchanges", id: null, filters: { ...DEFAULT_FILTERS } });
  assert.deepEqual(parseHash("#/nonsense"), { view: "exchanges", id: null, filters: { ...DEFAULT_FILTERS } });
});

test("parseHash reads the detail id and filters", () => {
  const state = parseHash("#/exchange/4611686018427387905?peer=stats&problem=1&hb=1&range=1h&q=%20c0ffee");
  assert.equal(state.view, "exchange");
  assert.equal(state.id, "4611686018427387905");
  assert.equal(state.filters.peer, "stats");
  assert.equal(state.filters.problem, true);
  assert.equal(state.filters.heartbeats, true);
  assert.equal(state.filters.range, "1h");
  assert.equal(state.filters.q, "c0ffee");
});

test("parseHash ignores an unknown range and a detail without id", () => {
  assert.equal(parseHash("#/exchanges?range=forever").filters.range, "");
  assert.equal(parseHash("#/exchange/").view, "exchanges");
});

test("buildHash round-trips and leaves defaults out", () => {
  assert.equal(buildHash({ view: "exchanges" }), "#/exchanges");
  assert.equal(buildHash({ view: "links", filters: { ...DEFAULT_FILTERS, peer: "x" } }), "#/links");
  const state = { view: "exchange", id: "42", filters: { ...DEFAULT_FILTERS, action: "Meter Values&", problem: true } };
  const hash = buildHash(state);
  assert.equal(hash, "#/exchange/42?action=Meter+Values%26&problem=1");
  assert.deepEqual(parseHash(hash), state);
});

test("exchangesQuery hides heartbeats by default and maps the range to since", () => {
  const query = new URLSearchParams(exchangesQuery({ ...DEFAULT_FILTERS, range: "1h", peer: "stats" }, { now: 5000 }));
  assert.equal(query.get("exclude"), "Heartbeat");
  assert.equal(query.get("since"), "1400");
  assert.equal(query.get("peer"), "stats");
  assert.equal(query.get("limit"), "200");
  assert.equal(query.get("before"), null);
});

test("exchangesQuery passes the cursor and shows heartbeats on request", () => {
  const query = new URLSearchParams(exchangesQuery({ ...DEFAULT_FILTERS, heartbeats: true }, { before: "c:1" }));
  assert.equal(query.get("exclude"), null);
  assert.equal(query.get("before"), "c:1");
});

test("matchesFilters mirrors the server filters", () => {
  const summary = meterValues();
  const now = summary.started_at;
  const match = (extra) => matchesFilters(summary, { ...DEFAULT_FILTERS, ...extra }, now);
  assert.ok(match({}));
  assert.ok(match({ peer: "homeassistant", action: "MeterValues", outcome: "delivered", tx: "10216" }));
  assert.ok(match({ q: "C0FFEE" }));
  assert.ok(match({ q: "4F2A" }));
  assert.ok(!match({ peer: "stats" }));
  assert.ok(!match({ problem: true }));
  assert.ok(!match({ charger: "other" }));
  assert.ok(!match({ q: "nothing" }));
  assert.ok(!matchesFilters(summary, { ...DEFAULT_FILTERS, range: "15m" }, now + 901));
});

test("heartbeats are hidden unless shown", () => {
  const heartbeat = { ...meterValues(), action: "Heartbeat" };
  assert.ok(isHiddenHeartbeat(heartbeat, DEFAULT_FILTERS));
  assert.ok(!matchesFilters(heartbeat, DEFAULT_FILTERS, heartbeat.started_at));
  assert.ok(matchesFilters(heartbeat, { ...DEFAULT_FILTERS, heartbeats: true }, heartbeat.started_at));
});

test("activeFilterCount counts what differs from the defaults", () => {
  assert.equal(activeFilterCount(DEFAULT_FILTERS), 0);
  assert.equal(activeFilterCount({ ...DEFAULT_FILTERS, peer: "x", problem: true }), 2);
});

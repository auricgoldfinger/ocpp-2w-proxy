import assert from "node:assert/strict";
import { test } from "node:test";

import { sessionFacts, sessionHeader } from "../components/session_header.js";
import { DEFAULT_FILTERS, parseHash, sessionHash } from "../filters.js";
import { loadFixture } from "./fixture_loader.js";

process.env.TZ = "UTC";

const session = () => loadFixture("session");
const header = (options) => String(sessionHeader({ tx: "10216", clearHref: "#/exchanges", now: 1760531000, ...options }));

test("sessionHash sets or clears tx and keeps the other filters", () => {
  const filters = { ...DEFAULT_FILTERS, peer: "stats", problem: true };
  assert.equal(sessionHash(filters, 10216), "#/exchanges?peer=stats&tx=10216&problem=1");
  assert.equal(sessionHash({ ...filters, tx: "10216" }, ""), "#/exchanges?peer=stats&problem=1");
  assert.equal(parseHash(sessionHash(DEFAULT_FILTERS, 7)).filters.tx, "7");
});

test("a stopped session lists every fact", () => {
  assert.deepEqual(sessionFacts(session(), 0).map(([label, value]) => `${label}=${value}`), [
    "charger=99224881", "connector=1", "idTag=***4F2A", "started=2025-10-15 12:06:40", "stopped=2025-10-15 13:20:12",
    "duration=1h13m", "energy=7.421 kWh", "exchanges=412", "problems=none",
  ]);
  const markup = header({ status: "ready", session: session() });
  assert.match(markup, /badge__label">stopped</);
  assert.match(markup, /<a class="session-header__clear" href="#\/exchanges">✕ <span>Show all exchanges<\/span><\/a>/);
  assert.match(markup, /<h2 id="session-title" class="session-header__title">Session <span class="mono">tx 10216<\/span><\/h2>/);
});

test("an ongoing session runs until now and shows its problems", () => {
  const ongoing = { ...session(), stopped_at: null, problems: 3 };
  const facts = Object.fromEntries(sessionFacts(ongoing, 1760531000));
  assert.equal(facts.stopped, "ongoing");
  assert.equal(facts["running for"], "16m40s");
  const markup = header({ status: "ready", session: ongoing });
  assert.match(markup, /session-header--problem/);
  assert.match(markup, /badge__label">ongoing</);
  assert.match(markup, /<dt>problems<\/dt><dd><span class="session-header__problems">⚠ 3<\/span>/);
});

test("loading, missing and failed sessions keep the clear link", () => {
  for (const [status, text] of [["loading", /Loading session 10216/], ["missing", /No session is stored for transaction 10216/], ["error", /Could not load/]]) {
    const markup = header({ status });
    assert.match(markup, text);
    assert.match(markup, /Show all exchanges/);
  }
});

test("session values are escaped", () => {
  const markup = header({ tx: "<t>", status: "ready", session: { ...session(), charger_id: "<c>", id_tag: "<i>" } });
  assert.ok(!/<[tci]>/.test(markup));
});

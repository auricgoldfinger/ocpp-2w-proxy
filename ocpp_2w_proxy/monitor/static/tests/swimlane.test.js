import assert from "node:assert/strict";
import { test } from "node:test";

import { exchangeRow, gapRow, laneHeader } from "../components/swimlane.js";
import { lanesFor, rewriteChips, rowModel } from "../components/swimlane_model.js";
import { exchangeById, loadFixture } from "./fixture_loader.js";

process.env.TZ = "UTC";

const config = loadFixture("config");
const lanes = lanesFor(config);

function row(idSuffix) {
  const summary = exchangeById(`46116860184273879${idSuffix}`);
  return rowModel(summary, lanes, summary.last_at);
}

const cellOf = (model, lane) => model.cells.find((cell) => cell.lane === lane);

test("lanes are charger, proxy, primary and each secondary", () => {
  assert.deepEqual(
    lanes.map((lane) => `${lane.key}:${lane.role}`),
    ["charger:charger", "proxy:proxy", "primary:primary", "homeassistant:secondary", "stats:secondary"],
  );
});

test("a peer missing from the config gets its own lane", () => {
  const summary = { ...exchangeById("4611686018427387905"), not_sent: ["old-backend"] };
  assert.equal(lanesFor(config, [summary]).at(-1).key, "old-backend");
});

test("a charger message fans out with a path to every reached peer", () => {
  const model = row("05");
  assert.deepEqual(
    model.cells.map((cell) => cell.kind),
    ["origin", "relay", "leg", "leg", "not-sent"],
  );
  assert.deepEqual(model.cells.map((cell) => cell.onPath), [true, true, true, true, false]);
  assert.equal(cellOf(model, "stats").view.label, "not sent");
  assert.deepEqual(cellOf(model, "homeassistant").chips, [{ field: "tx", label: "⇄tx", title: "10216 → 1791822333" }]);
});

test("a blocked command stops at the proxy", () => {
  const model = row("06");
  assert.equal(cellOf(model, "homeassistant").kind, "origin");
  assert.equal(cellOf(model, "charger").view.label, "blocked");
  assert.equal(cellOf(model, "charger").onPath, false);
  assert.equal(cellOf(model, "proxy").onPath, true);
});

test("a withheld leg is off the path while delivered secondaries are on it", () => {
  const model = row("08");
  assert.equal(cellOf(model, "primary").view.label, "withheld");
  assert.equal(cellOf(model, "stats").onPath, true);
});

test("proxy-originated exchanges start from the proxy lane with a replay or synthetic tag", () => {
  assert.equal(cellOf(row("10"), "proxy").kind, "origin");
  assert.equal(cellOf(row("10"), "proxy").tag.label, "replay");
  assert.equal(cellOf(row("11"), "proxy").tag.label, "synthetic");
  assert.equal(cellOf(row("15"), "proxy").tag.label, "debug");
});

test("late and duplicate replies get tags", () => {
  assert.deepEqual(cellOf(row("12"), "charger").tags.map((tag) => tag.label), ["late"]);
  assert.deepEqual(cellOf(row("13"), "charger").tags.map((tag) => tag.label), ["dup"]);
});

test("a removed field shows a dash", () => {
  const stats = exchangeById("4611686018427387909").legs.find((leg) => leg.peer === "stats");
  assert.equal(rewriteChips(stats)[0].title, "10216 → –");
});

test("the fan-out strip lists legs and not-sent peers in lane order", () => {
  assert.deepEqual(
    row("16").strip.map((item) => `${item.peer}${item.icon}`),
    ["primary⧗", "homeassistant✓", "stats⌫"],
  );
  assert.deepEqual(
    row("05").strip.map((item) => `${item.peer}${item.icon}`),
    ["primary✓", "homeassistant✓", "stats—"],
  );
});

test("an exchange row renders every outcome cell with an icon and a word", () => {
  const markup = String(exchangeRow(row("16"), "#/exchange/4611686018427387916"));
  assert.match(markup, /href="#\/exchange\/4611686018427387916"/);
  assert.match(markup, /<span class="badge__icon" aria-hidden="true">⧗<\/span><span class="badge__label">queued<\/span>/);
  assert.match(markup, /badge__label">dropped</);
  assert.match(markup, /tag--problem/);
  assert.match(markup, /row__time">13:08:20</);
});

test("an incomplete exchange is tagged", () => {
  assert.match(String(exchangeRow(row("14"), "#")), /⚠ incomplete/);
});

test("payload text is escaped in rows", () => {
  const summary = { ...exchangeById("4611686018427387905"), headline: "<img src=x onerror=alert(1)>" };
  const markup = String(exchangeRow(rowModel(summary, lanes, summary.last_at), "#"));
  assert.ok(!markup.includes("<img"));
  assert.match(markup, /&lt;img src=x/);
});

test("the lane header labels the primary", () => {
  assert.match(String(laneHeader(lanes)), /lane-head--primary" title="primary"><span class="lane-head__role">PRIMARY<\/span>primary/);
});

test("a gap row says how many events were lost and that relaying was unaffected", () => {
  const gap = loadFixture("links").gaps[0];
  assert.equal(
    String(gapRow(gap)),
    '<li class="row row--gap" role="separator"><span class="gap">⚠ 37 monitor events lost 14:02:02–14:02:04 · relaying unaffected</span></li>',
  );
});
